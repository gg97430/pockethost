import { join } from 'node:path'
import PocketBase from 'pocketbase'
import {
  APP_URL,
  DAEMON_PORT,
  DATA_ROOT,
  DISCORD_HEALTH_CHANNEL_URL,
  MOTHERSHIP_ADMIN_PASSWORD,
  MOTHERSHIP_ADMIN_USERNAME,
  MOTHERSHIP_PORT,
  PH_FTP_PORT,
  PH_SFTP_PORT,
  SSL_CERT,
  adminAuthWithPassword,
} from '../../..'
import { monitoringReport } from '../../../common/platformMonitoring'
import { collectPlatformHealth, readStatus, writeStatus } from './collectPlatformHealth'
import { monitorConfiguration, notifyMonitoring } from './monitorNotifications'

export const checkHealth = async (options: { notify?: boolean } = {}) => {
  let client: PocketBase | null = new PocketBase(`http://127.0.0.1:${MOTHERSHIP_PORT()}`)
  try {
    await adminAuthWithPassword(client, MOTHERSHIP_ADMIN_USERNAME(), MOTHERSHIP_ADMIN_PASSWORD())
  } catch {
    client = null
  }
  const folder = DATA_ROOT('monitoring'),
    config = await monitorConfiguration(client, folder, DISCORD_HEALTH_CHANNEL_URL())
  let report = await collectPlatformHealth({
    domain: new URL(APP_URL()).hostname,
    dataRoot: DATA_ROOT(),
    webRoot: process.env.WEB_APPS_ROOT || '/home/pockethost-web/data',
    mothershipPort: MOTHERSHIP_PORT(),
    daemonPort: DAEMON_PORT(),
    ftpPort: PH_FTP_PORT(),
    sftpPort: PH_SFTP_PORT(),
    cert: SSL_CERT(),
    client,
  })
  report.checks.push({
    id: 'notification-channels',
    group: 'infrastructure',
    name: 'Notifications de supervision',
    state: config.emailEnabled || config.discordEnabled ? 'ok' : 'paused',
    detail:
      config.emailEnabled || config.discordEnabled
        ? `E-mail ${config.emailEnabled ? 'activé' : 'désactivé'} · Discord ${config.discordEnabled ? 'activé' : 'désactivé'}`
        : 'Canaux désactivés ; configurez e-mail ou Discord dans cette page.',
  })
  try {
    const delivery = await readStatus(join(folder, 'delivery-status.json'))
    for (const channel of ['email', 'discord'] as const)
      if (channel === 'email' ? config.emailEnabled : config.discordEnabled)
        report.checks.push({
          id: 'notification-delivery:' + channel,
          group: 'infrastructure',
          name: 'Envoi ' + channel,
          state:
            delivery[channel]?.status === 'error' ? 'error' : delivery[channel]?.status === 'ok' ? 'ok' : 'unknown',
          detail:
            delivery[channel]?.status === 'error'
              ? 'La dernière notification n’a pas été confirmée par le relais.'
              : delivery[channel]?.status === 'ok'
                ? 'Dernier passage du canal sans erreur.'
                : 'En attente du premier envoi.',
        })
  } catch {}
  report = monitoringReport(report.host, report.domain, report.checks, Date.parse(report.checkedAt))
  await writeStatus(join(folder, 'status.json'), report)
  console.log(
    JSON.stringify({
      host: report.host,
      domain: report.domain,
      checkedAt: report.checkedAt,
      state: report.state,
      counts: report.counts,
    })
  )
  if (options.notify !== false) await notifyMonitoring(report, config, folder)
  return report
}
