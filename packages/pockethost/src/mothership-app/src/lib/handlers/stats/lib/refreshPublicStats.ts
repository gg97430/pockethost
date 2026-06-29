import { mkLog } from '$util/Logger'

export type PublicStats = {
  updatedAt: string
  developers: number
  instances: number
}

export const mkPublicStatsPath = () => `${$app.dataDir()}/stats.json`

const countTableRows = (tableName: string): number => {
  try {
    const result = new DynamicModel({ total: 0 })
    $app.db().newQuery(`SELECT COUNT(*) as total FROM ${tableName}`).one(result)
    return Number(result.total || 0)
  } catch {
    return 0
  }
}

export const refreshPublicStats = () => {
  const log = mkLog('refreshPublicStats')

  const stats: PublicStats = {
    updatedAt: new Date().toISOString(),
    developers: countTableRows('users'),
    instances: countTableRows('instances'),
  }

  $os.writeFile(mkPublicStatsPath(), JSON.stringify(stats), 0o644)
  log(`Wrote stats.json`, stats)
  return stats
}
