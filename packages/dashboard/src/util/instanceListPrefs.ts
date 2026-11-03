export type InstanceListSortDirection = 'asc' | 'desc'
export type InstanceListSortBy =
  | 'favorites'
  | 'name'
  | 'label'
  | 'status'
  | 'cpu'
  | 'memory'
  | 'disk'
  | 'version'
  | 'power'
export type InstanceListViewMode = 'list' | 'grid'

export type InstanceListPrefs = {
  sortBy: InstanceListSortBy
  groupFilter: string
  searchQuery: string
  sortDirection: InstanceListSortDirection
  viewMode: InstanceListViewMode
}

export const DEFAULT_INSTANCE_LIST_PREFS: InstanceListPrefs = {
  sortBy: 'favorites',
  groupFilter: 'all',
  searchQuery: '',
  sortDirection: 'asc',
  viewMode: 'list',
}

const STORAGE_KEY = 'ph.dashboard.instanceList'

const DEFAULTS = DEFAULT_INSTANCE_LIST_PREFS

const SORT_DIRECTIONS = new Set<InstanceListSortDirection>(['asc', 'desc'])
const VIEW_MODES = new Set<InstanceListViewMode>(['list', 'grid'])

export const INSTANCE_SORT_OPTIONS: { value: InstanceListSortBy; label: string }[] = [
  { value: 'favorites', label: 'Favoris puis nom' },
  { value: 'name', label: 'Nom' },
  { value: 'label', label: 'Label' },
  { value: 'status', label: 'État' },
  { value: 'cpu', label: 'CPU' },
  { value: 'memory', label: 'RAM' },
  { value: 'disk', label: 'Disque' },
  { value: 'version', label: 'Version' },
  { value: 'power', label: 'Alimentation' },
]
const SORT_KEYS = new Set(INSTANCE_SORT_OPTIONS.map((x) => x.value))
const validGroup = (value: unknown): value is string =>
  typeof value === 'string' &&
  (value === 'all' || value === 'ungrouped' || (value.startsWith('label:') && value.length <= 126))

const URL_KEYS = ['sort', 'q', 'view', 'by', 'group'] as const

export const instanceListPrefsHasUrlParams = (searchParams: URLSearchParams) =>
  URL_KEYS.some((key) => searchParams.has(key))

const parseStored = (raw: string | null): Partial<InstanceListPrefs> | null => {
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as Partial<InstanceListPrefs>
    return typeof parsed === 'object' && parsed !== null ? parsed : null
  } catch {
    return null
  }
}

const sanitize = (stored: Partial<InstanceListPrefs>): InstanceListPrefs => ({
  sortBy: stored.sortBy && SORT_KEYS.has(stored.sortBy) ? stored.sortBy : DEFAULTS.sortBy,
  groupFilter: validGroup(stored.groupFilter) ? stored.groupFilter : DEFAULTS.groupFilter,
  searchQuery: typeof stored.searchQuery === 'string' ? stored.searchQuery : DEFAULTS.searchQuery,
  sortDirection:
    stored.sortDirection && SORT_DIRECTIONS.has(stored.sortDirection) ? stored.sortDirection : DEFAULTS.sortDirection,
  viewMode: stored.viewMode && VIEW_MODES.has(stored.viewMode) ? stored.viewMode : DEFAULTS.viewMode,
})

export const loadInstanceListPrefsFromUrl = (searchParams: URLSearchParams): InstanceListPrefs => {
  const fromUrl: Partial<InstanceListPrefs> = {}
  const by = searchParams.get('by') as InstanceListSortBy
  if (SORT_KEYS.has(by)) fromUrl.sortBy = by
  const group = searchParams.get('group')
  if (validGroup(group)) fromUrl.groupFilter = group
  const sort = searchParams.get('sort')
  const q = searchParams.get('q')
  const view = searchParams.get('view')

  if (sort && SORT_DIRECTIONS.has(sort as InstanceListSortDirection)) {
    fromUrl.sortDirection = sort as InstanceListSortDirection
  }
  if (q !== null) {
    fromUrl.searchQuery = q
  }
  if (view && VIEW_MODES.has(view as InstanceListViewMode)) {
    fromUrl.viewMode = view as InstanceListViewMode
  }

  return { ...DEFAULTS, ...fromUrl }
}

export const loadInstanceListPrefsFromStorage = (): InstanceListPrefs => {
  if (typeof localStorage === 'undefined') return DEFAULTS
  return sanitize(parseStored(localStorage.getItem(STORAGE_KEY)) ?? {})
}

export const saveInstanceListPrefs = (prefs: InstanceListPrefs) => {
  if (typeof localStorage === 'undefined') return
  localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs))
}

const FAVORITES_STORAGE_KEY = 'ph.dashboard.instanceFavorites'

const sanitizeFavoriteIds = (raw: unknown): string[] => {
  if (!Array.isArray(raw)) return []
  return raw.filter((id): id is string => typeof id === 'string')
}

export const loadInstanceFavoritesFromStorage = (): string[] => {
  if (typeof localStorage === 'undefined') return []
  try {
    return sanitizeFavoriteIds(JSON.parse(localStorage.getItem(FAVORITES_STORAGE_KEY) ?? '[]'))
  } catch {
    return []
  }
}

export const saveInstanceFavorites = (ids: string[]) => {
  if (typeof localStorage === 'undefined') return
  localStorage.setItem(FAVORITES_STORAGE_KEY, JSON.stringify(ids))
}

export const toggleInstanceFavorite = (ids: string[], id: string): string[] =>
  ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]
