import { parseSshEd25519PublicKey } from '$common/sshPublicKey'
import { mkLog } from '$util/Logger'

const validateSshKeyRecord = (record: models.Record, authId: string) => {
  const log = mkLog(`ssh-keys`)

  let parsed
  try {
    parsed = parseSshEd25519PublicKey(record.getString('public_key'))
  } catch (error) {
    throw new BadRequestError(`${error}`)
  }
  record.set('public_key', parsed.normalized)

  const fingerprint = record.getString('fingerprint').trim()
  if (!fingerprint.startsWith('SHA256:')) {
    throw new BadRequestError('Empreinte invalide.')
  }

  const allInstances = record.getBool('all_instances')
  const instanceIds = record.getStringSlice('instances') || []

  if (!allInstances && instanceIds.length === 0) {
    throw new BadRequestError('Sélectionnez au moins une instance ou choisissez toutes les instances.')
  }

  if (!allInstances) {
    for (const instanceId of instanceIds) {
      const instance = $app.findRecordById('instances', instanceId)
      if (instance.getString('uid') !== authId) {
        log({ instanceId, authId, uid: instance.getString('uid') })
        throw new BadRequestError('Une ou plusieurs instances sélectionnées ne vous appartiennent pas.')
      }
    }
  }

  if (allInstances) {
    record.set('instances', [])
  }
}

export const BeforeCreate_ssh_keys = (e: core.RecordRequestEvent) => {
  const record = e.record
  if (!record) {
    throw new BadRequestError('Missing record.')
  }

  const authRecord = e.auth
  if (!authRecord) {
    throw new BadRequestError('Authentification requise.')
  }

  record.set('user', authRecord.id)
  validateSshKeyRecord(record, authRecord.id)
}

export const BeforeUpdate_ssh_keys = (e: core.RecordRequestEvent) => {
  const record = e.record
  if (!record) {
    throw new BadRequestError('Missing record.')
  }

  const authRecord = e.auth
  if (!authRecord) {
    throw new BadRequestError('Authentification requise.')
  }

  if (record.getString('user') !== authRecord.id) {
    throw new ForbiddenError('Vous ne pouvez modifier que vos propres clés SSH.')
  }

  validateSshKeyRecord(record, authRecord.id)
}
