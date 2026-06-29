import { mkLog } from '$util/Logger'

const COPY_DIRS = ['pb_data', 'pb_migrations', 'pb_public', 'pb_hooks']

const dataRoot = () => {
  const envRoot = $os.getenv('DATA_ROOT')
  if (envRoot) return envRoot

  const appDataDir = `${$app.dataDir()}`
  const inferred = appDataDir.replace(/\/mothership\/pb_data\/?$/, '')
  if (inferred !== appDataDir) return inferred

  throw new Error("Impossible de trouver le dossier de donnees des instances.")
}

const assertSafeInstanceId = (id: string) => {
  if (!id.match(/^[a-z0-9]+$/)) {
    throw new BadRequestError("Identifiant d'instance invalide.")
  }
}

const instanceRoot = (id: string) => `${dataRoot()}/instances/${id}`

const pathExists = (path: string) => {
  try {
    $os.stat(path)
    return true
  } catch {
    return false
  }
}

const copyDirectory = (source: string, target: string) => {
  $os.mkdirAll(target, 0o755)
  $os.cmd('cp', '-a', `${source}/.`, target).combinedOutput()
}

const copyInstanceFiles = (sourceId: string, targetId: string) => {
  assertSafeInstanceId(sourceId)
  assertSafeInstanceId(targetId)

  const sourceRoot = instanceRoot(sourceId)
  const targetRoot = instanceRoot(targetId)
  $os.mkdirAll(targetRoot, 0o755)

  for (const dir of COPY_DIRS) {
    const source = `${sourceRoot}/${dir}`
    const target = `${targetRoot}/${dir}`
    $os.removeAll(target)

    if (pathExists(source)) {
      copyDirectory(source, target)
    } else {
      $os.mkdirAll(target, 0o755)
    }
  }
}

const normalizeBaseSubdomain = (subdomain: string) => {
  const clean = subdomain
    .toLowerCase()
    .replace(/[^a-z0-9-]/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-+|-+$/g, '')

  const base = clean.match(/^[a-z]/) ? clean : `base-${clean}`
  return base.slice(0, 34).replace(/-+$/g, '') || 'base'
}

const subdomainExists = (subdomain: string) => {
  try {
    $app.findFirstRecordByData('instances', 'subdomain', subdomain)
    return true
  } catch {
    return false
  }
}

const suggestDuplicateSubdomain = (sourceSubdomain: string) => {
  const base = normalizeBaseSubdomain(sourceSubdomain)
  const fixed = `${base.slice(0, 34).replace(/-+$/g, '')}-copy`
  if (fixed.match(/^[a-z][a-z0-9-]{2,39}$/) && !subdomainExists(fixed)) return fixed

  for (let i = 0; i < 25; i++) {
    const suffix = $security.randomStringWithAlphabet(5 + Math.min(i, 4), 'abcdefghijklmnopqrstuvwxyz0123456789')
    const candidate = `${base.slice(0, 39 - suffix.length).replace(/-+$/g, '')}-${suffix}`
    if (candidate.match(/^[a-z][a-z0-9-]{2,39}$/) && !subdomainExists(candidate)) return candidate
  }

  throw new BadRequestError("Impossible de generer un nom d'instance disponible.")
}

export const HandleInstanceDuplicate = (e: core.RequestEvent) => {
  const log = mkLog(`POST:instance:duplicate`)
  const authRecord = e.auth
  if (!authRecord) throw new BadRequestError(`Session utilisateur attendue`)

  const sourceId = e.request.pathValue('id')
  assertSafeInstanceId(sourceId)

  const source = $app.findRecordById('instances', sourceId)
  if (!source) throw new BadRequestError(`Instance ${sourceId} introuvable.`)

  if (source.get('uid') !== authRecord.id && !authRecord.getBool('superAdmin')) {
    throw new BadRequestError(`Non autorise`)
  }

  if (source.getBool('power') || source.getString('status').toLowerCase() !== 'idle') {
    throw new BadRequestError("Eteignez l'instance avant de dupliquer sa base.")
  }

  const collection = $app.findCollectionByNameOrId('instances')
  const target = new Record(collection)
  const targetSubdomain = suggestDuplicateSubdomain(source.getString('subdomain'))

  target.set('uid', authRecord.id)
  target.set('subdomain', targetSubdomain)
  target.set('status', 'idle')
  target.set('power', false)
  target.set('version', source.getString('version'))
  target.set('dev', source.getBool('dev'))
  target.set('syncAdmin', source.getBool('syncAdmin'))
  target.set('autoVacuum', source.getBool('autoVacuum'))
  target.set('secrets', source.get('secrets'))
  target.set('webhooks', source.get('webhooks'))

  try {
    $app.save(target)

    // The create hook applies operator defaults. Save once more so the duplicate preserves the source options.
    target.set('dev', source.getBool('dev'))
    target.set('autoVacuum', source.getBool('autoVacuum'))
    $app.save(target)

    copyInstanceFiles(source.id, target.id)
    log(`duplicated ${source.id} to ${target.id}`)
  } catch (error) {
    try {
      if (target.id) $app.delete(target)
    } catch {}
    try {
      if (target.id) $os.removeAll(instanceRoot(target.id))
    } catch {}
    throw new ApiError(500, `Impossible de dupliquer la base.`, { error })
  }

  return e.json(200, { instance: target })
}
