import type { JSONSchemaType } from 'ajv'
import type { UserId } from '..'

export type GetUserTokenPayload = {
  id: UserId
  instanceId?: string
}

export type GetUserTokenResult = {
  id?: string
  email: string
  tokenKey: string
  passwordHash: string
  preserveExistingNativeIdentity?: boolean
}

export const GetUserTokenPayloadSchema: JSONSchemaType<GetUserTokenPayload> = {
  type: 'object',
  properties: {
    id: { type: 'string' },
    instanceId: { type: 'string', nullable: true, pattern: '^[a-z0-9]{15}$' },
  },
  required: ['id'],
  additionalProperties: false,
}
