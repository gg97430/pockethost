/** Only GitHub's provider pages are valid wizard destinations. */
export const githubDestination = (value: string, kind: 'create' | 'install') => {
  const url = new URL(value)
  const valid =
    kind === 'create'
      ? /^\/(?:settings|organizations\/[A-Za-z0-9-]+\/settings)\/apps\/new$/.test(url.pathname)
      : /^\/apps\/[A-Za-z0-9-]+\/installations\/new$/.test(url.pathname)
  if (url.protocol !== 'https:' || url.hostname !== 'github.com' || url.port || url.username || url.password || !valid)
    throw new Error('Adresse de connexion GitHub invalide. Relancez l’assistant.')
  return url.href
}

export type WebLogBuffer = { text: string; cursor?: string; truncated: boolean; hasMore: boolean }
export const updateWebLogBuffer = (
  previous: WebLogBuffer,
  chunk: { text: string; cursor?: string; reset?: boolean; truncated?: boolean; hasMore?: boolean },
  requestedCursor?: string
): WebLogBuffer => {
  const text = chunk.reset || !requestedCursor || !chunk.cursor ? chunk.text : previous.text + chunk.text
  const replaced = !!chunk.reset || !requestedCursor || !chunk.cursor
  return {
    text: text.length > 200000 ? text.slice(-200000) : text,
    cursor: chunk.cursor,
    truncated: (!replaced && previous.truncated) || !!chunk.truncated || text.length > 200000,
    hasMore: !!chunk.hasMore,
  }
}
