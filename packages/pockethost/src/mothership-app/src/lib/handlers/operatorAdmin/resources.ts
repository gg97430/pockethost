import {
  emptyPlatformResources,
  parsePlatformResources,
  PLATFORM_RESOURCE_RANGES,
  type PlatformResourceRange,
  type PlatformResourceResponse,
  type PlatformResourceRow,
} from '$common/platformResources'
import { requireOperatorAdmin } from './auth'

const recordId = /^[a-z0-9]{15}$/
const labels = {
  databases: 'Base de données',
  hosting: 'Hébergement',
  builds: 'BuildKit',
  platform: 'Service de la plateforme',
  backups: 'Service de sauvegarde',
  system: 'Système et autres processus',
}
const serviceLabels: Record<string, string> = {
  'platform:stack': 'Console et services Kolab',
  'platform:worker': 'Worker d’hébergement',
  'platform:terminal': 'Terminal web',
  'platform:bridge': 'Relais privé des bases',
  'platform:rootful': 'Docker des bases',
  'platform:containerd': 'Runtime des conteneurs',
  'platform:rootless': 'Docker des hébergements',
  'platform:collector': 'Collecteur de supervision',
  'backups:recovery': 'Sauvegardes de la plateforme',
}

/** IDs and measurements come from the collector; human labels come from this authenticated controller. */
const enrichResources = (response: PlatformResourceResponse) => {
  if (!response.sample) return response
  const rows = response.sample.resources
  for (const row of rows) row.name = serviceLabels[row.id] || labels[row.category]
  for (const definition of [
    { collection: 'instances', kind: 'database', category: 'databases', prefix: '/instances/' },
    { collection: 'web_apps', kind: 'application', category: 'hosting', prefix: '/applications/' },
  ] as const) {
    let records: core.Record[] = []
    try {
      records = $app.findRecordsByFilter(definition.collection, '', 'id', 4096, 0)
    } catch {}
    const existing = new Map(rows.filter((row) => row.kind === definition.kind).map((row) => [row.id, row]))
    for (const record of records) {
      if (!recordId.test(record.id)) continue
      let row = existing.get(record.id)
      if (!row && rows.length < 4096) {
        const state = record.getString('status')
        row = {
          id: record.id,
          category: definition.category,
          kind: definition.kind,
          state: ['stopped', 'off', 'sleeping'].includes(state) ? 'stopped' : 'unknown',
          cpuPercent: null,
          memoryBytes: null,
          diskBytes: null,
          memoryLimitBytes: null,
        } as PlatformResourceRow
        rows.push(row)
      }
      if (row) {
        row.name = (record.getString('name') || record.getString('subdomain') || labels[definition.category]).slice(
          0,
          200
        )
        row.href = definition.prefix + record.id
      }
    }
  }
  for (const category of response.sample.categories)
    category.resourceCount = rows.filter((row) => row.category === category.id).length
  return response
}

/** GET is a bounded file read: it never samples Docker, scans disk, runs a command or changes state. */
export const HandleOperatorResourceMetrics = (e: core.RequestEvent) => {
  requireOperatorAdmin(e)
  e.response.header().set('Cache-Control', 'no-store')
  const selected = e.request!.url!.query().get('range') || '1h'
  if (!PLATFORM_RESOURCE_RANGES.includes(selected as PlatformResourceRange))
    throw new BadRequestError('Choisissez une période de 15 min, 1 h, 24 h ou 7 jours.')
  const range = selected as PlatformResourceRange
  try {
    const root = $os.getenv('PLATFORM_RESOURCES_ROOT') || '/var/lib/kolab-resource-monitoring'
    const content = toString($os.readFile(`${root}/resources-${range}.json`))
    if (content.length > 4 * 1024 * 1024) throw Error('Resource cache too large')
    return e.json(200, enrichResources(parsePlatformResources(JSON.parse(content), range)))
  } catch {
    return e.json(200, emptyPlatformResources(range))
  }
}
