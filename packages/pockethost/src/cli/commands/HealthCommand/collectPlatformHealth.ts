import { execFile } from 'node:child_process'
import { X509Certificate } from 'node:crypto'
import { existsSync } from 'node:fs'
import { lstat, mkdir, readFile, rename, statfs, writeFile } from 'node:fs/promises'
import https from 'node:https'
import net from 'node:net'
import { cpus, freemem, hostname, loadavg, totalmem } from 'node:os'
import { join } from 'node:path'
import { promisify } from 'node:util'
import type PocketBase from 'pocketbase'
import {
  monitoringReport,
  type MonitorCheck,
  type MonitorGroup,
  type MonitorState,
} from '../../../common/platformMonitoring'

const exec = promisify(execFile)
const ID = /^[a-z0-9]{15}$/
const clean = (value: unknown, max = 160) => `${value || ''}`.replace(/[\r\n\u0000-\u001f]/g, ' ').slice(0, max)
const age = (value: unknown) => Date.now() - Date.parse(`${value || ''}`)
const fresh = (value: unknown, max = 180000) => Number.isFinite(age(value)) && age(value) >= -30000 && age(value) <= max
async function eachBounded<T>(items: T[], task: (item: T) => Promise<void>) {
  let cursor = 0
  await Promise.all(
    Array.from({ length: Math.min(8, items.length) }, async () => {
      while (cursor < items.length) await task(items[cursor++]!)
    })
  )
}

export async function command(file: string, args: string[]) {
  return (await exec(file, args, { timeout: 15000, maxBuffer: 16 * 1024 ** 2 })).stdout
}
export async function readStatus(path: string): Promise<any> {
  const stat = await lstat(path)
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 4 * 1024 ** 2) throw Error('Status unavailable')
  return JSON.parse(await readFile(path, 'utf8'))
}
export async function writeStatus(path: string, value: unknown) {
  await mkdir(join(path, '..'), { recursive: true, mode: 0o750 })
  const temporary = path + '.' + process.pid + '.tmp'
  await writeFile(temporary, JSON.stringify(value), { mode: 0o600 })
  await rename(temporary, path)
}
export async function httpProbe(
  url: string,
  statuses = [200]
): Promise<{ ok: boolean; latencyMs: number; detail: string }> {
  const began = Date.now()
  try {
    const response = await fetch(url, {
      signal: AbortSignal.timeout(4000),
      redirect: 'manual',
      headers: { 'User-Agent': 'Kolab-Monitor/1' },
    })
    await response.body?.cancel()
    return { ok: statuses.includes(response.status), latencyMs: Date.now() - began, detail: `HTTP ${response.status}` }
  } catch {
    return { ok: false, latencyMs: Date.now() - began, detail: 'Connexion impossible ou délai dépassé.' }
  }
}
/** Origin HTTPS with the configured hostname/SNI: independent of DNS/Cloudflare, certificate verification stays enabled. */
export function originProbe(
  domain: string,
  path: string,
  address = '127.0.0.1'
): Promise<{ ok: boolean; detail: string }> {
  return new Promise((resolve) => {
    const request = https.get(
      {
        hostname: address,
        port: 443,
        servername: domain,
        path,
        headers: { Host: domain, 'User-Agent': 'Kolab-Monitor/1' },
        timeout: 4000,
      },
      (response) => {
        response.resume()
        resolve({ ok: response.statusCode === 200, detail: `HTTP ${response.statusCode}` })
      }
    )
    request.on('error', () => resolve({ ok: false, detail: 'Origine HTTPS ou certificat indisponible.' }))
    request.on('timeout', () => request.destroy())
  })
}
export function tcpProbe(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = net.connect({ host: '127.0.0.1', port, timeout: 3000 })
    const finish = (ok: boolean) => {
      socket.destroy()
      resolve(ok)
    }
    socket.on('connect', () => finish(true))
    socket.on('error', () => finish(false))
    socket.on('timeout', () => finish(false))
  })
}
export async function readRows(
  client: PocketBase,
  collection: string,
  fields: string,
  filter = '',
  sort = ''
): Promise<any[]> {
  const rows = []
  for (let page = 1; page <= 25; page++) {
    const result = await client.collection(collection).getList(page, 200, { fields, filter, sort, requestKey: null })
    rows.push(...result.items)
    if (page >= result.totalPages) return rows
  }
  throw Error('Monitoring inventory exceeds its bound')
}

export type CollectOptions = {
  domain: string
  dataRoot: string
  webRoot: string
  mothershipPort: number
  daemonPort: number
  ftpPort: number
  sftpPort: number
  cert: string
  client: PocketBase | null
}
export async function collectPlatformHealth(options: CollectOptions) {
  const checks: MonitorCheck[] = []
  const add = (
    id: string,
    group: MonitorGroup,
    name: string,
    state: MonitorState,
    detail = '',
    href?: string,
    latencyMs?: number
  ) => {
    checks.push({
      id,
      group,
      name: clean(name, 200),
      state,
      detail: clean(detail, 600),
      ...(href ? { href } : {}),
      ...(latencyMs !== undefined ? { latencyMs } : {}),
    })
  }
  const guard = async (id: string, group: MonitorGroup, name: string, task: () => Promise<void>) => {
    try {
      await task()
    } catch {
      add(id, group, name, 'error', 'Contrôle indisponible ; aucun état sain déduit.')
    }
  }
  await guard('peer-configuration', 'infrastructure', 'Surveillance entre serveurs', async () => {
    const peers = JSON.parse(process.env.PLATFORM_MONITOR_PEERS || '[]')
    if (!Array.isArray(peers) || peers.length > 10) throw Error('Invalid peers')
    await Promise.all(
      peers.map(async (peer: any) => {
        if (
          typeof peer.host !== 'string' ||
          !/^[a-z0-9][a-z0-9.-]{1,251}[a-z0-9]$/i.test(peer.host) ||
          !net.isIP(peer.address)
        )
          throw Error('Invalid peer')
        for (const [key, path] of [
          ['firewall', '/api/firewall/health'],
          ['controller', '/api/health'],
        ] as const) {
          const result = await originProbe(peer.host, path, peer.address)
          add(
            'peer:' + peer.host + ':' + key,
            'infrastructure',
            peer.host + ' · ' + (key === 'firewall' ? 'HTTPS' : 'contrôleur'),
            result.ok ? 'ok' : 'error',
            result.detail
          )
        }
      })
    )
  })
  await Promise.all([
    guard('services', 'infrastructure', 'Processus de la plateforme', async () => {
      const processes = JSON.parse(await command('pm2', ['jlist']))
      for (const name of ['firewall', 'edge-daemon', 'edge-ftp', 'edge-sftp', 'mothership', 'dashboard']) {
        const process = processes.find((p: any) => p.name === name)
        add(
          'pm2:' + name,
          'infrastructure',
          name,
          process?.pm2_env?.status === 'online' ? 'ok' : 'error',
          process?.pm2_env?.status || 'Processus absent.'
        )
      }
    }),
    ...[options.dataRoot, '/']
      .filter((path, index, paths) => paths.indexOf(path) === index)
      .map((path, index) =>
        guard('disk:' + index, 'infrastructure', 'Disque ' + path, async () => {
          const stats = await statfs(path),
            free = Number(stats.bavail) * Number(stats.bsize),
            total = Number(stats.blocks) * Number(stats.bsize),
            percent = 100 * (1 - free / total)
          add(
            'disk:' + index,
            'infrastructure',
            'Disque ' + path,
            free < 15 * 1024 ** 3 || percent >= 95 ? 'error' : percent >= 85 ? 'warning' : 'ok',
            `${(free / 1024 ** 3).toFixed(1)} Gio libres · ${percent.toFixed(1)} % utilisés`
          )
        })
      ),
    guard('ram', 'infrastructure', 'Mémoire du serveur', async () => {
      // Linux MemAvailable includes reclaimable cache, unlike os.freemem().
      const text = await readFile('/proc/meminfo', 'utf8'),
        available = Number(text.match(/^MemAvailable:\s+(\d+)/m)?.[1]) * 1024 || freemem(),
        percent = 100 * (1 - available / totalmem())
      add(
        'ram',
        'infrastructure',
        'Mémoire du serveur',
        percent >= 95 ? 'error' : percent >= 90 ? 'warning' : 'ok',
        `${(available / 1024 ** 3).toFixed(1)} Gio disponibles · ${percent.toFixed(1)} % utilisés`
      )
    }),
    guard('tls', 'infrastructure', 'Certificat de la plateforme', async () => {
      const cert = new X509Certificate(await readFile(options.cert)),
        days = (Date.parse(cert.validTo) - Date.now()) / 86400000
      add(
        'tls',
        'infrastructure',
        'Certificat de la plateforme',
        days < 3 ? 'error' : days < 14 ? 'warning' : 'ok',
        `${Math.floor(days)} jours restants`
      )
    }),
    guard('open-files', 'infrastructure', 'Descripteurs disponibles', async () => {
      const [used = 0, , max = 0] = (await readFile('/proc/sys/fs/file-nr', 'utf8')).trim().split(/\s+/).map(Number)
      if (max <= 0) throw Error('Invalid descriptor count')
      add(
        'open-files',
        'infrastructure',
        'Descripteurs disponibles',
        used / max >= 0.95 ? 'error' : used / max >= 0.85 ? 'warning' : 'ok',
        `${used} ouverts / ${max}`
      )
    }),
    ...(
      [
        ['mothership', `http://127.0.0.1:${options.mothershipPort}/api/health`],
        ['daemon', `http://127.0.0.1:${options.daemonPort}/_api/daemon/health`],
        ['dashboard', 'http://127.0.0.1:5174/'],
      ] as const
    ).map(([id, url]) =>
      guard('http:' + id, 'infrastructure', id, async () => {
        const result = await httpProbe(url!)
        add('http:' + id, 'infrastructure', id!, result.ok ? 'ok' : 'error', result.detail, undefined, result.latencyMs)
      })
    ),
    guard('origin', 'infrastructure', 'Pare-feu HTTPS à l’origine', async () => {
      const result = await originProbe(options.domain, '/api/firewall/health')
      add('origin', 'infrastructure', 'Pare-feu HTTPS à l’origine', result.ok ? 'ok' : 'error', result.detail)
    }),
    ...[
      ['FTP', options.ftpPort],
      ['SFTP', options.sftpPort],
    ].map(([name, port]) =>
      guard('tcp:' + name, 'infrastructure', `${name}`, async () =>
        add('tcp:' + name, 'infrastructure', `${name}`, (await tcpProbe(Number(port))) ? 'ok' : 'error', `Port ${port}`)
      )
    ),
    ...['fail2ban', 'ssh'].map((service) =>
      guard('security:' + service, 'security', service, async () => {
        let active = ''
        try {
          active = (await command('systemctl', ['is-active', service])).trim()
        } catch {}
        add(
          'security:' + service,
          'security',
          service,
          active === 'active' ? 'ok' : 'error',
          active || 'Service inactif ou absent.'
        )
      })
    ),
  ])
  const load = (loadavg()[1] || 0) / Math.max(1, cpus().length)
  add(
    'cpu',
    'infrastructure',
    'Charge CPU sur 5 minutes',
    load > 1.5 ? 'warning' : 'ok',
    `${loadavg()[1]!.toFixed(2)} / ${cpus().length} cœurs`
  )
  if (!options.client) {
    add(
      'inventory',
      'infrastructure',
      'Inventaire BDD et hébergement',
      'error',
      'Authentification du contrôleur indisponible.'
    )
    return monitoringReport(hostname(), options.domain, checks)
  }
  const client = options.client
  await guard('database-inventory', 'databases', 'Inventaire des bases', async () => {
    const instances = await readRows(client, 'instances', 'id,subdomain,power,status,suspension')
    let containers: any[] = []
    try {
      const ids = (await command('docker', ['ps', '-aq', '--no-trunc'])).trim().split(/\s+/).filter(Boolean)
      for (let offset = 0; offset < ids.length; offset += 50) {
        try {
          containers.push(...JSON.parse(await command('docker', ['inspect', ...ids.slice(offset, offset + 50)])))
        } catch (error: any) {
          if (error.stdout) containers.push(...JSON.parse(error.stdout))
          else throw error
        }
      }
      add('docker:databases', 'databases', 'Moteur Docker des bases', 'ok', `${instances.length} bases enregistrées`)
    } catch {
      add('docker:databases', 'databases', 'Moteur Docker des bases', 'error', 'Inventaire Docker indisponible.')
      return
    }
    const byName = new Map(containers.map((info) => [info.Name?.replace(/^\//, ''), info]))
    await eachBounded(instances, async (row) => {
      if (!ID.test(row.id)) return
      const id = 'database:' + row.id,
        name = row.subdomain || row.id,
        href = '/instances/' + row.id + '/monitoring'
      if (!row.power || row.suspension) {
        add(id, 'databases', name, 'paused', row.suspension ? 'Base suspendue.' : 'Arrêt volontaire.', href)
        return
      }
      const info = byName.get(row.id)
      if (!info?.State?.Running) {
        add(
          id,
          'databases',
          name,
          ['idle', 'vacuuming', 'starting', 'stopping'].includes(row.status) ? 'paused' : 'error',
          row.status === 'idle'
            ? 'En veille ; aucune requête ne réveille la base.'
            : row.status === 'vacuuming'
              ? 'Maintenance en cours.'
              : 'Aucun conteneur actif.',
          href
        )
        return
      }
      const port = info.NetworkSettings?.Ports?.['8090/tcp']?.find((binding: any) =>
        ['127.0.0.1', '0.0.0.0'].includes(binding.HostIp)
      )?.HostPort
      if (!/^\d{2,5}$/.test(`${port}`)) {
        add(id, 'databases', name, 'error', 'Port local de santé introuvable.', href)
        return
      }
      const result = await httpProbe(`http://127.0.0.1:${port}/api/health`)
      add(id, 'databases', name, result.ok ? 'ok' : 'error', result.detail, href, result.latencyMs)
    })
  })
  const webPresent = process.env.WEB_APPS_ENABLED === 'true' || existsSync(join(options.webRoot, 'worker-status.json'))
  if (webPresent)
    await guard('hosting-inventory', 'hosting', 'Inventaire hébergement', async () => {
      const apps = await readRows(client, 'web_apps', 'id,name,status,activeDeployment,config')
      const profiles = await readRows(client, 'web_runtime_profiles', 'appId,profile')
      let runtime: any = null
      try {
        const worker = await readStatus(join(options.webRoot, 'worker-status.json'))
        add(
          'web-worker',
          'hosting',
          'Worker et moteur rootless',
          fresh(worker.checkedAt) && worker.ready && worker.rootless ? (worker.error ? 'warning' : 'ok') : 'error',
          fresh(worker.checkedAt)
            ? worker.error
              ? 'Le worker signale un problème de ressources.'
              : 'Battement du worker reçu.'
            : 'Battement absent depuis plus de 3 minutes.'
        )
      } catch {
        add('web-worker', 'hosting', 'Worker et moteur rootless', 'error', 'État du worker illisible.')
      }
      try {
        runtime = await readStatus(join(options.webRoot, 'runtime-status.json'))
      } catch {}
      add(
        'web-runtime-metrics',
        'hosting',
        'Mesures des conteneurs web',
        runtime && fresh(runtime.checkedAt) ? 'ok' : 'error',
        runtime && fresh(runtime.checkedAt) ? 'Mesures récentes.' : 'Mesures absentes ou périmées.'
      )
      let routes: any[] = []
      try {
        routes = (await readStatus(join(options.webRoot, 'routes.json'))).routes || []
      } catch {
        add('web-routes', 'hosting', 'Routes publiées', 'error', 'Table de routage illisible.')
      }
      await eachBounded(apps, async (row) => {
        if (!ID.test(row.id)) return
        const id = 'web:' + row.id,
          name = row.name || row.id,
          href = '/applications/' + row.id
        if (!row.activeDeployment || ['stopped', 'idle', 'deleting'].includes(row.status)) {
          add(
            id,
            'hosting',
            name,
            'paused',
            row.status === 'deleting'
              ? 'Suppression en cours.'
              : row.activeDeployment
                ? 'Arrêt volontaire.'
                : 'Aucune version publiée.',
            href
          )
          return
        }
        const actual = runtime?.applications?.[row.id]
        if (!runtime || !fresh(runtime.checkedAt) || !actual || !fresh(actual.checkedAt)) {
          add(id, 'hosting', name, 'unknown', 'Mesures de la publication absentes ou périmées.', href)
          return
        }
        if (!actual.available || actual.state !== 'running') {
          add(id, 'hosting', name, 'error', 'Service publié arrêté ou indisponible.', href)
          return
        }
        const route = routes.find((r) => r.appId === row.id && r.active)
        if (!route) {
          const worker = profiles.find((p) => p.appId === row.id)?.profile?.kind === 'worker'
          add(
            id,
            'hosting',
            name,
            worker ? 'ok' : 'error',
            worker ? 'Worker sans route publique · conteneur actif.' : 'Publication active sans route publique.',
            href
          )
          return
        }
        if (!/^http:\/\/127\.0\.0\.1:[1-9][0-9]{3,4}$/.test(route.target)) {
          add(id, 'hosting', name, 'error', 'Cible locale de routage invalide.', href)
          return
        }
        // Generic HTTP routing probe accepts authentication/not-found responses, but never proxy/server errors.
        const path =
          typeof row.config?.healthPath === 'string' && row.config.healthPath.startsWith('/')
            ? row.config.healthPath
            : '/'
        const response = await httpProbe(
          route.target + path,
          Array.from({ length: 400 }, (_, i) => i + 100)
        )
        add(
          id,
          'hosting',
          name,
          response.ok ? 'ok' : 'error',
          response.detail + ' · ' + actual.services + ' service(s)',
          href,
          response.latencyMs
        )
        if (actual.memoryLimitBytes > 0)
          add(
            'web-memory:' + row.id,
            'hosting',
            name + ' · RAM',
            actual.memoryBytes / actual.memoryLimitBytes >= 0.9 ? 'warning' : 'ok',
            `${Math.round((100 * actual.memoryBytes) / actual.memoryLimitBytes)} % du quota utilisés`,
            href
          )
      })
      const jobs = await readRows(
        client,
        'web_deployments',
        'id,appId,status,stage,created,startedAt,heartbeatAt',
        'status="queued" || status="running"'
      )
      const silent = jobs.some((job) => job.status === 'running' && !fresh(job.heartbeatAt, 120000)),
        old = jobs.some((job) => age(job.status === 'queued' ? job.created : job.startedAt) > 1800000)
      add(
        'web-queue',
        'hosting',
        'File de déploiement',
        silent ? 'error' : old ? 'warning' : 'ok',
        silent
          ? 'Un traitement actif ne transmet plus son battement.'
          : `${jobs.filter((job) => job.status === 'running').length} en cours · ${jobs.filter((job) => job.status === 'queued').length} en attente${old ? ' · attente supérieure à 30 minutes' : ''}`
      )
      const failed = await client.collection('web_deployments').getList(1, 100, {
        fields: 'id,appId,finishedAt',
        filter: `status="failed" && finishedAt>"${new Date(Date.now() - 3600000).toISOString()}"`,
        sort: '-finishedAt',
        requestKey: null,
      })
      add(
        'deployments-failed',
        'hosting',
        'Constructions échouées récemment',
        failed.totalItems ? 'warning' : 'ok',
        `${failed.totalItems} échec(s) dans la dernière heure ; la version publiée est contrôlée séparément.`
      )
      try {
        const storage = await readStatus(join(options.webRoot, 'storage-status.json'))
        for (const [appId, raw] of Object.entries(storage.applications || {})) {
          const row = raw as any
          if (row.volumes?.length) {
            const over = row.volumes.some((v: any) => v.overLimit)
            add(
              'storage:' + appId,
              'hosting',
              'Stockage persistant ' + (apps.find((a) => a.id === appId)?.name || appId),
              !row.available || !fresh(row.checkedAt, 300000) ? 'unknown' : over ? 'error' : 'ok',
              !row.available || !fresh(row.checkedAt, 300000)
                ? 'Mesures de volume indisponibles ou périmées.'
                : over
                  ? 'Un volume dépasse sa limite.'
                  : 'Volumes dans leurs limites.',
              '/applications/' + appId
            )
          }
        }
      } catch {
        add('storage', 'hosting', 'Mesure des volumes', 'unknown', 'Mesures indisponibles.')
      }
      await guard('web-backups', 'backups', 'Sauvegardes web', async () => {
        const policies = await readRows(
          client,
          'web_app_backup_settings',
          'id,appId,enabled,lastError,lastRunAt,nextRunAt',
          'enabled=true'
        )
        for (const row of policies)
          add(
            'web-backup:' + row.appId,
            'backups',
            'Sauvegarde ' + (apps.find((a) => a.id === row.appId)?.name || row.appId),
            row.lastError ? 'error' : age(row.nextRunAt) > 3600000 ? 'warning' : row.lastRunAt ? 'ok' : 'unknown',
            row.lastError
              ? 'Dernière sauvegarde en échec.'
              : age(row.nextRunAt) > 3600000
                ? 'Planification en retard de plus d’une heure.'
                : row.lastRunAt
                  ? 'Dernière exécution : ' + row.lastRunAt
                  : 'En attente de la première exécution.',
            '/applications/' + row.appId
          )
        if (!policies.length)
          add('web-backups', 'backups', 'Sauvegardes web planifiées', 'paused', 'Aucune planification activée.')
      })
    })
  await guard('database-backups', 'backups', 'Sauvegardes des bases', async () => {
    const policies = await readRows(
      client,
      'instance_backup_policies',
      'id,instance,enabled,lastStatus,lastRunAt,lastSuccessAt,lastError',
      'enabled=true'
    )
    for (const row of policies)
      add(
        'database-backup:' + row.instance,
        'backups',
        'Sauvegarde BDD ' + row.instance,
        row.lastStatus === 'failed' || row.lastError
          ? 'error'
          : row.lastStatus === 'running' && age(row.lastRunAt) > 7200000
            ? 'warning'
            : row.lastSuccessAt
              ? 'ok'
              : 'unknown',
        row.lastError
          ? 'Dernière sauvegarde en échec.'
          : row.lastStatus === 'running'
            ? 'Sauvegarde en cours.'
            : row.lastSuccessAt
              ? 'Dernier succès : ' + row.lastSuccessAt
              : 'En attente de la première sauvegarde.',
        '/instances/' + row.instance + '/backups'
      )
    if (!policies.length)
      add('database-backups', 'backups', 'Sauvegardes BDD planifiées', 'paused', 'Aucune planification activée.')
  })
  await guard('litestream', 'backups', 'Réplication Litestream', async () => {
    const replicas = await readRows(
      client,
      'instance_litestream_replicas',
      'id,instance,enabled,status,lastCheckedAt,lastError',
      'enabled=true'
    )
    if (replicas.length) {
      const processes = JSON.parse(await command('pm2', ['jlist'])),
        online = processes.some((p: any) => p.name === 'pockethost-litestream' && p.pm2_env?.status === 'online')
      for (const row of replicas) {
        if (!ID.test(row.instance)) continue
        let local = '',
          error = false
        if (online)
          try {
            const value = JSON.parse(
              await command('litestream', [
                'status',
                '-no-expand-env',
                '-config',
                join(process.env.LITESTREAM_ROOT || join(options.dataRoot, 'litestream'), 'litestream.yml'),
                '-json',
                join(options.dataRoot, 'instances', row.instance, 'pb_data', 'data.db'),
              ])
            )
            local = value[0]?.status || ''
          } catch {
            error = true
          }
        add(
          'litestream:' + row.instance,
          'backups',
          'Litestream ' + row.instance,
          !online || error || !['ok', 'syncing', 'initializing'].includes(local)
            ? 'error'
            : local !== 'ok'
              ? 'warning'
              : 'ok',
          !online
            ? 'Service de réplication arrêté.'
            : error
              ? 'État local indisponible.'
              : 'État local : ' + local + ' · restauration non attestée.',
          '/instances/' + row.instance + '/litestream'
        )
      }
    }
    if (!replicas.length)
      add('litestream', 'backups', 'Réplication Litestream', 'paused', 'Aucune réplication activée.')
  })
  return monitoringReport(
    hostname(),
    options.domain,
    checks.sort((a, b) => a.group.localeCompare(b.group) || a.name.localeCompare(b.name))
  )
}
