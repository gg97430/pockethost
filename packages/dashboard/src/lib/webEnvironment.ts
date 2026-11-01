import {
  formatWebDotenv,
  parseWebDotenv,
  planWebEnvironment,
  quoteWebEnv,
  WEB_ENV_MASK,
  type WebEnvMap,
} from '../../../pockethost/src/common/webEnvironment'
import type { WebAppEnvironmentDraft } from './webApps'
export { formatWebDotenv, parseWebDotenv, planWebEnvironment, WEB_ENV_MASK }
export type EnvironmentRow = WebAppEnvironmentDraft & { setEmpty?: boolean; multiline?: boolean; isEmpty?: boolean }
/** Values fetched for editing stay inside the editor, never in its parent's bound rows. */
export type EnvironmentDraftRow = EnvironmentRow & { valueKnown?: boolean }
export function environmentAccessDenied(problem: unknown) {
  if (!problem || typeof problem !== 'object') return false
  return [401, 403, 404].includes(Number((problem as { status?: unknown }).status))
}
export type EnvironmentResponse = {
  revision: string
  revealed: boolean
  variables: (EnvironmentRow & { value?: string })[]
  dotenv: string
  nextDotenv?: string
  nextExportWarnings?: string[]
  example: string
  exportWarnings: string[]
  pendingChanges: boolean
  requiresRebuild: boolean
  runtimeOnlyChanges: boolean
  canApplyRuntime: boolean
}
export function nextLocalEnvironment(value: EnvironmentResponse) {
  if (typeof value.nextDotenv === 'string') return { text: value.nextDotenv, warnings: value.nextExportWarnings || [] }
  const values: WebEnvMap = Object.create(null)
  for (const item of value.variables) {
    if (value.revealed && typeof item.value !== 'string') return { text: '', warnings: [item.name] }
    values[item.name] = { value: item.value || '', build: item.build, runtime: item.runtime }
  }
  return formatWebDotenv(values, value.revealed, false, 'nextjs')
}
export type EnvironmentPreview = {
  revision: string
  previewToken: string
  changes: { added: string[]; updated: string[]; removed: string[]; preserved: string[] }
  requiresRebuild: boolean
  total: number
}
export function environmentRowsText(
  rows: EnvironmentRow[],
  options: { compact?: boolean; baseline?: EnvironmentRow[] } = {}
) {
  const lines: string[] = []
  const names = new Set<string>()
  for (const row of rows) {
    if (row.remove) continue
    if (!row.name.trim()) throw new Error('Complétez ou retirez les variables sans nom avant de changer de vue.')
    const name = row.name.trim()
    if (names.has(name)) throw new Error(`La variable ${name} est présente plusieurs fois.`)
    names.add(name)
    const preserve = row.hasValue && !row.setEmpty && !row.value
    const value = row.setEmpty ? '' : row.value || ''
    const quoted = preserve ? WEB_ENV_MASK : quoteWebEnv(value)
    if (quoted === null)
      throw new Error(
        `La variable ${name} ne peut pas être exportée dans un .env portable : modifiez-la dans la vue normale.`
      )
    const before = options.baseline?.find((item) => item.name === name)
    if (!options.compact || row.build !== (before?.build ?? true) || row.runtime !== (before?.runtime ?? true))
      lines.push(`# pockethost: build=${row.build} runtime=${row.runtime}`)
    lines.push(`${name}=${quoted}`)
  }
  return lines.join('\n') + (lines.length ? '\n' : '')
}
export function environmentDraftRows(value: EnvironmentResponse): EnvironmentDraftRow[] {
  return value.variables.map((item) => {
    if (value.revealed && typeof item.value !== 'string') throw new Error(`Valeur indisponible pour ${item.name}.`)
    return {
      name: item.name,
      build: item.build,
      runtime: item.runtime,
      hasValue: true,
      isEmpty: item.isEmpty,
      multiline: item.multiline,
      value: value.revealed ? item.value! : '',
      valueKnown: value.revealed,
      setEmpty: value.revealed && item.value === '',
    }
  })
}
/** Reveal fills only unknown values. Draft replacements, removals and scope changes remain intact. */
export function mergeRevealedEnvironmentRows(rows: EnvironmentDraftRow[], value: EnvironmentResponse) {
  if (!value.revealed) throw new Error('Les valeurs ne sont pas révélées.')
  const saved = environmentDraftRows(value)
  return rows.map((row) => {
    const before = row.hasValue && saved.find((item) => item.name === row.name)
    if (!before) return { ...row }
    const keep = row.valueKnown || row.setEmpty || !!row.value
    return {
      ...row,
      value: keep ? row.value || '' : before.value,
      valueKnown: true,
      setEmpty: keep ? !!row.setEmpty || row.value === '' : before.setEmpty,
      multiline: row.multiline || !!before.value?.includes('\n'),
    }
  })
}
export function environmentRowsPayload(rows: EnvironmentDraftRow[]) {
  return rows.map((row) => ({
    name: row.name.trim(),
    value: row.setEmpty ? '' : row.value || '',
    build: row.build,
    runtime: row.runtime,
    remove: !!row.remove,
    setEmpty: !!row.setEmpty || (!!row.valueKnown && !row.value),
  }))
}
/** Switching back to rows applies the current file locally; it never calls the API or drops unsaved edits. */
export function environmentRowsFromText(
  text: string,
  rows: EnvironmentDraftRow[],
  mode: 'merge' | 'replace',
  baseline: EnvironmentRow[] = rows
): EnvironmentDraftRow[] {
  const current: WebEnvMap = Object.create(null)
  // Existing apps use the persisted local snapshot, matching the API's mask/merge/scope semantics.
  // Creation drafts have no server baseline and continue using their own rows.
  for (const row of baseline)
    if (row.name) current[row.name] = { value: row.value || '', build: row.build, runtime: row.runtime }
  const entries = parseWebDotenv(text),
    plan = planWebEnvironment(current, entries, mode)
  const next: EnvironmentDraftRow[] = Object.keys(plan.next).map((name) => {
    const draft = rows.find((row) => row.name === name),
      saved = baseline.find((row) => row.name === name),
      before = draft || saved,
      item = plan.next[name]!
    const explicit = entries.find((entry) => entry.name === name)
    return {
      ...before,
      name,
      ...item,
      remove: false,
      hasValue: !!before?.hasValue,
      multiline: !!before?.multiline || item.value.includes('\n'),
      setEmpty: item.value === '' && (explicit ? !explicit.preserve : !!before?.setEmpty),
      valueKnown:
        explicit && !explicit.preserve
          ? true
          : (baseline !== rows && typeof saved?.value === 'string') || draft?.valueKnown,
    }
  })
  for (const row of rows) if (row.hasValue && !Object.hasOwn(plan.next, row.name)) next.push({ ...row, remove: true })
  return next
}
/** Parent callbacks need deployment state, never plaintext from a reveal response. */
export function publicEnvironmentResponse(value: EnvironmentResponse): EnvironmentResponse {
  return {
    revision: value.revision,
    revealed: false,
    variables: value.variables.map(({ name, build, runtime, hasValue, isEmpty, multiline }) => ({
      name,
      build,
      runtime,
      hasValue,
      isEmpty,
      multiline,
    })),
    dotenv: '',
    nextDotenv: '',
    example: '',
    exportWarnings: value.exportWarnings,
    nextExportWarnings: value.nextExportWarnings,
    pendingChanges: value.pendingChanges,
    requiresRebuild: value.requiresRebuild,
    runtimeOnlyChanges: value.runtimeOnlyChanges,
    canApplyRuntime: value.canApplyRuntime,
  }
}
export function localEnvironmentImport(text: string, rows: EnvironmentRow[], mode: 'merge' | 'replace') {
  const current: WebEnvMap = Object.create(null)
  for (const row of rows)
    if (row.name && !row.remove) current[row.name] = { value: row.value || '', build: row.build, runtime: row.runtime }
  const plan = planWebEnvironment(current, parseWebDotenv(text), mode)
  return {
    ...plan,
    rows: Object.keys(plan.next)
      .sort()
      .map((name) => ({ name, ...plan.next[name]!, setEmpty: plan.next[name]!.value === '', hasValue: false })),
  }
}
