import {
  GetUserTokenPayload,
  GetUserTokenPayloadSchema,
  GetUserTokenResult,
  Logger,
  PocketBase,
  RestCommands,
  RestMethods,
  adminAuthWithPassword,
  createFirstAdmin as createFirstAdminRecord,
} from '@'
import Ajv from 'ajv'
import { MixinContext } from '.'
import { createInstanceMixin } from './InstanceMIxin'

export type PocketbaseClientApi = ReturnType<typeof createAdminPbClient>

export const createAdminPbClient = (url: string, logger: Logger) => {
  const _clientLogger = logger.create('PbClient')
  const { info } = _clientLogger

  info(`Initializing client: ${url}`)

  const client = new PocketBase(url)
  client.autoCancellation(false)

  const adminAuthViaEmail = (email: string, password: string) => adminAuthWithPassword(client, email, password)

  const createFirstAdmin = (email: string, password: string) => createFirstAdminRecord(client, email, password)

  const context: MixinContext = { client, logger: _clientLogger }
  const instanceApi = createInstanceMixin(context)

  const validateUserTokenPayload = new Ajv().compile(GetUserTokenPayloadSchema)
  // The generic GET helper drops extra payload fields and logs responses. This
  // private response contains password hashes/token keys, so send it directly.
  const getUserTokenInfo = async (payload: GetUserTokenPayload): Promise<GetUserTokenResult> => {
    if (!validateUserTokenPayload(payload)) throw new Error('Invalid admin identity request')
    return client.send(`/api/${RestCommands.UserToken}/${encodeURIComponent(payload.id)}`, {
      method: RestMethods.Get,
      query: payload.instanceId ? { instanceId: payload.instanceId } : undefined,
    })
  }

  const api = {
    client,
    url,
    createFirstAdmin,
    adminAuthViaEmail,
    getUserTokenInfo,
    ...instanceApi,
  }

  return api
}
