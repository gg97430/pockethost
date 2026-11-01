import { mkdtemp, rm } from 'node:fs/promises'
import { createServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { monitoringReport } from '../../../common/platformMonitoring'
import { notifyMonitoring, sendMonitorEmail } from './monitorNotifications'

describe('monitoring notification delivery', () => {
  const cleanup: (() => Promise<void>)[] = []
  afterEach(async () => {
    vi.unstubAllGlobals()
    for (const fn of cleanup.splice(0)) await fn()
  })
  it('does not resend confirmed Discord messages when the email channel fails', async () => {
    const folder = await mkdtemp(join(tmpdir(), 'kolab-notify-isolation-'))
    cleanup.push(() => rm(folder, { recursive: true, force: true }))
    const fetch = vi.fn(async () => new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetch)
    const config = {
      revision: 'r1',
      emailEnabled: true,
      emailRecipients: ['fixture@example.invalid'],
      smtp: { enabled: false },
      discordEnabled: true,
      discordWebhook: 'https://discord.com/api/webhooks/fixture',
    }
    const report = monitoringReport('host', 'app.example.invalid', [])
    const first = await notifyMonitoring(report, config, folder)
    expect(first.email.status).toBe('error')
    expect(first.discord.status).toBe('ok')
    await notifyMonitoring(report, config, folder)
    expect(fetch).toHaveBeenCalledTimes(1)
  })
  it('sends through a real local SMTP dialogue without creating a credentials file', async () => {
    const recipients: string[] = []
    let message = ''
    const server = createServer((socket) => {
      socket.setEncoding('utf8')
      socket.write('220 local fixture\r\n')
      let buffer = '',
        data = false
      socket.on('data', (chunk) => {
        buffer += chunk
        let end: number
        while ((end = buffer.indexOf('\r\n')) >= 0) {
          const line = buffer.slice(0, end)
          buffer = buffer.slice(end + 2)
          if (data) {
            if (line === '.') {
              data = false
              socket.write('250 accepted\r\n')
            } else message += line + '\n'
            continue
          }
          if (/^EHLO|^HELO/i.test(line)) socket.write('250 local\r\n')
          else if (/^MAIL FROM/i.test(line)) socket.write('250 ok\r\n')
          else if (/^RCPT TO/i.test(line)) {
            recipients.push(line)
            socket.write('250 ok\r\n')
          } else if (line.toUpperCase() === 'DATA') {
            data = true
            socket.write('354 message\r\n')
          } else if (line.toUpperCase() === 'QUIT') {
            socket.end('221 bye\r\n')
          }
        }
      })
    })
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    cleanup.push(() => new Promise((resolve) => server.close(() => resolve())))
    await sendMonitorEmail(
      {
        enabled: true,
        host: '127.0.0.1',
        port: (server.address() as any).port,
        tls: false,
        senderAddress: 'sender@example.invalid',
      },
      ['receiver@example.invalid'],
      'Fixture monitoring',
      'Incident local contrôlé'
    )
    expect(recipients).toEqual(['rcpt TO:<receiver@example.invalid>'])
    expect(message).toContain('Subject: Fixture monitoring')
  })
  it('deduplicates incidents and confirms recovery independently for Discord', async () => {
    const folder = await mkdtemp(join(tmpdir(), 'kolab-notify-'))
    cleanup.push(() => rm(folder, { recursive: true, force: true }))
    const fetch = vi.fn(async () => new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetch)
    const config = {
      revision: 'r1',
      emailEnabled: false,
      emailRecipients: [],
      discordEnabled: true,
      discordWebhook: 'https://discord.com/api/webhooks/fixture',
    }
    const report = monitoringReport('host', 'app.example.invalid', [
      { id: 'database:test', group: 'databases', name: 'BDD', state: 'error', detail: 'HTTP 503' },
    ])
    await notifyMonitoring(report, config, folder)
    expect(fetch).toHaveBeenCalledTimes(1)
    await notifyMonitoring(report, config, folder)
    expect(fetch).toHaveBeenCalledTimes(2)
    expect(fetch.mock.calls[1]?.[1]).toBeDefined()
    await notifyMonitoring(report, config, folder)
    expect(fetch).toHaveBeenCalledTimes(2)
    report.checks[0]!.state = 'ok'
    await notifyMonitoring(report, config, folder)
    expect(fetch).toHaveBeenCalledTimes(2)
    await notifyMonitoring(report, config, folder)
    expect(fetch).toHaveBeenCalledTimes(3)
    const body = JSON.parse((fetch.mock.calls[2] as any)[1].body)
    expect(body.content).toContain('Rétabli')
    expect(body.allowed_mentions).toEqual({ parse: [] })
  })
})
