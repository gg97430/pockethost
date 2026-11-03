import { describe, expect, it } from 'vitest'
import { accountWelcomeEmail } from './accountWelcomeEmail'
describe('account welcome mail', () => {
  it('escapes credentials in HTML and preserves their literal value in plain text', () => {
    const password = '  <strong>"initial"&password</strong>  '
    const mail = accountWelcomeEmail({ baseUrl: 'https://app4.example.test/', email: 'user@example.test', password })
    expect(mail.text).toContain(password)
    expect(mail.html).toContain('&lt;strong&gt;&quot;initial&quot;&amp;password&lt;/strong&gt;')
    expect(mail.html).not.toContain(password)
    expect(mail.html).toContain('href="https://app4.example.test/login"')
    expect(mail.subject).toBe('[Kolab Hosting] Votre compte et vos identifiants')
  })
  it('includes email confirmation in one welcome message when needed', () => {
    const mail = accountWelcomeEmail({
      baseUrl: 'https://app3.example.test',
      email: 'user@example.test',
      password: 'synthetic-password',
      verificationToken: 'a.b_c-d',
    })
    expect(mail.html).toContain('Confirmer mon compte')
    expect(mail.html).toContain('/login/confirm-account/a.b_c-d')
  })
  it('rejects injected, insecure or ambiguous platform origins', () => {
    for (const baseUrl of [
      'javascript:alert(1)',
      'https://app.example.test/" onload="alert(1)',
      'https://user:pass@app.example.test',
      'http://public.example.test',
      'https://app.example.test?next=evil',
    ])
      expect(() => accountWelcomeEmail({ baseUrl, email: 'user@example.test', password: 'test' })).toThrow()
  })
})
