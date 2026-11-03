export type ConnectionLimits = { total: number; perApp: number; perBinding: number; perDatabase: number }
export const DEFAULT_CONNECTION_LIMITS: ConnectionLimits = {
  total: 512,
  perApp: 128,
  perBinding: 128,
  perDatabase: 128,
}
const NAME = 'private_db_connection_limits'

export const validateConnectionLimits = (value: any): ConnectionLimits => {
  if (
    !value ||
    typeof value !== 'object' ||
    Array.isArray(value) ||
    Object.keys(value).sort().join(',') !== 'perApp,perBinding,perDatabase,total'
  )
    throw new BadRequestError('Les quatre plafonds de connexion sont obligatoires.')
  for (const [key, maximum] of [
    ['total', 1024],
    ['perApp', 256],
    ['perBinding', 256],
    ['perDatabase', 512],
  ] as const)
    if (!Number.isSafeInteger(value[key]) || value[key] < 1 || value[key] > maximum)
      throw new BadRequestError(`Le plafond ${key} doit être un entier de 1 à ${maximum}.`)
  if (
    value.perBinding > value.perApp ||
    value.perApp > value.total ||
    value.perBinding > value.perDatabase ||
    value.perDatabase > value.total
  )
    throw new BadRequestError('Une liaison ne peut dépasser son application ou sa base, ni le plafond total.')
  return { total: value.total, perApp: value.perApp, perBinding: value.perBinding, perDatabase: value.perDatabase }
}
export const readConnectionLimits = (app = $app): ConnectionLimits => {
  const rows = app.findRecordsByFilter('settings', 'name = {:name}', '', 2, 0, { name: NAME })
  if (!rows.length) return { ...DEFAULT_CONNECTION_LIMITS }
  if (rows.length !== 1) throw new InternalServerError('Configuration de connexion ambiguë.')
  return validateConnectionLimits(JSON.parse(rows[0]!.getString('value')))
}
export const connectionLimitsRevision = (limits: ConnectionLimits) => $security.sha256(JSON.stringify(limits))
export const writeConnectionLimits = (value: ConnectionLimits, revision: string) => {
  $app.runInTransaction((app) => {
    if (connectionLimitsRevision(readConnectionLimits(app)) !== revision)
      throw new ApiError(409, 'Ces plafonds ont été modifiés. Rechargez avant d’enregistrer.')
    const rows = app.findRecordsByFilter('settings', 'name = {:name}', '', 1, 0, { name: NAME })
    const row = rows[0] || new Record(app.findCollectionByNameOrId('settings'))
    row.set('name', NAME)
    row.set('value', JSON.stringify(value))
    app.save(row)
  })
}
