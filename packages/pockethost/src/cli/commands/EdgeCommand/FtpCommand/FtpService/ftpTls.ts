/** The fork's connection.secure getter covers implicit TLS and a completed explicit AUTH TLS upgrade. */
export async function authorizeFtpLogin<T>(
  connection: { secure: boolean },
  requireTls: boolean,
  authenticate: () => Promise<T>
): Promise<T> {
  if (requireTls && !connection.secure) throw new Error('TLS is required before FTP authentication.')
  return authenticate()
}
