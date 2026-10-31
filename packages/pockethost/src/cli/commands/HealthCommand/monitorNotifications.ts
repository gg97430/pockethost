import { spawn } from 'node:child_process'
import { join } from 'node:path'
import type PocketBase from 'pocketbase'
import { advanceMonitorSignals, type MonitoringReport, type MonitorSignals } from '../../../common/platformMonitoring'
import { readStatus, writeStatus } from './collectPlatformHealth'

type Configuration = {
  revision: string
  emailEnabled: boolean
  emailRecipients: string[]
  discordEnabled: boolean
  discordWebhook: string
  smtp?: any
}
export const EMAIL_WORKER = String.raw`
import sys,json,smtplib,ssl,socket,signal,ipaddress
from email.message import EmailMessage
from email.utils import formataddr
signal.signal(signal.SIGALRM,lambda *args:(_ for _ in ()).throw(TimeoutError()));signal.alarm(25)
value=json.load(sys.stdin);config=value['smtp'];client=None
try:
 local=config.get('localName') or None
 if config.get('tls'):client=smtplib.SMTP_SSL(config['host'],int(config['port']),timeout=8,context=ssl.create_default_context(),local_hostname=local)
 else:client=smtplib.SMTP(config['host'],int(config['port']),timeout=8,local_hostname=local)
 client.ehlo_or_helo_if_needed()
 if not config.get('tls') and client.has_extn('starttls'):client.starttls(context=ssl.create_default_context());client.ehlo()
 if config.get('username'):
  if not isinstance(client.sock,ssl.SSLSocket) and not ipaddress.ip_address(client.sock.getpeername()[0]).is_loopback:raise ValueError()
  client.user=config['username'];client.password=config.get('password','');method=config.get('authMethod','PLAIN')
  client.auth(method,client.auth_login if method=='LOGIN' else client.auth_plain,initial_response_ok=method!='LOGIN')
 message=EmailMessage();message['From']=formataddr((config.get('senderName','Kolab Hosting'),config['senderAddress']));message['To']=', '.join(value['to']);message['Subject']=value['subject'];message.set_content(value['content'])
 client.send_message(message);print(json.dumps({'ok':True}))
except Exception:print(json.dumps({'ok':False}))
finally:
 if client:
  try:client.close()
  except Exception:pass
`

export async function sendMonitorEmail(smtp: any, to: string[], subject: string, content: string) {
  if (!smtp?.enabled || !smtp.host || !smtp.senderAddress || !to.length)
    throw Error('SMTP ou destinataires indisponibles.')
  await new Promise<void>((resolve, reject) => {
    const child = spawn('python3', ['-c', EMAIL_WORKER], { stdio: ['pipe', 'pipe', 'pipe'] })
    let output = ''
    const timer = setTimeout(() => child.kill('SIGKILL'), 30000)
    child.stdout.on('data', (data) => {
      output += data.toString()
      if (output.length > 4096) child.kill('SIGKILL')
    })
    child.stderr.resume()
    child.on('error', () => {
      clearTimeout(timer)
      reject(Error('Relais SMTP indisponible.'))
    })
    child.on('close', (code) => {
      clearTimeout(timer)
      try {
        if (code !== 0 || !JSON.parse(output).ok) throw Error()
        resolve()
      } catch {
        reject(Error('Envoi e-mail non confirmé par le relais SMTP.'))
      }
    })
    child.stdin.on('error', () => {})
    child.stdin.end(JSON.stringify({ smtp, to, subject, content }))
  })
}

export async function monitorConfiguration(
  client: PocketBase | null,
  folder: string,
  legacyDiscord = ''
): Promise<Configuration> {
  const cache = join(folder, 'notification-config.json')
  if (client)
    try {
      let config: Configuration
      try {
        config = await client.send<Configuration>('/api/admin/_monitoring/config', { method: 'GET', requestKey: null })
      } catch (error: any) {
        if (error.status !== 404) throw error
        config = {
          revision: 'legacy',
          emailEnabled: false,
          emailRecipients: [],
          discordEnabled: !!legacyDiscord,
          discordWebhook: legacyDiscord,
        }
      }
      await writeStatus(cache, config)
      return config
    } catch {}
  // Private 0600 cache preserves outage alerts when authentication/the controller itself is unavailable.
  try {
    return await readStatus(cache)
  } catch {
    return { revision: '', emailEnabled: false, emailRecipients: [], discordEnabled: false, discordWebhook: '' }
  }
}

export async function notifyMonitoring(report: MonitoringReport, config: Configuration, folder: string) {
  const label = (value: string) => value.replace(/[\r\n`*_<>]/g, ' ').slice(0, 200)
  const deliveries: Record<string, any> = {}
  for (const channel of ['email', 'discord'] as const) {
    const enabled = channel === 'email' ? config.emailEnabled : config.discordEnabled
    if (!enabled) {
      deliveries[channel] = { status: 'disabled' }
      continue
    }
    const path = join(folder, 'notifications-' + channel + '.json')
    let previous: { checkedAt?: string; lastSummaryAt?: number; signals?: MonitorSignals; revision?: string } = {}
    try {
      previous = await readStatus(path)
    } catch {}
    if (previous.revision !== config.revision) previous = {}
    const recent = Date.now() - Date.parse(previous.checkedAt || '') < 180000
    const before = Object.fromEntries(
      Object.entries(previous.signals || {}).map(([id, signal]) => [
        id,
        recent ? signal : { ...signal, failed: 0, healthy: 0 },
      ])
    )
    const transition = advanceMonitorSignals(report.checks, before),
      summaryDue = Date.now() - (previous.lastSummaryAt || 0) >= 3600000
    try {
      if (transition.opened.length || transition.recovered.length || summaryDue) {
        const lines = [
          `Kolab Hosting · ${label(report.domain)}`,
          ...transition.opened
            .slice(0, 25)
            .map((row) => `${row.state === 'error' ? '🔴' : '🟡'} ${label(row.name)} : ${label(row.detail)}`),
          ...transition.recovered.slice(0, 25).map((name) => `🟢 Rétabli : ${label(name)}`),
          ...(summaryDue
            ? [
                `État : ${report.counts.ok} OK · ${report.counts.error} en erreur · ${report.counts.warning} à surveiller · ${report.counts.unknown} sans mesure · ${report.counts.paused} en pause`,
              ]
            : []),
          `https://${report.domain}/monitoring`,
        ]
        if (channel === 'email')
          await sendMonitorEmail(
            config.smtp,
            config.emailRecipients,
            `[Kolab Hosting] ${report.domain} · supervision`,
            lines.join('\n')
          )
        else {
          let content = ''
          const chunks: string[] = []
          for (const line of lines) {
            if ((content + '\n' + line).length > 1900) {
              chunks.push(content)
              content = ''
            }
            content += (content ? '\n' : '') + line
          }
          if (content) chunks.push(content)
          for (const chunk of chunks) {
            const response = await fetch(config.discordWebhook, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ content: chunk, allowed_mentions: { parse: [] } }),
              signal: AbortSignal.timeout(10000),
              redirect: 'error',
            })
            if (!response.ok) throw Error('Discord non confirmé.')
          }
        }
      }
      await writeStatus(path, {
        checkedAt: report.checkedAt,
        lastSummaryAt: summaryDue ? Date.now() : previous.lastSummaryAt,
        signals: transition.signals,
        revision: config.revision,
      })
      deliveries[channel] = { status: 'ok', checkedAt: report.checkedAt }
    } catch {
      deliveries[channel] = {
        status: 'error',
        checkedAt: report.checkedAt,
        detail: 'Envoi non confirmé ; nouvelle tentative au prochain contrôle.',
      }
    }
  }
  await writeStatus(join(folder, 'delivery-status.json'), deliveries)
  return deliveries
}
