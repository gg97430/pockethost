/** Goja-safe pagination over a fixed, sanitized analysis snapshot. */
export const paginatePerformance = (report: any, slowPage: number, groupPage: number, sort: string, perPage = 50) => {
  const groups = [...report.groups].sort(
    (a, b) => b[sort] - a[sort] || `${a.method} ${a.endpoint}`.localeCompare(`${b.method} ${b.endpoint}`)
  )
  const page = (items: any[], requested: number) => {
    const totalPages = Math.max(1, Math.ceil(items.length / perPage))
    const current = Math.min(totalPages, Math.max(1, requested))
    return {
      page: current,
      perPage,
      totalItems: items.length,
      totalPages,
      items: items.slice((current - 1) * perPage, current * perPage),
    }
  }
  const slow = page(report.slowest, slowPage)
  const routes = page(groups, groupPage)
  return {
    ...report,
    groups: routes.items,
    slowest: slow.items,
    slowPagination: { page: slow.page, perPage, totalItems: slow.totalItems, totalPages: slow.totalPages },
    groupPagination: { page: routes.page, perPage, totalItems: routes.totalItems, totalPages: routes.totalPages },
  }
}
