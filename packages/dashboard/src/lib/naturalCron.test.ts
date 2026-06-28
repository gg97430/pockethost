import { describe, expect, it } from 'vitest'
import { parseNaturalCron } from './naturalCron'

const reunion = { timezoneOffsetMinutes: -240, timezoneLabel: 'Indian/Reunion' }
const reunionToUtc = { ...reunion, convertToUtc: true }

describe('parseNaturalCron', () => {
  it('keeps a daily schedule in the configured server timezone', () => {
    expect(parseNaturalCron('tous les jours à 2h', reunion)).toMatchObject({
      ok: true,
      cron: '0 2 * * *',
      serverDescription: 'Serveur Indian/Reunion : tous les jours à 02:00',
    })
  })

  it('keeps a named weekday in the configured server timezone', () => {
    expect(parseNaturalCron('chaque lundi à 3h30', reunion)).toMatchObject({
      ok: true,
      cron: '30 3 * * 1',
    })
  })

  it('keeps weekdays in the configured server timezone', () => {
    expect(parseNaturalCron('du lundi au vendredi à 22h', reunion)).toMatchObject({
      ok: true,
      cron: '0 22 * * 1,2,3,4,5',
    })
  })

  it('supports simple hourly intervals', () => {
    expect(parseNaturalCron('toutes les 6 heures', reunion)).toMatchObject({
      ok: true,
      cron: '0 */6 * * *',
    })
  })

  it('keeps the first day of the month in the configured server timezone', () => {
    expect(parseNaturalCron('le 1 du mois à 2h', reunion)).toMatchObject({
      ok: true,
      cron: '0 2 1 * *',
    })
  })

  it('can still convert a daily local schedule to UTC when requested', () => {
    expect(parseNaturalCron('tous les jours à 2h', reunionToUtc)).toMatchObject({
      ok: true,
      cron: '0 22 * * *',
    })
  })

  it('can still shift a named weekday across the UTC day boundary when requested', () => {
    expect(parseNaturalCron('chaque lundi à 3h30', reunionToUtc)).toMatchObject({
      ok: true,
      cron: '30 23 * * 0',
    })
  })

  it('can still use L when the first local day of the month runs on the previous UTC day', () => {
    expect(parseNaturalCron('le 1 du mois à 2h', reunionToUtc)).toMatchObject({
      ok: true,
      cron: '0 22 L * *',
    })
  })
})
