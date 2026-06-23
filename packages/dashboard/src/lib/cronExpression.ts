export const CUSTOM_CRON_PRESET_ID = 'custom'

export type CronPreset = {
  id: string
  label: string
  cron: string
  description: string
}

export const CRON_PRESETS: CronPreset[] = [
  {
    id: 'hourly',
    label: 'Toutes les heures',
    cron: '0 * * * *',
    description: 'À la minute 0 de chaque heure (UTC)',
  },
  {
    id: 'daily-midnight',
    label: 'Tous les jours à minuit',
    cron: '0 0 * * *',
    description: 'Tous les jours à 00:00 UTC',
  },
  {
    id: 'weekdays-9am',
    label: 'Chaque jour ouvré à 9 h',
    cron: '0 9 * * 1-5',
    description: 'Du lundi au vendredi à 09:00 UTC',
  },
  {
    id: 'monday-noon',
    label: 'Chaque lundi à midi',
    cron: '0 12 * * 1',
    description: 'Le lundi à 12:00 UTC',
  },
  {
    id: 'friday-6pm',
    label: 'Chaque vendredi à 18 h',
    cron: '0 18 * * 5',
    description: 'Le vendredi à 18:00 UTC',
  },
  {
    id: 'every-6-hours',
    label: 'Toutes les 6 heures',
    cron: '0 */6 * * *',
    description: '00:00, 06:00, 12:00 et 18:00 UTC',
  },
  {
    id: 'weekly-sunday',
    label: 'Chaque dimanche à minuit',
    cron: '0 0 * * 0',
    description: 'Le dimanche à 00:00 UTC',
  },
  {
    id: 'monthly-first',
    label: 'Le premier de chaque mois à minuit',
    cron: '0 0 1 * *',
    description: 'Jour 1 de chaque mois à 00:00 UTC',
  },
  {
    id: 'macro-daily',
    label: '@daily',
    cron: '@daily',
    description: 'Une fois par jour à minuit UTC',
  },
  {
    id: 'macro-hourly',
    label: '@hourly',
    cron: '@hourly',
    description: 'Une fois par heure à la minute 0 UTC',
  },
  {
    id: 'macro-weekdays',
    label: '@weekdays',
    cron: '@weekdays',
    description: 'Chaque jour ouvré à minuit UTC',
  },
  {
    id: CUSTOM_CRON_PRESET_ID,
    label: 'Expression personnalisée...',
    cron: '',
    description: '',
  },
]

const CRON_MACROS = [
  '@yearly',
  '@annually',
  '@monthly',
  '@weekly',
  '@daily',
  '@midnight',
  '@hourly',
  '@minutely',
  '@secondly',
  '@weekdays',
  '@weekends',
] as const

export function validateCronExpression(cronExpression: string): boolean {
  if (!cronExpression || cronExpression.length === 0) return false

  const expression = cronExpression.trim()

  if (CRON_MACROS.includes(expression as (typeof CRON_MACROS)[number])) {
    return true
  }

  const parts = expression.split(/\s+/)
  if (parts.length !== 5) return false

  const validChars = /^[\d*,\-/?LW#]+$/
  return parts.every((part) => validChars.test(part))
}

export function findPresetIdForCron(cron: string): string {
  const trimmed = cron.trim()
  if (!trimmed) return ''

  const match = CRON_PRESETS.find((preset) => preset.id !== CUSTOM_CRON_PRESET_ID && preset.cron === trimmed)
  return match?.id ?? CUSTOM_CRON_PRESET_ID
}

export function getCronPreset(id: string): CronPreset | undefined {
  return CRON_PRESETS.find((preset) => preset.id === id)
}
