import { webRoleAllows } from '../../../../../common/webAccess'
import { webImageDigestValid, webRegistryHostAllowed } from '../../../../../common/webRegistry'
import { requireWebAppPermission } from './access'
import { decryptWebValue, encryptWebValue, webAppsCapability, webInput, webJson } from './api'
const COLLECTION = 'web_runtime_profiles'
const kinds = ['nextjs', 'auto', 'dockerfile', 'image', 'compose', 'worker']
const composeLimits = { cpuMillicores: 4000, memoryMiB: 8192, pids: 512 }
const name = /^[a-z][a-z0-9-]{0,30}$/
const integer = (value: any, min: number, max: number) => {
  if (!Number.isInteger(value) || value < min || value > max) throw new BadRequestError('Quota ou port invalide.')
  return value
}
const relative = (value: any) => {
  if (
    typeof value !== 'string' ||
    !value ||
    value.length > 200 ||
    value.startsWith('/') ||
    value.split('/').includes('..') ||
    !/^[A-Za-z0-9_./-]+$/.test(value)
  )
    throw new BadRequestError('Chemin de construction invalide.')
  return value
}
const command = (value: any) => {
  if (value === undefined) return []
  if (
    !Array.isArray(value) ||
    value.length > 50 ||
    value.some((x) => typeof x !== 'string' || !x || x.length > 4096 || x.includes('\0'))
  )
    throw new BadRequestError('Commande attendue sous forme de tableau.')
  return value
}
// Miroir de text() dans web_app_runtime.py : commande Railpack d’une ligne, transmise à prepare.
const text = (value: any) => {
  if (value === undefined || value === null || value === '') return ''
  if (typeof value !== 'string' || value.length > 1000 || /[\0\r\n]/.test(value))
    throw new BadRequestError('Commande de démarrage ou de construction invalide (une ligne, 1000 caractères maximum).')
  return value
}
const quota = (value: any = {}, compose = false) => {
  if (
    !value ||
    typeof value !== 'object' ||
    Array.isArray(value) ||
    Object.keys(value).some((k) => !['cpuMillicores', 'memoryMiB', 'pids'].includes(k))
  )
    throw new BadRequestError('Quotas invalides.')
  return {
    cpuMillicores: integer(value.cpuMillicores ?? 500, 50, compose ? composeLimits.cpuMillicores : 1000),
    memoryMiB: integer(value.memoryMiB ?? 768, 64, compose ? composeLimits.memoryMiB : 1536),
    pids: integer(value.pids ?? 256, 16, 512),
  }
}
export const validateRuntimeProfile = (input: any = {}) => {
  if (
    !input ||
    typeof input !== 'object' ||
    Array.isArray(input) ||
    Object.keys(input).some(
      (k) =>
        ![
          'kind',
          'port',
          'dockerfile',
          'image',
          'command',
          'entrypoint',
          'compose',
          'resources',
          'buildTarget',
          'startCommand',
          'buildCommand',
          'pruneDependencies',
        ].includes(k)
    )
  )
    throw new BadRequestError('Profil de runtime invalide.')
  const kind = input.kind || 'nextjs'
  if (!kinds.includes(kind)) throw new BadRequestError('Runtime inconnu.')
  const profile: any = {
    kind,
    port: kind === 'worker' ? null : integer(input.port ?? 3000, 1, 65535),
    dockerfile: relative(input.dockerfile || 'Dockerfile'),
    image: ['image', 'worker'].includes(kind) ? input.image || '' : '',
    command: command(input.command),
    entrypoint: command(input.entrypoint),
    resources: quota(input.resources, kind === 'compose'),
  }
  if ((kind === 'image' || kind === 'worker') && profile.image && !webImageDigestValid(profile.image))
    throw new BadRequestError('Image attendue avec @sha256 et un digest complet.')
  if (kind === 'image' && !profile.image) throw new BadRequestError('Image immuable requise.')
  // Railpack : l’image lance « bash -c », seule la commande texte a un sens.
  if (kind === 'auto' && (profile.command.length || profile.entrypoint.length))
    throw new BadRequestError('Pour la construction automatique, utilisez la commande de démarrage (champ texte).')
  if (kind === 'auto' && input.buildTarget)
    throw new BadRequestError('Étape Dockerfile sans objet pour la construction automatique.')
  for (const key of ['startCommand', 'buildCommand']) {
    const value = text(input[key])
    if (value && kind !== 'auto') throw new BadRequestError('Profil de runtime invalide.')
    if (value) profile[key] = value
  }
  // Construction automatique : retirer les dépendances de développement de l’image (RAILPACK_PRUNE_DEPS).
  if (
    input.pruneDependencies !== undefined &&
    input.pruneDependencies !== null &&
    typeof input.pruneDependencies !== 'boolean'
  )
    throw new BadRequestError('Profil de runtime invalide.')
  if (input.pruneDependencies && kind !== 'auto') throw new BadRequestError('Profil de runtime invalide.')
  if (input.pruneDependencies) profile.pruneDependencies = true
  if (input.buildTarget) {
    if (typeof input.buildTarget !== 'string' || !name.test(input.buildTarget))
      throw new BadRequestError('Étape Dockerfile invalide.')
    profile.buildTarget = input.buildTarget
  }
  if (kind === 'compose') {
    const compose = input.compose
    if (
      !compose ||
      typeof compose !== 'object' ||
      Object.keys(compose).some((k) => !['front', 'services'].includes(k)) ||
      !compose.services ||
      typeof compose.services !== 'object' ||
      Array.isArray(compose.services) ||
      Object.keys(compose.services).length < 1 ||
      Object.keys(compose.services).length > 4 ||
      !Object.hasOwn(compose.services, compose.front)
    )
      throw new BadRequestError('Compose exige un frontal et de un à quatre services.')
    const services: any = {}
    const count = Object.keys(compose.services).length
    for (const [key, source] of Object.entries(compose.services)) {
      const item: any = source
      if (
        !name.test(key) ||
        !item ||
        typeof item !== 'object' ||
        Object.keys(item).some(
          (k) =>
            ![
              'image',
              'dockerfile',
              'port',
              'command',
              'entrypoint',
              'environment',
              'depends_on',
              'volumes',
              'resources',
              'healthcheck',
            ].includes(k)
        )
      )
        throw new BadRequestError('Option Compose non autorisée : aucun privilège, bind, port public ou réseau hôte.')
      if (!!item.image === !!item.dockerfile)
        throw new BadRequestError('Chaque service nécessite une image immuable ou un Dockerfile.')
      if (item.image && !webImageDigestValid(item.image)) throw new BadRequestError('Image Compose sans digest.')
      if (item.dockerfile) relative(item.dockerfile)
      const env = item.environment || [],
        depends = item.depends_on || [],
        volumes = item.volumes || []
      if (
        !Array.isArray(env) ||
        env.length > 100 ||
        env.some((v) => typeof v !== 'string' || !/^[A-Za-z_][A-Za-z0-9_]{0,100}$/.test(v))
      )
        throw new BadRequestError('Compose référence uniquement les noms des variables enregistrées.')
      if (
        (!Array.isArray(depends) && (!depends || typeof depends !== 'object')) ||
        Object.keys(depends).length > 4 ||
        (Array.isArray(depends) ? depends : Object.keys(depends)).some(
          (v) => typeof v !== 'string' || v === key || !Object.hasOwn(compose.services, v)
        )
      )
        throw new BadRequestError('Dépendance Compose invalide.')
      if (
        !Array.isArray(depends) &&
        Object.values(depends).some(
          (value: any) =>
            !value ||
            typeof value !== 'object' ||
            Array.isArray(value) ||
            Object.keys(value).length !== 1 ||
            !Object.hasOwn(value, 'condition') ||
            !['service_started', 'service_healthy'].includes(value.condition)
        )
      )
        throw new BadRequestError('Condition de dépendance Compose invalide.')
      if (
        !Array.isArray(volumes) ||
        volumes.length > 4 ||
        volumes.some(
          (v) =>
            typeof v !== 'string' ||
            !/^[a-z][a-z0-9-]{0,30}:\/[A-Za-z0-9_./-]+(?::ro)?$/.test(v) ||
            v.split(':')[1].split('/').includes('..')
        )
      )
        throw new BadRequestError('Montage Compose invalide : volume nommé uniquement.')
      const resources = quota(
        item.resources || {
          cpuMillicores: Math.max(50, Math.floor(profile.resources.cpuMillicores / count)),
          memoryMiB: Math.max(64, Math.floor(profile.resources.memoryMiB / count)),
          pids: Math.max(16, Math.floor(profile.resources.pids / count)),
        },
        true
      )
      let healthcheck: any
      if (item.healthcheck) {
        const check = item.healthcheck,
          test = command(check.test)
        if (
          Object.keys(check).some(
            (k) => !['test', 'timeoutSeconds', 'intervalSeconds', 'retries', 'startPeriodSeconds'].includes(k)
          ) ||
          test[0] !== 'CMD' ||
          test.length < 2
        )
          throw new BadRequestError('Contrôle Compose attendu avec CMD.')
        healthcheck = {
          test,
          timeoutSeconds: integer(check.timeoutSeconds ?? 5, 1, 10),
          intervalSeconds: integer(check.intervalSeconds ?? 10, 5, 60),
          retries: integer(check.retries ?? 3, 1, 10),
          startPeriodSeconds: integer(check.startPeriodSeconds ?? 0, 0, 60),
        }
      }
      services[key] = {
        ...(item.image ? { image: item.image } : { dockerfile: item.dockerfile }),
        port: integer(item.port ?? 3000, 1, 65535),
        command: command(item.command),
        entrypoint: command(item.entrypoint),
        environment: env,
        depends_on: depends,
        volumes,
        resources,
        ...(healthcheck ? { healthcheck } : {}),
      }
    }
    for (const [key, max] of Object.entries(composeLimits))
      if (Object.values(services).reduce((n: number, row: any) => n + row.resources[key], 0) > max)
        throw new BadRequestError('La somme des quotas Compose dépasse celle du site.')
    const ordered: string[] = []
    while (ordered.length < count) {
      const ready = Object.keys(services).filter(
        (key) =>
          !ordered.includes(key) &&
          (Array.isArray(services[key].depends_on)
            ? services[key].depends_on
            : Object.keys(services[key].depends_on)
          ).every((dep: string) => ordered.includes(dep))
      )
      if (!ready.length) throw new BadRequestError('Cycle de dépendances Compose refusé.')
      ordered.push(...ready)
    }
    profile.compose = { front: compose.front, services }
    profile.resources = Object.fromEntries(
      Object.keys(composeLimits).map((key) => [
        key,
        Object.values(services).reduce((total: number, service: any) => total + service.resources[key], 0),
      ])
    )
  }
  return profile
}
const rowOf = (app: core.Record, store: any = $app) =>
  store.findRecordsByFilter(COLLECTION, 'appId={:id}', '', 1, 0, { id: app.id })[0]
export const runtimeProfile = (app: core.Record, store: any = $app) => {
  const row = rowOf(app, store)
  return row ? JSON.parse(row.getString('profile') || '{}') : validateRuntimeProfile()
}
export const runtimeWorkerSnapshot = (app: core.Record, store: any = $app) => {
  const row = rowOf(app, store),
    profile = runtimeProfile(app, store)
  return {
    ...profile,
    ...(row?.getString('registryEncrypted') ? { registry: decryptWebValue(row.getString('registryEncrypted')) } : {}),
  }
}
/** Serveur et compte du registre : app.configure seulement (mêmes champs vides pour les autres rôles). */
const view = (app: core.Record, canConfigure: boolean) => {
  const row = rowOf(app),
    registry =
      canConfigure && row?.getString('registryEncrypted') ? decryptWebValue(row.getString('registryEncrypted')) : {},
    profile = runtimeProfile(app)
  return {
    profile,
    revision: row?.getString('revision') || '',
    registry: { server: registry.server || '', username: registry.username || '', hasPassword: !!registry.password },
    capabilities: {
      kinds,
      maxServices: 4,
      maxCpuMillicores: profile.kind === 'compose' ? composeLimits.cpuMillicores : 1000,
      maxMemoryMiB: profile.kind === 'compose' ? composeLimits.memoryMiB : 1536,
      maxPids: 512,
      hostBindings: false,
      railpackAvailable: webAppsCapability().railpackAvailable,
    },
  }
}
export const HandleWebRuntimeGet = (e: core.RequestEvent) => {
  const { app, role } = requireWebAppPermission(e, 'app.read')
  return webJson(e, view(app!, webRoleAllows(role, 'app.configure')))
}
export const HandleWebRuntimeUpdate = (e: core.RequestEvent) => {
  const app = requireWebAppPermission(e, 'app.configure').app!,
    input = webInput(e),
    profile = validateRuntimeProfile(input.profile)
  $app.runInTransaction((tx) => {
    let row = rowOf(app, tx)
    if (input.revision !== undefined && input.revision !== (row?.getString('revision') || ''))
      throw new BadRequestError('Le profil a changé. Rechargez la page.')
    if (!row) {
      row = new Record(tx.findCollectionByNameOrId(COLLECTION))
      row.set('appId', app.id)
    }
    if (input.registry !== undefined) {
      const previous = row.getString('registryEncrypted') ? decryptWebValue(row.getString('registryEncrypted')) : {},
        value = input.registry
      if (value?.clear === true) row.set('registryEncrypted', '')
      else {
        if (
          !value ||
          !webRegistryHostAllowed(value.server) ||
          typeof value.username !== 'string' ||
          !value.username ||
          value.username.length > 200
        )
          throw new BadRequestError('Registre privé invalide.')
        const password =
          value.password === undefined && previous.server === value.server && previous.username === value.username
            ? previous.password
            : value.password
        if (typeof password !== 'string' || !password || password.length > 10000 || /[\0\r\n]/.test(password))
          throw new BadRequestError('Mot de passe du registre invalide.')
        row.set('registryEncrypted', encryptWebValue({ server: value.server, username: value.username, password }))
      }
    }
    row.set('profile', profile)
    row.set('revision', $security.randomString(24))
    tx.save(row)
  })
  return webJson(e, view(app, true))
}

export const currentWebRuntimeRevision = (app: core.Record, store: any = $app) =>
  rowOf(app, store)?.getString('revision') || ''
export const applyWebRuntimePublishedImage = (
  app: core.Record,
  image: string,
  store: any = $app,
  expectedRevision?: string
) => {
  if (!webImageDigestValid(image)) throw new BadRequestError('Image CI immuable invalide.')
  const row = rowOf(app, store)
  if (!row) return false
  if (expectedRevision !== undefined && row.getString('revision') !== expectedRevision) return false
  const profile = JSON.parse(row.getString('profile') || '{}')
  if (!['image', 'worker'].includes(profile.kind)) return false
  profile.image = image
  row.set('profile', validateRuntimeProfile(profile))
  row.set('revision', $security.randomString(24))
  store.save(row)
  return true
}

export const HandleWebRuntimePreview = (e: core.RequestEvent) => {
  const app = requireWebAppPermission(e, 'app.configure').app!,
    input = webInput(e),
    profile = validateRuntimeProfile(input.profile),
    before = runtimeProfile(app)
  let volumes: any[] = []
  try {
    const row = $app.findRecordsByFilter('web_storage_settings', 'appId={:id}', '', 1, 0, { id: app.id })[0]
    volumes = row ? JSON.parse(row.getString('volumes') || '[]') : []
  } catch {}
  const strategy = volumes.some((item) => !item.readOnly) ? 'recreate' : 'rolling',
    resources =
      profile.kind === 'compose'
        ? Object.values(profile.compose.services).reduce(
            (sum: any, item: any) => ({
              cpuMillicores: sum.cpuMillicores + item.resources.cpuMillicores,
              memoryMiB: sum.memoryMiB + item.resources.memoryMiB,
              pids: sum.pids + item.resources.pids,
            }),
            { cpuMillicores: 0, memoryMiB: 0, pids: 0 }
          )
        : profile.resources
  return webJson(e, {
    profile,
    changes: Object.keys(profile).filter((key) => JSON.stringify(profile[key]) !== JSON.stringify(before[key])),
    requiresDeploy: true,
    strategy,
    resources,
    warnings: [
      ...(strategy === 'recreate'
        ? [
            'Les volumes en écriture imposent une pause des services au démarrage de la nouvelle version. Le code précédent ne restaure pas automatiquement un ancien schéma de données.',
          ]
        : []),
      ...(profile.kind === 'worker' ? ['Ce profil ne publie pas de route HTTP.'] : []),
      ...(profile.kind === 'auto'
        ? [
            'Railpack détecte le langage du dossier du projet et télécharge les outils pendant la construction : la première construction peut être longue.',
            `Le conteneur reçoit PORT=${profile.port} : l’application doit écouter sur cette variable.`,
            'Les variables de construction dont le nom commence par RAILPACK_ sont transmises en clair à l’analyse : n’y placez pas de secret.',
            'Les applications PHP ou qui écrivent sur disque doivent déclarer leurs dossiers inscriptibles dans Stockage.',
            'PHP : Railpack construit sur dunglas/frankenphp et copie composer:latest depuis Docker Hub par tags mobiles, non épinglés par la plateforme.',
            ...(webAppsCapability().railpackAvailable
              ? []
              : ['Railpack n’est pas installé sur le worker : la construction échouera.']),
          ]
        : []),
      'Les images doivent fonctionner sous UID 10001 avec une racine en lecture seule.',
      'La capacité effective est revérifiée avant la construction et le démarrage.',
    ],
  })
}

export const restoreWebRuntimeBackup = (app: core.Record, value: any, store: any = $app) => {
  const input = { ...(value || { kind: 'nextjs' }) },
    registry = input.registry
  delete input.registry
  const profile = validateRuntimeProfile(input)
  let row = rowOf(app, store)
  if (!row) {
    row = new Record(store.findCollectionByNameOrId(COLLECTION))
    row.set('appId', app.id)
  }
  if (registry) {
    if (
      !webRegistryHostAllowed(registry.server) ||
      typeof registry.username !== 'string' ||
      typeof registry.password !== 'string' ||
      !registry.password ||
      /[\0\r\n]/.test(registry.password)
    )
      throw new BadRequestError('Registre de sauvegarde invalide.')
    row.set(
      'registryEncrypted',
      encryptWebValue({ server: registry.server, username: registry.username, password: registry.password })
    )
  } else row.set('registryEncrypted', '')
  row.set('profile', profile)
  row.set('revision', $security.randomString(24))
  store.save(row)
}

export const runtimeNeedsSource = (profile: any) =>
  profile.kind === 'compose'
    ? Object.values(profile.compose?.services || {}).some((service: any) => !!service.dockerfile)
    : profile.kind !== 'image' && !(profile.kind === 'worker' && profile.image)
