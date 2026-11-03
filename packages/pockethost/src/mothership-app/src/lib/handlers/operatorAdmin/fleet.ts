import { parsePlatformFleet } from '../../../../../common/platformFleet'
import { requireOperatorAdmin } from './auth'

/** Fixed-peer read only helper; actor identity is taken from validated native auth, never a query/body. */
export const HandleOperatorFleet = (e: core.RequestEvent) => {
  const actor = requireOperatorAdmin(e)
  if (!actor.getBool('verified')) throw new ForbiddenError('Une adresse e-mail vérifiée est requise.')
  e.response.header().set('Cache-Control', 'no-store')
  try {
    const result = toString(
      $os
        .cmd(
          '/usr/bin/timeout',
          '13',
          '/usr/bin/python3',
          '-I',
          '/opt/pockethost-web/platform_fleet_reader.py',
          'request',
          JSON.stringify({ version: 1, email: actor.getString('email') })
        )
        .output()
    )
    if (result.length > 2 * 1024 * 1024) throw Error('Oversized result')
    return e.json(200, parsePlatformFleet(JSON.parse(result)))
  } catch {
    throw new ApiError(503, 'La vue des serveurs est indisponible. Les sites et bases restent en service.')
  }
}
