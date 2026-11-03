import type { PocketBase } from '@'
import { SSH_KEY_COLLECTION, SshKeyFields, UserFields } from '@'
import { createHash } from 'crypto'
import ssh2 from 'ssh2'

export type SshKeyAuthResult = {
  user: UserFields
  key: SshKeyFields
  instanceIds: string[] | null
}

export function sshPublicKeyFingerprint(keyData: Buffer): string {
  const hash = createHash('sha256').update(keyData).digest('base64')
  return `SHA256:${hash.replace(/=+$/, '')}`
}

/** PocketBase single relations may be returned as one ID rather than an array. */
export const sshKeyInstanceIds = (value: unknown): string[] => {
  const values = typeof value === 'string' ? [value] : Array.isArray(value) ? value : []
  return [...new Set(values.filter((id): id is string => typeof id === 'string' && /^[a-z0-9]{15}$/.test(id)))]
}

export async function findSshKeyByPublicKey(
  adminClient: PocketBase,
  email: string,
  keyAlgo: string,
  keyData: Buffer
): Promise<SshKeyAuthResult | null> {
  if (keyAlgo !== 'ssh-ed25519') {
    return null
  }

  let user: UserFields
  try {
    user = await adminClient.collection('users').getFirstListItem<UserFields>(`email=${JSON.stringify(email)}`)
  } catch {
    return null
  }

  const keys = await adminClient.collection(SSH_KEY_COLLECTION).getFullList<SshKeyFields>({
    filter: `user=${JSON.stringify(user.id)}`,
  })

  for (const key of keys) {
    let parsed
    try {
      parsed = ssh2.utils.parseKey(key.public_key)
    } catch {
      continue
    }

    if (parsed instanceof Error || parsed.type !== 'ssh-ed25519') {
      continue
    }

    if (!parsed.getPublicSSH().equals(keyData)) {
      continue
    }

    const instanceIds = key.all_instances ? null : sshKeyInstanceIds(key.instances)

    return { user, key, instanceIds }
  }

  return null
}

export function verifySshPublicKeySignature(
  publicKeyLine: string,
  blob: Buffer,
  signature: Buffer,
  hashAlgo?: string
): boolean {
  const parsed = ssh2.utils.parseKey(publicKeyLine)
  if (parsed instanceof Error) return false
  return parsed.verify(blob, signature, hashAlgo)
}
