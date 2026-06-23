import { client } from '$src/pocketbase-client'
import { SSH_KEY_COLLECTION, type SshKeyFields } from 'pockethost/common'

export const sortSshKeys = (keys: SshKeyFields[]) =>
  [...keys].sort((a, b) => {
    const byLabel = a.label.localeCompare(b.label, 'fr', { sensitivity: 'base' })
    if (byLabel !== 0) return byLabel
    return a.id.localeCompare(b.id)
  })

export const listSshKeys = async () => {
  const keys = await client().client.collection(SSH_KEY_COLLECTION).getFullList<SshKeyFields>()
  return sortSshKeys(keys)
}
