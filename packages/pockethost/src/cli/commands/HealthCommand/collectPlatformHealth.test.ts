import { mkdtemp, rm, symlink, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { httpProbe, memoryAllocationStatus, monitoredFileServices, readRows, readStatus } from './collectPlatformHealth'

describe('monitoring collector boundaries', () => {
  it('alerts when automatic memory allocation stops or cannot measure a service', () => {
    const now = Date.now()
    expect(memoryAllocationStatus({ checkedAt: new Date(now - 46000).toISOString(), resources: [] }, now).state).toBe(
      'error'
    )
    expect(memoryAllocationStatus({ checkedAt: new Date(now + 31000).toISOString(), resources: [] }, now).state).toBe(
      'error'
    )
    expect(
      memoryAllocationStatus({ checkedAt: new Date(now).toISOString(), resources: [{ decision: 'unavailable' }] }, now)
        .state
    ).toBe('warning')
    expect(
      memoryAllocationStatus({ checkedAt: new Date(now).toISOString(), resources: [{ decision: 'grow' }] }, now).state
    ).toBe('ok')
  })
  const cleanup: (() => Promise<void>)[] = []
  afterEach(async () => {
    for (const fn of cleanup.splice(0)) await fn()
  })
  it('does not follow redirects to another service and records HTTP failures', async () => {
    let secretReached = false
    const server = createServer((req, res) => {
      if (req.url === '/redirect') {
        res.writeHead(302, { Location: '/private' })
        res.end()
      } else if (req.url === '/private') {
        secretReached = true
        res.end('private')
      } else {
        res.writeHead(503)
        res.end()
      }
    })
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    cleanup.push(() => new Promise((resolve) => server.close(() => resolve())))
    const port = (server.address() as any).port
    expect((await httpProbe(`http://127.0.0.1:${port}/redirect`)).ok).toBe(false)
    expect(secretReached).toBe(false)
    expect((await httpProbe(`http://127.0.0.1:${port}/`)).detail).toBe('HTTP 503')
  })
  it('refuses symlinked and oversized worker files', async () => {
    const root = await mkdtemp(join(tmpdir(), 'kolab-monitor-'))
    cleanup.push(() => rm(root, { recursive: true, force: true }))
    await writeFile(join(root, 'status'), '{}')
    await symlink(join(root, 'status'), join(root, 'link'))
    await expect(readStatus(join(root, 'link'))).rejects.toThrow()
    await writeFile(join(root, 'large'), 'x'.repeat(4 * 1024 ** 2 + 1))
    await expect(readStatus(join(root, 'large'))).rejects.toThrow()
  })
  it('pages the control inventory and never silently truncates it', async () => {
    let pages = 0
    const client = {
      collection: () => ({
        getList: async (page: number) => {
          pages++
          return { totalPages: 2, items: [{ id: page }] }
        },
      }),
    } as any
    expect(await readRows(client, 'instances', 'id')).toEqual([{ id: 1 }, { id: 2 }])
    expect(pages).toBe(2)
    const tooMany = { collection: () => ({ getList: async () => ({ totalPages: 26, items: [] }) }) } as any
    await expect(readRows(tooMany, 'instances', 'id')).rejects.toThrow('bound')
  })
})

describe('optional legacy FTPS monitoring', () => {
  it('keeps existing FTP and SFTP checks when FTPS is enabled', () => {
    expect(monitoredFileServices(false)).toEqual([
      { process: 'edge-ftp', name: 'FTP' },
      { process: 'edge-sftp', name: 'SFTP' },
    ])
  })
  it('keeps SFTP checked when legacy FTPS is deliberately disabled', () => {
    expect(monitoredFileServices(true)).toEqual([{ process: 'edge-sftp', name: 'SFTP' }])
  })
})
