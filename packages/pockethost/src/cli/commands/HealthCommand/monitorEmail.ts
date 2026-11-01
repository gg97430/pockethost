import type { MonitorCheck, MonitoringReport } from '../../../common/platformMonitoring'

const escape = (value: string) =>
  value.replace(
    /[&<>"']/g,
    (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]!
  )
const clean = (value: string) => value.replace(/[\r\n\u0000-\u001f]/g, ' ').slice(0, 500)
const groups = {
  infrastructure: 'Infrastructure',
  databases: 'Base de données',
  hosting: 'Hébergement',
  backups: 'Sauvegarde',
  security: 'Sécurité',
}

/** Inline styles and presentation tables keep the message usable without remote assets or scripts. */
export function monitoringEmail(report: MonitoringReport, opened: MonitorCheck[], recovered: string[]) {
  const domain = clean(report.domain)
  const url = new URL(`https://${domain}/monitoring`)
  if (url.hostname !== domain || url.username || url.password || url.port || url.pathname !== '/monitoring')
    throw Error('Domaine de supervision invalide.')
  const errors = opened.filter((row) => row.state === 'error').length
  const title = opened.length ? (errors ? 'Incident détecté' : 'Point de vigilance') : 'Services rétablis'
  const accent = opened.length ? (errors ? '#b42318' : '#925600') : '#087443'
  const tint = opened.length ? (errors ? '#fff1ef' : '#fff7e6') : '#ecfdf3'
  const summary = [
    ...(opened.length
      ? [`${opened.length} nouvelle${opened.length > 1 ? 's' : ''} alerte${opened.length > 1 ? 's' : ''}`]
      : []),
    ...(recovered.length ? [`${recovered.length} rétablissement${recovered.length > 1 ? 's' : ''}`] : []),
  ].join(' · ')
  const date = new Intl.DateTimeFormat('fr-FR', {
    timeZone: 'Indian/Reunion',
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(report.checkedAt))
  const entries = opened
    .slice(0, 25)
    .map((row) => ({
      name: clean(row.name),
      detail: clean(row.detail),
      group: groups[row.group],
      status: row.state === 'error' ? 'INCIDENT' : 'À SURVEILLER',
      color: row.state === 'error' ? '#b42318' : '#925600',
      background: row.state === 'error' ? '#fff1ef' : '#fff7e6',
    }))
    .concat(
      recovered.slice(0, 25).map((name) => ({
        name: clean(name),
        detail: 'Le contrôle confirme le rétablissement du service.',
        group: 'Rétablissement',
        status: 'RÉTABLI',
        color: '#087443',
        background: '#ecfdf3',
      }))
    )
  const omitted = opened.length + recovered.length - entries.length
  const cards = entries
    .map(
      (row) => `<tr><td style="padding:0 28px 14px">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border:1px solid #dde3eb;border-left:4px solid ${row.color};border-radius:8px"><tr><td style="padding:18px">
      <span style="display:inline-block;padding:4px 8px;border-radius:4px;background:${row.background};color:${row.color};font-size:11px;font-weight:700;letter-spacing:1px">${row.status}</span>
      <p style="margin:12px 0 5px;color:#162137;font-size:18px;font-weight:700;overflow-wrap:anywhere">${escape(row.name)}</p>
      <p style="margin:0 0 10px;color:#596579;font-size:12px">${escape(row.group)}</p>
      <p style="margin:0;color:#344054;font-size:14px;line-height:1.6;overflow-wrap:anywhere">${escape(row.detail)}</p>
    </td></tr></table></td></tr>`
    )
    .join('')
  const counters = [
    [report.counts.error, 'en erreur', '#b42318'],
    [report.counts.warning, 'à surveiller', '#925600'],
    [report.counts.ok, 'OK', '#087443'],
  ]
    .map(
      ([count, label, color]) =>
        `<td width="33%" valign="top" style="padding:14px 6px;text-align:center;background:#f5f7fa"><strong style="font-size:26px;color:${color}">${count}</strong><br><span style="font-size:12px;color:#475467">${label}</span></td>`
    )
    .join('')
  const html = `<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>${escape(title)} · Kolab</title></head>
  <body style="margin:0;padding:0;background:#edf1f6;font-family:Verdana,Geneva,sans-serif;color:#162137">
  <div style="display:none;max-height:0;overflow:hidden;mso-hide:all">${escape(summary)} sur ${escape(domain)}. ${report.counts.error} contrôle(s) actuellement en erreur.</div>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:24px 8px">
  <table role="presentation" width="600" cellspacing="0" cellpadding="0" style="width:100%;max-width:600px;background:#ffffff;border:1px solid #dce3ec;border-radius:12px">
    <tr><td style="padding:24px 28px;background:#162137;border-radius:12px 12px 0 0;color:#ffffff"><strong style="font-size:23px;letter-spacing:3px">KOLAB</strong><span style="display:block;margin-top:7px;color:#b9c7dc;font-size:11px;letter-spacing:2px">SUPERVISION</span></td></tr>
    <tr><td style="padding:26px 28px 18px"><span style="display:inline-block;background:${tint};color:${accent};padding:6px 10px;border-radius:4px;font-size:12px;font-weight:700">${escape(summary)}</span>
      <h1 style="margin:16px 0 12px;font-size:28px;line-height:1.2;color:#162137">${title}</h1>
      <p style="margin:0;font-size:14px;font-weight:700;overflow-wrap:anywhere">${escape(domain)}</p>
      <p style="margin:7px 0 0;font-size:12px;line-height:1.6;color:#596579">${escape(date)} · La Réunion (UTC+4)<br>Serveur ${escape(clean(report.host))}</p>
    </td></tr>
    ${cards}
    ${omitted ? `<tr><td style="padding:0 28px 18px;font-size:13px;color:#596579">${omitted} autre(s) changement(s) à consulter dans la supervision.</td></tr>` : ''}
    <tr><td style="padding:8px 28px 0"><p style="margin:0 0 10px;font-size:11px;letter-spacing:1px;color:#596579;font-weight:700">ÉTAT AU DERNIER CONTRÔLE</p><table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>${counters}</tr></table><p style="margin:10px 0 0;font-size:12px;color:#596579">${report.counts.paused} en pause · ${report.counts.unknown} sans mesure</p></td></tr>
    <tr><td style="padding:26px 28px"><table role="presentation" cellspacing="0" cellpadding="0"><tr><td bgcolor="#2455d6" style="border-radius:6px"><a href="${escape(url.href)}" style="display:inline-block;padding:14px 20px;color:#ffffff;text-decoration:none;font-size:14px;font-weight:700">Ouvrir la supervision &rarr;</a></td></tr></table>
      <p style="margin:16px 0 0;font-size:12px;line-height:1.6;color:#596579">Notification envoyée après confirmation du changement d’état. Les contrôles se poursuivent automatiquement.</p></td></tr>
  </table><p style="margin:18px 0 0;font-size:11px;line-height:1.6;color:#596579">Kolab · Infrastructure, bases de données et hébergements<br><a href="${escape(url.href)}" style="color:#475467">${escape(url.href)}</a></p>
  </td></tr></table></body></html>`
  const text = [
    `KOLAB · ${title}`,
    domain,
    `${date} · La Réunion (UTC+4)`,
    `Serveur ${clean(report.host)}`,
    '',
    summary,
    '',
    ...entries.map((row) => `[${row.status}] ${row.name}\n${row.group} · ${row.detail}\n`),
    ...(omitted ? [`${omitted} autre(s) changement(s) dans la supervision.`] : []),
    `État : ${report.counts.error} en erreur · ${report.counts.warning} à surveiller · ${report.counts.ok} OK · ${report.counts.paused} en pause · ${report.counts.unknown} sans mesure`,
    '',
    url.href,
  ].join('\n')
  return { subject: `[Kolab] ${title} · ${domain}`, text, html }
}
