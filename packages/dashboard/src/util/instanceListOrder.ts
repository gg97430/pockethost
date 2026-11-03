import type { InstanceFields } from 'pockethost/common'
import type { DashboardInstanceMetric } from '../pocketbase-client/PocketbaseClient'
import type { InstanceListSortBy, InstanceListSortDirection } from './instanceListPrefs'

const textOrder = (a: string, b: string) => a.localeCompare(b, 'fr', { sensitivity: 'base', numeric: true })
const statusLabel = (instance: InstanceFields) =>
  (
    ({
      running: 'En ligne',
      idle: 'Éteinte',
      starting: 'Démarrage',
      porting: 'Démarrage',
      failed: 'Erreur',
      vacuuming: 'Nettoyage',
    }) as Record<string, string>
  )[instance.status] || 'Inconnu'

export const compareInstanceList = (
  a: InstanceFields,
  b: InstanceFields,
  by: InstanceListSortBy,
  direction: InstanceListSortDirection,
  metrics: Record<string, DashboardInstanceMetric>
) => {
  const value = (instance: InstanceFields): number | string | null => {
    const metric = metrics[instance.id]
    switch (by) {
      case 'favorites':
      case 'name':
        return instance.cname || instance.subdomain
      case 'label':
        return instance.projectLabel || null
      case 'status':
        return statusLabel(instance)
      case 'version':
        return instance.version || null
      case 'power':
        return Number(instance.power)
      case 'cpu':
        return metric?.cpuPercent ?? null
      case 'memory':
        return metric?.memoryBytes ?? null
      case 'disk':
        return metric?.diskBytes ?? null
    }
  }
  const av = value(a),
    bv = value(b)
  const missing = (v: typeof av) => v === null || (typeof v === 'number' && !Number.isFinite(v))
  // Missing telemetry stays at the bottom in both directions, instead of appearing as zero usage.
  if (missing(av) !== missing(bv)) return missing(av) ? 1 : -1
  const order = missing(av)
    ? 0
    : typeof av === 'number' && typeof bv === 'number'
      ? av - bv
      : textOrder(String(av), String(bv))
  return order * (direction === 'asc' ? 1 : -1) || textOrder(a.subdomain, b.subdomain) || a.id.localeCompare(b.id)
}
