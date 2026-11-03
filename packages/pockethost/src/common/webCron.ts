/** Strict five-field cron. Only the supported fixed-offset zones are accepted. */
export type WebCronTimezone = 'UTC' | 'Indian/Reunion'
export const webCronOffset = (timezone: unknown = 'UTC') => {
  if (timezone === 'UTC') return 0
  if (timezone === 'Indian/Reunion') return 4 * 3600000
  throw new Error('Choisissez UTC ou La Réunion (UTC+4).')
}
export type WebCron = { expression: string; fields: Set<number>[]; anyDay: boolean; anyWeekday: boolean }
export const parseWebCron = (expression: unknown): WebCron => {
  if (typeof expression !== 'string' || expression.length > 100) throw new Error('Expression cron invalide.')
  const parts = expression.trim().split(/\s+/)
  if (parts.length !== 5) throw new Error('Utilisez cinq champs : minute heure jour mois jour-de-semaine.')
  const ranges: [number, number][] = [
    [0, 59],
    [0, 23],
    [1, 31],
    [1, 12],
    [0, 7],
  ]
  const fields = parts.map((part, index) => {
    const [min, max] = ranges[index]!
    const values = new Set<number>()
    for (const item of part.split(',')) {
      if (!/^(?:\*|[0-9]{1,2}(?:-[0-9]{1,2})?)(?:\/[0-9]{1,2})?$/.test(item))
        throw new Error('Seuls *, nombres, listes, plages et pas sont acceptés.')
      const [span, stepText] = item.split('/')
      const step = stepText ? Number(stepText) : 1
      if (step < 1 || step > max - min + 1 || (stepText && span !== '*' && !span!.includes('-')))
        throw new Error('Pas cron invalide : utilisez */n ou début-fin/n.')
      const [low, high] =
        span === '*' ? [min, max] : span!.includes('-') ? span!.split('-').map(Number) : [Number(span), Number(span)]
      if (low! < min || high! > max || low! > high!) throw new Error('Valeur cron hors limites.')
      for (let value = low!; value <= high!; value += step) values.add(index === 4 && value === 7 ? 0 : value)
    }
    return values
  })
  return { expression: parts.join(' '), fields, anyDay: parts[2] === '*', anyWeekday: parts[4] === '*' }
}
const dayMatches = (cron: WebCron, date: Date) => {
  const day = cron.fields[2]!.has(date.getUTCDate()),
    weekday = cron.fields[4]!.has(date.getUTCDay())
  return cron.anyDay ? weekday : cron.anyWeekday ? day : day || weekday
}
export const nextWebCron = (cron: WebCron, after: number, timezone: WebCronTimezone = 'UTC'): string | null => {
  const offset = webCronOffset(timezone)
  const date = new Date(Math.floor((after + offset) / 60000) * 60000 + 60000)
  const limit = date.getUTCFullYear() + 5
  while (date.getUTCFullYear() <= limit) {
    if (!cron.fields[3]!.has(date.getUTCMonth() + 1)) {
      date.setUTCMonth(date.getUTCMonth() + 1, 1)
      date.setUTCHours(0, 0, 0, 0)
      continue
    }
    if (!dayMatches(cron, date)) {
      date.setUTCDate(date.getUTCDate() + 1)
      date.setUTCHours(0, 0, 0, 0)
      continue
    }
    if (!cron.fields[1]!.has(date.getUTCHours())) {
      date.setUTCHours(date.getUTCHours() + 1, 0, 0, 0)
      continue
    }
    if (!cron.fields[0]!.has(date.getUTCMinutes())) {
      date.setUTCMinutes(date.getUTCMinutes() + 1, 0, 0)
      continue
    }
    return new Date(date.getTime() - offset).toISOString()
  }
  return null
}
