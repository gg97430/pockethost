/** Terminal grants never belong in URLs, application storage or logs. */
export const webTerminalUrl = (origin: string, path: string) => {
  const base = new URL(origin)
  if (base.protocol !== 'https:' && !(base.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(base.hostname)))
    throw new Error('Le terminal exige une connexion HTTPS.')
  if (path !== '/api/web-terminal/connect') throw new Error('Adresse du terminal invalide.')
  base.pathname = path
  base.search = ''
  base.hash = ''
  base.protocol = base.protocol === 'https:' ? 'wss:' : 'ws:'
  return base.toString()
}
export const terminalClientFrame = (value: Record<string, unknown>) => {
  if (value.type === 'input') {
    if (typeof value.data !== 'string' || new TextEncoder().encode(value.data).length > 64000)
      throw new Error('Le texte collé dépasse la taille autorisée pour un message.')
  } else if (value.type === 'resize') {
    if (
      !Number.isInteger(value.cols) ||
      !Number.isInteger(value.rows) ||
      Number(value.cols) < 1 ||
      Number(value.cols) > 500 ||
      Number(value.rows) < 1 ||
      Number(value.rows) > 200
    )
      throw new Error('Dimensions du terminal invalides.')
  } else if (value.type !== 'close') throw new Error('Message terminal invalide.')
  return JSON.stringify(value)
}

export const githubManageUrl = (value?: string) => {
  if (!value) return ''
  try {
    const url = new URL(value)
    if (
      url.protocol !== 'https:' ||
      url.hostname !== 'github.com' ||
      url.username ||
      url.password ||
      url.search ||
      url.hash
    )
      return ''
    return /^\/settings\/installations\/\d+$/.test(url.pathname) ||
      /^\/organizations\/[A-Za-z0-9-]+\/settings\/installations\/\d+$/.test(url.pathname) ||
      /^\/apps\/[A-Za-z0-9-]+\/installations\/new$/.test(url.pathname)
      ? url.toString()
      : ''
  } catch {
    return ''
  }
}
