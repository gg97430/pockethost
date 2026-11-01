export const INSTANCE_TRANSFER_PROTOCOL = 'pockethost-transfer-v1'

export type InstanceTransferStatus =
  | 'queued'
  | 'preparing'
  | 'ready'
  | 'downloading'
  | 'restoring'
  | 'complete'
  | 'failed'
  | 'revoked'

export type InstanceTransfer = {
  id: string
  direction: 'export' | 'import'
  instance: string
  subdomain: string
  backup: string
  status: InstanceTransferStatus
  phase?: InstanceTransferStatus | 'verifying'
  preparedBytes?: number
  preparationPhase?: string
  sourceBytes?: number
  progressUpdatedAt?: string
  claimed?: boolean
  expiresAt: string
  error: string
  downloadedBytes: number
  totalBytes: number
  created: string
}

export type TransferMetadata = {
  protocol: typeof INSTANCE_TRANSFER_PROTOCOL
  id: string
  status: InstanceTransferStatus
  expiresAt: string
  instance: {
    subdomain: string
    version: string
    syncAdmin: boolean
    autoVacuum: boolean
    secrets?: unknown
    webhooks?: unknown
  }
  archive?: { filename: string; checksum: string; compressedBytes: number; sizeBytes: number }
}

/** Goja-safe parser; a transfer credential is never placed in a query string. */
export const parseInstanceTransferLink = (value: string, separateCode = '') => {
  const match = value
    .trim()
    .match(
      /^(https?):\/\/([a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::[0-9]{1,5})?)\/api\/instance-transfers\/([a-z0-9]{15})\/?(?:#(?:code=)?([a-zA-Z0-9]{48}))?$/i
    )
  if (!match) throw new Error('Lien de transfert invalide.')
  const code = separateCode.trim() || match[4] || ''
  if (!/^[a-zA-Z0-9]{48}$/.test(code)) throw new Error('Code de transfert invalide.')
  return {
    origin: `${match[1]!.toLowerCase()}://${match[2]!.toLowerCase()}`,
    authority: match[2]!.toLowerCase(),
    protocol: match[1]!.toLowerCase(),
    id: match[3]!,
    code,
  }
}

export const assertTransferPeerAllowed = (
  peer: ReturnType<typeof parseInstanceTransferLink>,
  allowedHosts: string,
  allowHttp = false
) => {
  const hosts = allowedHosts
    .toLowerCase()
    .split(/[\s,]+/)
    .filter(Boolean)
  if (!hosts.includes(peer.authority)) {
    throw new Error(
      `Serveur source non autorisé : ${peer.authority}. Ajoutez-le à INSTANCE_TRANSFER_ALLOWED_HOSTS sur le serveur cible.`
    )
  }
  if (peer.protocol !== 'https' && !allowHttp) throw new Error('Le transfert nécessite HTTPS.')
}

export const validateTransferMetadata = (value: unknown, id: string, maxBytes: number): TransferMetadata => {
  const data = value as TransferMetadata
  if (!data || data.protocol !== INSTANCE_TRANSFER_PROTOCOL || data.id !== id) {
    throw new Error('Ce serveur ne fournit pas un transfert PocketHost compatible.')
  }
  if (data.status !== 'ready') throw new Error('Le transfert source n’est pas prêt ou a déjà été utilisé.')
  if (!Number.isFinite(Date.parse(data.expiresAt)) || Date.parse(data.expiresAt) <= Date.now()) {
    throw new Error('Le lien de transfert a expiré.')
  }
  if (
    !data.instance ||
    !/^[a-z][a-z0-9-]{2,39}$/.test(data.instance.subdomain) ||
    !/^\d+\.\d+\.(?:\d+|\*)$/.test(data.instance.version)
  ) {
    throw new Error('Informations de l’instance source invalides.')
  }
  const archive = data.archive
  if (
    !archive ||
    !/^[a-f0-9]{64}$/.test(archive.checksum) ||
    !/^[a-zA-Z0-9._-]+\.tar\.gz$/.test(archive.filename) ||
    !Number.isSafeInteger(archive.compressedBytes) ||
    archive.compressedBytes <= 0 ||
    archive.compressedBytes > maxBytes ||
    !Number.isSafeInteger(archive.sizeBytes) ||
    archive.sizeBytes < 0 ||
    archive.sizeBytes > maxBytes
  ) {
    throw new Error('Archive source invalide ou trop volumineuse pour ce serveur.')
  }
  return data
}
