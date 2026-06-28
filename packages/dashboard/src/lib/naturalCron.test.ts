import { describe, expect, it } from 'vitest'
import { parseNaturalCron } from './naturalCron'

const reunion = { timezoneOffsetMinutes: -240, timezoneLabel: 'Indian/Reunion' }

describe('parseNaturalCron', () => {
  it('converts a daily local schedule to server UTC cron', () => {
    expect(parseNaturalCron('tous les jours à 2h', reunion)).toMatchObject({
      ok: true,
      cron: '0 22 * * *',
    })
  })

  it('converts a named weekday across the UTC day boundary', () => {
    expect(parseNaturalCron('chaque lundi à 3h30', reunion)).toMatchObject({
      ok: true,
      cron: '30 23 * * 0',
    })
  })

  it('converts weekdays without shifting when the UTC day stays identical', () => {
    expect(parseNaturalCron('du lundi au vendredi à 22h', reunion)).toMatchObject({
      ok: true,
      cron: '0 18 * * 1,2,3,4,5',
    })
  })

  it('supports simple hourly intervals', () => {
    expect(parseNaturalCron('toutes les 6 heures', reunion)).toMatchObject({
      ok: true,
      cron: '0 */6 * * *',
    })
  })

  it('uses L when the first local day of the month runs on the previous UTC day', () => {
    expect(parseNaturalCron('le 1 du mois à 2h', reunion)).toMatchObject({
      ok: true,
      cron: '0 22 L * *',
    })
  })
})
