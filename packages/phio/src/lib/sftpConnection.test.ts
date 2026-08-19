import { afterEach, describe, expect, it } from 'vitest'
import {
  DEFAULT_PHIO_SFTP_HOST,
  DEFAULT_PHIO_SFTP_PORT,
  PHIO_SFTP_HOST,
  PHIO_SFTP_PORT,
} from './sftpConnection'

const originalHost = process.env.PHIO_SFTP_HOST
const originalPort = process.env.PHIO_SFTP_PORT

afterEach(() => {
  if (originalHost === undefined) {
    delete process.env.PHIO_SFTP_HOST
  } else {
    process.env.PHIO_SFTP_HOST = originalHost
  }

  if (originalPort === undefined) {
    delete process.env.PHIO_SFTP_PORT
  } else {
    process.env.PHIO_SFTP_PORT = originalPort
  }
})

describe('SFTP environment configuration', () => {
  it('uses PocketHost defaults', () => {
    delete process.env.PHIO_SFTP_HOST
    delete process.env.PHIO_SFTP_PORT

    expect(PHIO_SFTP_HOST()).toBe(DEFAULT_PHIO_SFTP_HOST)
    expect(PHIO_SFTP_PORT()).toBe(DEFAULT_PHIO_SFTP_PORT)
  })

  it('uses a custom SFTP endpoint', () => {
    process.env.PHIO_SFTP_HOST = 'ftp.app2.monappli.re'
    process.env.PHIO_SFTP_PORT = '2223'

    expect(PHIO_SFTP_HOST()).toBe('ftp.app2.monappli.re')
    expect(PHIO_SFTP_PORT()).toBe(2223)
  })

  it('rejects an invalid SFTP port', () => {
    process.env.PHIO_SFTP_PORT = 'not-a-port'

    expect(() => PHIO_SFTP_PORT()).toThrow()
  })
})
