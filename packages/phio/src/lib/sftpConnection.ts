import env from 'env-var'

export const DEFAULT_PHIO_SFTP_HOST = 'ftp.pockethost.io'
export const DEFAULT_PHIO_SFTP_PORT = 2222

export const PHIO_SFTP_HOST = () =>
  env
    .get('PHIO_SFTP_HOST')
    .default(DEFAULT_PHIO_SFTP_HOST)
    .required()
    .asString()

export const PHIO_SFTP_PORT = () =>
  env
    .get('PHIO_SFTP_PORT')
    .default(DEFAULT_PHIO_SFTP_PORT)
    .required()
    .asPortNumber()

export type SftpConnection = {
  host: string
  port: number
  username: string
  privateKeyPath: string
  remoteDir: string
}

export const buildSftpTarget = ({
  username,
  host,
  remoteDir,
}: SftpConnection) => {
  const userHost = `${username}@${host}`
  return remoteDir ? `${userHost}:${remoteDir}` : userHost
}

export const buildSftpArgs = (connection: SftpConnection) => [
  '-i',
  connection.privateKeyPath,
  '-P',
  String(connection.port),
  buildSftpTarget(connection),
]

const shellQuote = (value: string) => {
  if (/^[a-zA-Z0-9_./:-]+$/.test(value)) {
    return value
  }
  return `'${value.replace(/'/g, `'\\''`)}'`
}

export const formatSftpCommand = (
  connection: SftpConnection,
  sftpBin = 'sftp'
) => [sftpBin, ...buildSftpArgs(connection).map(shellQuote)].join(' ')
