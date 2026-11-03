export type AccountWelcomeEmailInput = {
  baseUrl: string
  email: string
  password: string
  verificationToken?: string
}
const escapeWelcomeHtml = (value: string) =>
  value.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!)
/** Credentials exist only in the creation request and the mail; never persist a delivery payload. */
export const accountWelcomeEmail = ({ baseUrl, email, password, verificationToken }: AccountWelcomeEmailInput) => {
  const base = baseUrl.trim().replace(/\/+$/, '')
  if (
    !/^(?:https:\/\/(?:[a-z0-9-]+\.)+[a-z]{2,63}(?::\d{1,5})?|http:\/\/(?:localhost|127\.0\.0\.1)(?::\d{1,5})?)$/i.test(
      base
    )
  )
    throw new Error('Adresse de la plateforme invalide.')
  const login = `${base}/login`
  const action = verificationToken ? `${base}/login/confirm-account/${encodeURIComponent(verificationToken)}` : login
  const button = verificationToken ? 'Confirmer mon compte' : 'Me connecter à Kolab'
  const intro = verificationToken
    ? 'Votre compte a été créé par votre administrateur. Confirmez votre adresse e-mail pour commencer.'
    : 'Votre compte est prêt. Retrouvez vos bases de données et vos hébergements dans votre espace Kolab.'
  const credential = (label: string, value: string, mono = false) =>
    `<tr><td style="padding:16px 20px;border-bottom:1px solid #e4e9f0"><p style="margin:0 0 7px;font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#667085">${label}</p><div style="font-family:${mono ? "'Courier New',monospace" : 'Arial,sans-serif'};font-size:16px;font-weight:600;line-height:1.6;color:#162137;overflow-wrap:anywhere;word-break:break-word;white-space:pre-wrap">${escapeWelcomeHtml(value)}</div></td></tr>`
  return {
    subject: '[Kolab Hosting] Votre compte et vos identifiants',
    text: [
      'Bienvenue sur Kolab Hosting',
      '',
      intro,
      '',
      `Plateforme : ${base}`,
      `Connexion : ${login}`,
      `E-mail : ${email}`,
      'Mot de passe :',
      password,
      '',
      ...(verificationToken ? [`Confirmer mon compte : ${action}`, ''] : []),
      'Vous pourrez changer votre mot de passe depuis « Mon compte » après connexion.',
      'Conservez ces identifiants dans votre gestionnaire de mots de passe.',
      '',
      '— Kolab Hosting',
    ].join('\n'),
    html: `<!doctype html><html lang="fr"><head><meta name="viewport" content="width=device-width,initial-scale=1"><meta charset="utf-8"><title>Bienvenue sur Kolab</title></head><body style="margin:0;background:#f2f5fa;font-family:Arial,sans-serif"><div style="display:none;max-height:0;overflow:hidden">Votre accès à Kolab Hosting est prêt.</div><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:32px 12px"><table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;background:#fff;border:1px solid #dce3ec;border-radius:14px"><tr><td style="background:#162137;padding:28px 30px;border-radius:14px 14px 0 0;color:#fff"><strong style="font-size:25px;letter-spacing:4px">KOLAB</strong><p style="margin:8px 0 0;color:#b9c7dc;font-size:11px;letter-spacing:2px">GESTION &amp; HÉBERGEMENT</p></td></tr><tr><td style="padding:30px;border-top:4px solid #2455d6"><span style="display:inline-block;padding:6px 10px;background:#e9f6ef;color:#087443;border-radius:20px;font-size:11px;font-weight:700;letter-spacing:.7px">VOTRE NOUVEL ESPACE</span><h1 style="margin:18px 0 12px;font-size:29px;line-height:1.2;color:#162137">Bienvenue sur Kolab.</h1><p style="margin:0 0 24px;font-size:15px;line-height:1.7;color:#475467">${intro}</p><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e4e9f0;border-radius:8px;background:#f8faff">${credential('Adresse de connexion', login)}${credential('Votre e-mail', email)}${credential('Votre mot de passe', password, true)}</table><table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:26px"><tr><td bgcolor="#2455d6" style="border-radius:7px"><a href="${escapeWelcomeHtml(action)}" style="display:inline-block;padding:15px 23px;color:#fff;text-decoration:none;font-size:15px;font-weight:700">${button} &rarr;</a></td></tr></table><p style="margin:22px 0 0;font-size:13px;line-height:1.7;color:#667085">Vous pourrez changer votre mot de passe depuis <strong>Mon compte</strong> après connexion. Conservez ces identifiants dans votre gestionnaire de mots de passe.</p></td></tr><tr><td style="padding:20px 30px;border-top:1px solid #e4e9f0;background:#fafbfe;border-radius:0 0 14px 14px;font-size:12px;line-height:1.6;color:#667085">Compte créé par votre administrateur sur <a href="${escapeWelcomeHtml(login)}" style="color:#2455d6;text-decoration:none">${escapeWelcomeHtml(base)}</a>.<br>Kolab Hosting · Vos applications, au même endroit.</td></tr></table></td></tr></table></body></html>`,
  }
}
