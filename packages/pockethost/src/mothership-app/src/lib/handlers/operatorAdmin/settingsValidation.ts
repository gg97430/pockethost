const email = /^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$/
const invalid = (message: string) => {
  throw new BadRequestError(message)
}
export const validateOperatorSettingsInput = (input: any) => {
  if (!input || typeof input !== 'object' || Array.isArray(input)) invalid('Les paramètres doivent être un objet JSON.')
  for (const key of [
    'publicSignupEnabled',
    'autoVerifyUsers',
    'defaultInstancePower',
    'defaultInstanceDevMode',
    'defaultSyncAdmin',
    'defaultAutoVacuum',
  ])
    if (key in input && typeof input[key] !== 'boolean') invalid(`Valeur oui/non attendue pour ${key}.`)
  if ('defaultUserQuota' in input && (!Number.isSafeInteger(input.defaultUserQuota) || input.defaultUserQuota < 0))
    invalid('Le quota doit être un entier positif ou nul.')
  if (
    'defaultSubscription' in input &&
    !['free', 'premium', 'founder', 'flounder', 'legacy'].includes(input.defaultSubscription)
  )
    invalid('Offre par défaut invalide.')
  if ('serverTimezone' in input) {
    if (typeof input.serverTimezone !== 'string' || !input.serverTimezone.trim()) invalid('Fuseau horaire obligatoire.')
    const name = input.serverTimezone.trim()
    const zone = new Timezone(name)
    if (!['UTC', 'Etc/UTC', 'Local'].includes(name) && zone.string() === 'UTC')
      invalid('Fuseau horaire invalide. Exemple : Indian/Reunion.')
  }
  for (const [key, max] of [
    ['supportEmail', 254],
    ['maintenanceMessage', 2000],
    ['notes', 10000],
  ] as const)
    if (key in input && (typeof input[key] !== 'string' || input[key].length > max))
      invalid(`Le champ ${key} est invalide ou trop long.`)
  if (input.supportEmail && !email.test(input.supportEmail.trim())) invalid('Adresse email de support invalide.')
  for (const group of ['smtp', 'backupS3'])
    if (group in input && (!input[group] || typeof input[group] !== 'object' || Array.isArray(input[group])))
      invalid(`Configuration ${group} invalide.`)
  if (input.smtp) {
    const s = input.smtp
    for (const key of ['enabled', 'tls'])
      if (key in s && typeof s[key] !== 'boolean') invalid(`Valeur oui/non attendue pour SMTP ${key}.`)
    if ('port' in s && (!Number.isInteger(s.port) || s.port < 1 || s.port > 65535))
      invalid('Le port SMTP doit être un entier entre 1 et 65535.')
    if ('authMethod' in s && !['PLAIN', 'LOGIN'].includes(s.authMethod))
      invalid('Méthode SMTP attendue : PLAIN ou LOGIN.')
    for (const key of ['host', 'username', 'password', 'localName', 'senderName', 'senderAddress'])
      if (
        key in s &&
        (typeof s[key] !== 'string' ||
          s[key].length > (key === 'password' ? 1024 : 255) ||
          (/[\r\n\0]/.test(s[key]) && key !== 'password'))
      )
        invalid(`Champ SMTP ${key} invalide.`)
    if (s.senderAddress && !email.test(s.senderAddress.trim()))
      invalid(
        'Expéditeur email invalide : saisissez uniquement une adresse, par exemple contact@monappli.re. Le nom va dans « Expéditeur nom ».'
      )
    if (s.host && /[\s/\\@]/.test(s.host.trim()))
      invalid('L’hôte SMTP doit contenir uniquement un nom de serveur ou une adresse IP, sans https:// ni port.')
  }
  if (input.backupS3) {
    const s = input.backupS3
    if ('enabled' in s && typeof s.enabled !== 'boolean') invalid('Valeur oui/non attendue pour S3/R2.')
    for (const key of ['endpoint', 'bucket', 'prefix', 'region', 'accessKeyId', 'secretAccessKey'])
      if (key in s && (typeof s[key] !== 'string' || s[key].length > (key === 'secretAccessKey' ? 1024 : 500)))
        invalid(`Champ S3/R2 ${key} invalide.`)
    if (s.endpoint && !/^https?:\/\/[^\s/?#@]+(?::\d+)?(?:\/[^\s?#]*)?$/.test(s.endpoint.trim()))
      invalid('Endpoint S3/R2 invalide : une URL http:// ou https:// est attendue, sans identifiants dans l’URL.')
  }
}
export const validateSMTPConfiguration = (smtp: any) => {
  validateOperatorSettingsInput({ smtp })
  if (smtp.enabled) {
    if (!smtp.host) invalid('Hôte SMTP obligatoire lorsque SMTP est activé.')
    if (!smtp.senderAddress || !email.test(smtp.senderAddress))
      invalid('Adresse email expéditeur valide obligatoire lorsque SMTP est activé.')
    if (smtp.username && !smtp.password) invalid('Mot de passe SMTP obligatoire pour cet utilisateur.')
  }
}

export const validateS3Configuration = (s: any) => {
  validateOperatorSettingsInput({ backupS3: s })
  if (s.enabled && (!s.endpoint || !s.bucket || !s.accessKeyId || !s.secretAccessKey))
    invalid('S3/R2 activé : renseignez l’endpoint, le bucket, la clé d’accès et la clé secrète.')
}
export const validateOperatorUserInput = (input: any) => {
  if (
    'managedInstances' in input &&
    (!Array.isArray(input.managedInstances) ||
      input.managedInstances.length > 1000 ||
      input.managedInstances.some((id: unknown) => typeof id !== 'string' || !/^[a-z0-9]{15}$/.test(id)))
  )
    invalid('Sélection de bases invalide (1000 bases maximum).')
  for (const key of ['verified', 'superAdmin'])
    if (key in input && typeof input[key] !== 'boolean') invalid(`Valeur oui/non attendue pour ${key}.`)
  if (
    'subscription_quantity' in input &&
    (!Number.isSafeInteger(input.subscription_quantity) || input.subscription_quantity < 0)
  )
    invalid('Le quota utilisateur doit être un entier positif ou nul.')
  if ('subscription' in input && !['free', 'premium', 'founder', 'flounder', 'legacy'].includes(input.subscription))
    invalid('Offre utilisateur invalide.')
  if ('suspension' in input && (typeof input.suspension !== 'string' || input.suspension.length > 1000))
    invalid('Motif de suspension invalide ou trop long.')
}
