export type NaturalCronResult =
  | {
      ok: true
      cron: string
      localDescription: string
      serverDescription: string
      timezoneLabel: string
    }
  | {
      ok: false
      message: string
      timezoneLabel: string
    }

type NaturalCronOptions = {
  timezoneOffsetMinutes?: number
  timezoneLabel?: string
}

type ParsedTime = {
  hour: number
  minute: number
  usedDefault: boolean
}

const DEFAULT_BACKUP_HOUR = 2
const DEFAULT_BACKUP_MINUTE = 0

const WEEK_DAYS = [
  { cron: 0, name: 'dimanche', aliases: ['dimanche', 'dim'] },
  { cron: 1, name: 'lundi', aliases: ['lundi', 'lun'] },
  { cron: 2, name: 'mardi', aliases: ['mardi', 'mar'] },
  { cron: 3, name: 'mercredi', aliases: ['mercredi', 'mer'] },
  { cron: 4, name: 'jeudi', aliases: ['jeudi', 'jeu'] },
  { cron: 5, name: 'vendredi', aliases: ['vendredi', 'ven'] },
  { cron: 6, name: 'samedi', aliases: ['samedi', 'sam'] },
]

export const NATURAL_CRON_EXAMPLES = [
  'tous les jours à 2h',
  'chaque lundi à 3h30',
  'du lundi au vendredi à 22h',
  'toutes les 6 heures',
  'le 1 du mois à 2h',
]

function normalize(input: string) {
  return input
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[’']/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function currentTimezoneLabel() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'heure locale'
  } catch {
    return 'heure locale'
  }
}

function formatTime(hour: number, minute: number) {
  return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`
}

function rangeLabel(days: number[]) {
  if (days.length === 7) return 'tous les jours'
  if (days.join(',') === '1,2,3,4,5') return 'du lundi au vendredi'
  if (days.join(',') === '0,6') return 'le week-end'
  return days
    .map((day) => WEEK_DAYS.find((entry) => entry.cron === day)?.name || `${day}`)
    .join(', ')
}

function parseTime(text: string): ParsedTime | null {
  if (/\bminuit\b/.test(text)) return { hour: 0, minute: 0, usedDefault: false }
  if (/\bmidi\b/.test(text)) return { hour: 12, minute: 0, usedDefault: false }

  const explicit =
    text.match(/(?:^|\s)(?:a|vers|@)\s*(\d{1,2})(?:\s*(?:h|heure|heures|:)\s*(\d{1,2}))?\b/) ||
    text.match(/\b(\d{1,2})\s*h\s*(\d{1,2})?\b/)

  if (!explicit) {
    return {
      hour: DEFAULT_BACKUP_HOUR,
      minute: DEFAULT_BACKUP_MINUTE,
      usedDefault: true,
    }
  }

  const hour = Number(explicit[1])
  const minute = explicit[2] === undefined ? 0 : Number(explicit[2])
  if (!Number.isInteger(hour) || !Number.isInteger(minute) || hour < 0 || hour > 23 || minute < 0 || minute > 59) {
    return null
  }

  return { hour, minute, usedDefault: false }
}

function localToUtc(hour: number, minute: number, timezoneOffsetMinutes: number) {
  const total = hour * 60 + minute + timezoneOffsetMinutes
  const dayOffset = Math.floor(total / 1440)
  const normalized = ((total % 1440) + 1440) % 1440
  return {
    hour: Math.floor(normalized / 60),
    minute: normalized % 60,
    dayOffset,
  }
}

function shiftWeekDay(day: number, offset: number) {
  return ((day + offset) % 7 + 7) % 7
}

function shiftWeekDays(days: number[], offset: number) {
  return [...new Set(days.map((day) => shiftWeekDay(day, offset)))].sort((a, b) => a - b)
}

function cronDays(days: number[]) {
  if (days.length === 7) return '*'
  return days.join(',')
}

function findNamedWeekDays(text: string) {
  const matches = WEEK_DAYS.filter((day) => {
    return day.aliases.some((alias) => new RegExp(`\\b${alias}\\b`).test(text))
  }).map((day) => day.cron)

  return [...new Set(matches)].sort((a, b) => a - b)
}

function parseHourInterval(text: string) {
  const match = text.match(/\b(?:tous|toutes|chaque)\s+(?:les?\s+)?(\d{1,2})\s*(?:h|heure|heures)\b/)
  if (!match) return null

  const hours = Number(match[1])
  if (!Number.isInteger(hours) || hours < 1 || hours > 23) return null
  return hours
}

function parseMonthlyDay(text: string) {
  if (/\b(?:le\s+)?(?:1er|premier)\s+(?:du|de chaque)\s+mois\b/.test(text)) return 1
  if (/\bchaque\s+mois\s+(?:le\s+)?(?:1er|premier)\b/.test(text)) return 1

  const explicit =
    text.match(/\b(?:le\s+)?(\d{1,2})(?:er|e|eme)?\s+(?:du|de chaque)\s+mois\b/) ||
    text.match(/\bchaque\s+mois\s+(?:le\s+)?(\d{1,2})(?:er|e|eme)?\b/)

  if (!explicit) return null
  const day = Number(explicit[1])
  if (!Number.isInteger(day) || day < 1 || day > 31) return null
  return day
}

function timezoneOffset(options: NaturalCronOptions) {
  return options.timezoneOffsetMinutes ?? new Date().getTimezoneOffset()
}

function timezoneLabel(options: NaturalCronOptions) {
  return options.timezoneLabel || currentTimezoneLabel()
}

export function parseNaturalCron(input: string, options: NaturalCronOptions = {}): NaturalCronResult {
  const text = normalize(input)
  const label = timezoneLabel(options)

  if (!text) {
    return {
      ok: false,
      message: 'Indiquez une fréquence, par exemple "tous les jours à 2h".',
      timezoneLabel: label,
    }
  }

  if (/\b(demain|aujourd hui|ce soir|date precise|une fois)\b/.test(text)) {
    return {
      ok: false,
      message: 'La sauvegarde automatique est récurrente. Utilisez plutôt "tous les jours à 2h" ou "chaque lundi à 3h".',
      timezoneLabel: label,
    }
  }

  const intervalHours = parseHourInterval(text)
  if (intervalHours) {
    return {
      ok: true,
      cron: intervalHours === 1 ? '0 * * * *' : `0 */${intervalHours} * * *`,
      localDescription:
        intervalHours === 1 ? 'Toutes les heures' : `Toutes les ${intervalHours} heures, à la minute 0`,
      serverDescription: intervalHours === 1 ? '0 * * * *' : `0 */${intervalHours} * * *`,
      timezoneLabel: label,
    }
  }

  const time = parseTime(text)
  if (!time) {
    return {
      ok: false,
      message: 'Heure invalide. Utilisez par exemple "2h", "02:30", "midi" ou "minuit".',
      timezoneLabel: label,
    }
  }

  const utc = localToUtc(time.hour, time.minute, timezoneOffset(options))
  const defaultTimeNote = time.usedDefault ? ' à 02:00 par défaut' : ` à ${formatTime(time.hour, time.minute)}`
  const monthlyDay = parseMonthlyDay(text)

  if (monthlyDay !== null) {
    let serverDay: number | 'L' = monthlyDay + utc.dayOffset
    if (serverDay < 1) serverDay = 'L'

    if (serverDay === 32) {
      return {
        ok: false,
        message: 'Cette heure décale la planification au mois suivant. Choisissez une heure plus tôt ou utilisez un cron personnalisé.',
        timezoneLabel: label,
      }
    }

    return {
      ok: true,
      cron: `${utc.minute} ${utc.hour} ${serverDay} * *`,
      localDescription: `Chaque mois le ${monthlyDay}${monthlyDay === 1 ? 'er' : ''}${defaultTimeNote} (${label})`,
      serverDescription: `Serveur UTC : ${formatTime(utc.hour, utc.minute)}, jour ${serverDay}`,
      timezoneLabel: label,
    }
  }

  let days: number[] | null = null

  if (/\b(ouvrable|ouvrables|ouvre|ouvres|lundi au vendredi|semaine)\b/.test(text)) {
    days = [1, 2, 3, 4, 5]
  } else if (/\b(week end|weekend|samedi et dimanche)\b/.test(text)) {
    days = [0, 6]
  } else {
    const namedDays = findNamedWeekDays(text)
    if (namedDays.length) days = namedDays
  }

  if (days) {
    const serverDays = shiftWeekDays(days, utc.dayOffset)
    return {
      ok: true,
      cron: `${utc.minute} ${utc.hour} * * ${cronDays(serverDays)}`,
      localDescription: `${rangeLabel(days)}${defaultTimeNote} (${label})`,
      serverDescription: `Serveur UTC : ${rangeLabel(serverDays)} à ${formatTime(utc.hour, utc.minute)}`,
      timezoneLabel: label,
    }
  }

  if (/\b(tous les jours|toutes les nuits|chaque jour|quotidien|quotidienne|journalier|journaliere)\b/.test(text)) {
    return {
      ok: true,
      cron: `${utc.minute} ${utc.hour} * * *`,
      localDescription: `Tous les jours${defaultTimeNote} (${label})`,
      serverDescription: `Serveur UTC : tous les jours à ${formatTime(utc.hour, utc.minute)}`,
      timezoneLabel: label,
    }
  }

  return {
    ok: false,
    message: 'Fréquence non reconnue. Essayez "tous les jours à 2h", "chaque lundi à 3h" ou "toutes les 6 heures".',
    timezoneLabel: label,
  }
}
