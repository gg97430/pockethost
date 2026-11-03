import { accountWelcomeEmail } from '../../../../../common/accountWelcomeEmail'

export const welcomeMailSettings = () => {
  const settings = $app.settings()
  if (!settings.smtp?.enabled || !settings.smtp.host || !settings.meta.senderAddress)
    throw new BadRequestError('Configurez le serveur SMTP avant d’envoyer les identifiants par e-mail.')
  const baseUrl = ($os.getenv('APP_URL') || `https://${$os.getenv('APP_HOST') || $os.getenv('APEX_DOMAIN')}`).trim()
  // Validate the configured origin before the account is saved. Request headers never determine the link.
  accountWelcomeEmail({ baseUrl, email: '', password: '' })
  return {
    baseUrl,
    senderAddress: settings.meta.senderAddress,
    senderName: settings.meta.senderName || 'Kolab Hosting',
  }
}
export const sendAccountWelcomeEmail = (
  record: core.Record,
  password: string,
  settings: ReturnType<typeof welcomeMailSettings>
) => {
  const mail = accountWelcomeEmail({
    baseUrl: settings.baseUrl,
    email: record.email(),
    password,
    ...(!record.getBool('verified') ? { verificationToken: record.newVerificationToken() } : {}),
  })
  $app.newMailClient().send(
    new MailerMessage({
      from: { address: settings.senderAddress, name: settings.senderName },
      to: [{ address: record.email() }],
      ...mail,
    })
  )
}
