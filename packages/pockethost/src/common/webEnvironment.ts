/** Literal dotenv editing shared by the controller and dashboard. Never executes/interpolates values. */
export const WEB_ENV_MASK = '__POCKETHOST_MASKED__'
const NEXT_EXPORT_MARKER = '# pockethost-export: nextjs'
export type WebEnvValue = { value: string; build: boolean; runtime: boolean }
export type WebEnvMap = Record<string, WebEnvValue>
export type WebEnvEntry = {
  name: string
  value: string
  preserve: boolean
  build?: boolean
  runtime?: boolean
  line: number
}
export type WebEnvPlan = {
  next: WebEnvMap
  changes: { added: string[]; updated: string[]; removed: string[]; preserved: string[] }
  requiresRebuild: boolean
}
const own = (value: object, key: string) => Object.prototype.hasOwnProperty.call(value, key)
function problem(line: number, message: string): never {
  throw new Error(`Ligne ${line} : ${message}`)
}
export function validateWebEnvName(name: string) {
  if (!/^[A-Za-z_][A-Za-z0-9_]{0,100}$/.test(name)) throw new Error('Nom de variable invalide.')
  if (['NODE_OPTIONS', 'NODE_ENV', 'HOSTNAME', 'PORT'].includes(name))
    throw new Error(`La variable ${name} est réservée au moteur d’hébergement.`)
  if (['__proto__', 'constructor', 'prototype'].includes(name)) throw new Error('Nom de variable réservé.')
}
export function parseWebDotenv(input: string): WebEnvEntry[] {
  if (typeof input !== 'string' || input.length > 200000 || input.includes('\u0000'))
    throw new Error('Fichier .env invalide ou trop volumineux (200 000 caractères maximum).')
  const text = input.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n')
  // Our Next.js export escapes every $ for dotenv-expand. This explicit marker
  // makes importing that file reversible without evaluating references.
  const nextExport = text === NEXT_EXPORT_MARKER || text.startsWith(NEXT_EXPORT_MARKER + '\n')
  const result: WebEnvEntry[] = []
  const names = new Set<string>()
  let index = 0
  let line = 1
  let flags: { build?: boolean; runtime?: boolean } | undefined
  while (index < text.length) {
    const startLine = line
    let end = text.indexOf('\n', index)
    if (end < 0) end = text.length
    const raw = text.slice(index, end)
    const trimmed = raw.trim()
    if (!trimmed || trimmed.startsWith('#')) {
      const annotation = /^#\s*(?:pockethost|coolify):\s*(.*)$/.exec(trimmed)
      if (annotation) {
        if (flags) problem(line, 'une seule directive est permise, juste avant sa variable.')
        flags = {}
        const tokens = annotation[1]!.trim().split(/\s+/).filter(Boolean)
        if (!tokens.length) problem(line, 'directive vide.')
        const seen = new Set<string>()
        for (const token of tokens) {
          const match = /^(build|buildtime|runtime|literal)(?:=(true|false))?$/.exec(token)
          if (!match) problem(line, 'directive inconnue ; utilisez build=true/false et runtime=true/false.')
          const key = match[1] === 'buildtime' ? 'build' : match[1]!
          if (seen.has(key)) problem(line, 'directive dupliquée.')
          seen.add(key)
          const enabled = match[2] !== 'false'
          if (key === 'literal') {
            if (!enabled)
              problem(line, 'les références $ restent toujours littérales ; l’interpolation est désactivée.')
          } else flags[key as 'build' | 'runtime'] = enabled
        }
      } else if (flags) problem(line, 'la directive doit être immédiatement suivie de sa variable.')
      index = end + 1
      line++
      continue
    }
    const prefix = /^[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*=[ \t]*/.exec(raw)
    if (!prefix) problem(line, 'entrée attendue au format NOM=valeur.')
    const name = prefix[1]!
    try {
      validateWebEnvName(name)
    } catch (e) {
      problem(line, e instanceof Error ? e.message : 'Nom de variable invalide.')
    }
    if (names.has(name)) problem(line, `la variable ${name} est présente plusieurs fois.`)
    names.add(name)
    if (result.length >= 100) problem(line, '100 variables maximum.')
    index += prefix[0].length
    let value = ''
    let quoted = false
    const quote = text[index]
    if (quote === '"' || quote === "'" || quote === '`') {
      quoted = true
      index++
      let closed = false
      while (index < text.length) {
        const char = text[index++]!
        if (char === quote) {
          closed = true
          break
        }
        if (char === '\n') line++
        if (char === '\\' && index < text.length) {
          const next = text[index]!
          if (quote === '"' && (next === 'n' || next === 'r')) {
            value += next === 'n' ? '\n' : '\r'
            index++
            continue
          }
        }
        value += char
      }
      if (!closed) problem(startLine, 'valeur entre guillemets non terminée.')
      while (text[index] === ' ' || text[index] === '\t') index++
      if (text[index] !== undefined && text[index] !== '\n' && text[index] !== '#')
        problem(
          line,
          'caractères inattendus après la valeur ; utilisez des guillemets pour les commentaires littéraux.'
        )
      while (index < text.length && text[index] !== '\n') index++
    } else {
      end = text.indexOf('\n', index)
      if (end < 0) end = text.length
      value = text.slice(index, end).split('#', 1)[0]!.trim()
      index = end
    }
    if (nextExport) value = value.replace(/\\\$/g, '$')
    if (value.length > 10000 || value.includes('\u0000'))
      problem(startLine, 'valeur trop longue (10 000 caractères maximum).')
    result.push({ name, value, preserve: !quoted && value === WEB_ENV_MASK, ...flags, line: startLine })
    flags = undefined
    if (text[index] === '\n') {
      index++
      line++
    }
  }
  if (flags) problem(line, 'directive sans variable.')
  return result
}
/**
 * `blind` : pour un appelant sans droit de révélation, une valeur explicite est toujours signalée « modifiée » (jamais
 * « conservée ») : le diff ne doit pas confirmer qu'une valeur devinée égale la valeur enregistrée.
 */
export function planWebEnvironment(
  current: WebEnvMap,
  entries: WebEnvEntry[],
  mode: 'merge' | 'replace',
  options: { blind?: boolean } = {}
): WebEnvPlan {
  if (!['merge', 'replace'].includes(mode)) throw new Error('Choisissez Fusionner ou Remplacer.')
  const explicit = new Set<string>()
  const next: WebEnvMap = Object.create(null)
  if (mode === 'merge') for (const name of Object.keys(current)) next[name] = { ...current[name]! }
  for (const entry of entries) {
    validateWebEnvName(entry.name)
    const before = own(current, entry.name) ? current[entry.name] : undefined
    if (entry.preserve && !before)
      throw new Error(`La variable ${entry.name} n’a aucune valeur enregistrée à conserver.`)
    if (entry.preserve) explicit.delete(entry.name)
    else explicit.add(entry.name)
    next[entry.name] = {
      value: entry.preserve ? before!.value : entry.value,
      build: entry.build ?? before?.build ?? true,
      runtime: entry.runtime ?? before?.runtime ?? true,
    }
    if (!next[entry.name]!.build && !next[entry.name]!.runtime)
      throw new Error(`La variable ${entry.name} doit être utilisée à la construction ou à l’exécution.`)
  }
  if (Object.keys(next).length > 100) throw new Error('100 variables maximum après fusion.')
  const changes: WebEnvPlan['changes'] = { added: [], updated: [], removed: [], preserved: [] }
  let requiresRebuild = false
  for (const name of [...new Set([...Object.keys(current), ...Object.keys(next)])].sort()) {
    const before = own(current, name) ? current[name] : undefined
    const after = own(next, name) ? next[name] : undefined
    const kind = !before
      ? 'added'
      : !after
        ? 'removed'
        : before.value !== after.value ||
            before.build !== after.build ||
            before.runtime !== after.runtime ||
            (options.blind && explicit.has(name))
          ? 'updated'
          : 'preserved'
    changes[kind].push(name)
    if (kind !== 'preserved' && (before?.build || after?.build)) requiresRebuild = true
  }
  // The existing encrypted field is limited to 100 KiB; leave room for encryption/base64 overhead.
  if (utf8Size(JSON.stringify(next)) > 65000)
    throw new Error('L’ensemble des variables est trop volumineux (65 Ko maximum).')
  return { next, changes, requiresRebuild }
}
function utf8Size(value: string) {
  let bytes = 0
  for (const char of value) {
    const n = char.codePointAt(0)!
    bytes += n <= 0x7f ? 1 : n <= 0x7ff ? 2 : n <= 0xffff ? 3 : 4
  }
  return bytes
}
/** Select an absent quote delimiter; target-specific expansion escaping is handled by the formatter. */
export function quoteWebEnv(value: string): string | null {
  // Local dotenv readers disagree on CR normalization and \\r decoding.
  if (value.includes('\r')) return null
  if (!value.includes("'")) return "'" + value + "'"
  if (!value.includes('`')) return '`' + value + '`'
  if (!value.includes('"') && !/\\[nr]/.test(value)) return '"' + value + '"'
  return null
}
export function formatWebDotenv(
  values: WebEnvMap,
  reveal = false,
  example = false,
  target: 'literal' | 'nextjs' = 'literal'
) {
  const warnings: string[] = []
  const lines: string[] = target === 'nextjs' ? [NEXT_EXPORT_MARKER] : []
  for (const name of Object.keys(values).sort()) {
    const item = values[name]!
    lines.push(`# pockethost: build=${item.build} runtime=${item.runtime}`)
    let value = example ? "''" : WEB_ENV_MASK
    if (reveal && !example) {
      const quoted = quoteWebEnv(target === 'nextjs' ? item.value.replace(/\$/g, '\\$') : item.value)
      if (quoted === null) warnings.push(name)
      else value = quoted
    }
    lines.push(`${name}=${value}`)
  }
  return { text: lines.join('\n') + (lines.length ? '\n' : ''), warnings }
}
