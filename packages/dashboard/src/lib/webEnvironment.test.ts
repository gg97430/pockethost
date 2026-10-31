import { describe, expect, it } from 'vitest'
import {
  environmentAccessDenied,
  environmentDraftRows,
  environmentRowsFromText,
  environmentRowsPayload,
  environmentRowsText,
  localEnvironmentImport,
  mergeRevealedEnvironmentRows,
  planWebEnvironment,
  publicEnvironmentResponse,
  type EnvironmentResponse,
} from './webEnvironment'

const response = (revealed = true): EnvironmentResponse => ({
  revision: 'revision-1',
  revealed,
  variables: [
    { name: 'API_KEY', build: false, runtime: true, hasValue: true, ...(revealed ? { value: 'saved-secret' } : {}) },
    {
      name: 'BASE_URL',
      build: true,
      runtime: false,
      hasValue: true,
      ...(revealed ? { value: 'https://example.test/$LITERAL' } : {}),
    },
  ],
  dotenv: 'API_KEY=__POCKETHOST_MASKED__\n',
  nextDotenv: 'INHERITED_SECRET=must-not-be-edited\n',
  example: "API_KEY=''\n",
  exportWarnings: [],
  nextExportWarnings: [],
  pendingChanges: true,
  requiresRebuild: false,
  runtimeOnlyChanges: true,
  canApplyRuntime: true,
})

describe('environment editor drafts', () => {
  it('identifies access revocation without discarding drafts for network, server or revision errors', () => {
    for (const status of [401, 403, 404]) expect(environmentAccessDenied({ status })).toBe(true)
    for (const problem of [
      { status: 0 },
      { status: 400 },
      { status: 409 },
      { status: 500 },
      new Error('offline'),
      null,
    ])
      expect(environmentAccessDenied(problem)).toBe(false)
  })
  it('builds the real compact local file from revealed values and preserves existing scopes without noise', () => {
    const value = response(),
      rows = environmentDraftRows(value)
    const text = environmentRowsText(rows, { compact: true, baseline: value.variables })
    expect(text).toBe("API_KEY='saved-secret'\nBASE_URL='https://example.test/$LITERAL'\n")
    expect(text).not.toContain('INHERITED_SECRET')
    const roundtrip = environmentRowsFromText(text, rows, 'replace')
    expect(roundtrip.map(({ name, build, runtime, value }) => ({ name, build, runtime, value }))).toEqual(
      rows.map(({ name, build, runtime, value }) => ({ name, build, runtime, value }))
    )
  })

  it('writes a directive only for changed scopes or a nondefault new variable', () => {
    const value = response(),
      rows = environmentDraftRows(value)
    rows[0]!.build = true
    rows.push({ name: 'NEW_FLAG', value: 'yes', build: true, runtime: true })
    rows.push({ name: 'RUNTIME_ONLY', value: 'private', build: false, runtime: true })
    const text = environmentRowsText(rows, { compact: true, baseline: value.variables })
    expect(text).toContain("# pockethost: build=true runtime=true\nAPI_KEY='saved-secret'")
    expect(text).toContain("NEW_FLAG='yes'\n# pockethost: build=false runtime=true\nRUNTIME_ONLY='private'")
    expect(text.match(/# pockethost:/g)).toHaveLength(2)
  })

  it('fills hidden values on reveal while retaining replacements, deletions and scope edits', () => {
    const draft = environmentDraftRows(response(false))
    draft[0] = { ...draft[0]!, value: 'unsaved-replacement', build: true }
    draft[1] = { ...draft[1]!, remove: true }
    draft.push({ name: 'NEW', value: 'unsaved-new', build: true, runtime: true })
    const merged = mergeRevealedEnvironmentRows(draft, response())
    expect(merged[0]).toMatchObject({ value: 'unsaved-replacement', build: true, runtime: true, valueKnown: true })
    expect(merged[1]).toMatchObject({ value: 'https://example.test/$LITERAL', remove: true, runtime: false })
    expect(merged[2]).toMatchObject({ value: 'unsaved-new' })
    expect(mergeRevealedEnvironmentRows(environmentDraftRows(response(false)), response())[0]!.value).toBe(
      'saved-secret'
    )
  })

  it('distinguishes an erased revealed value from an untouched hidden value', () => {
    const hidden = environmentDraftRows(response(false)),
      shown = environmentDraftRows(response())
    shown[0]!.value = ''
    expect(environmentRowsPayload(hidden)[0]).toMatchObject({ value: '', setEmpty: false })
    expect(environmentRowsPayload(shown)[0]).toMatchObject({ value: '', setEmpty: true })
    hidden[0]!.setEmpty = true
    expect(mergeRevealedEnvironmentRows(hidden, response())[0]).toMatchObject({ value: '', setEmpty: true })
  })

  it('keeps the advanced explicit-empty control effective while hidden and restores preserve on uncheck', () => {
    const rows = environmentDraftRows(response(false))
    const current = { API_KEY: { value: 'saved-secret', build: false, runtime: true } }
    const plan = () => {
      const item = environmentRowsPayload(rows)[0]!
      return planWebEnvironment(current, [{ ...item, preserve: !item.setEmpty && !item.value, line: 1 }], 'merge')
    }
    rows[0]!.setEmpty = true
    expect(plan().next.API_KEY!.value).toBe('')
    rows[0]!.setEmpty = false
    expect(plan().next.API_KEY!.value).toBe('saved-secret')
  })

  it('preserves raw edits and missing names across view changes with replace or merge semantics', () => {
    const current = environmentDraftRows(response())
    const raw = "# comment entered by user\nAPI_KEY='edited-value'\nNEW_VALUE=''\n"
    const replaced = environmentRowsFromText(raw, current, 'replace')
    expect(replaced.find((row) => row.name === 'API_KEY')).toMatchObject({
      value: 'edited-value',
      build: false,
      runtime: true,
    })
    expect(replaced.find((row) => row.name === 'NEW_VALUE')).toMatchObject({
      value: '',
      setEmpty: true,
      hasValue: false,
    })
    expect(replaced.find((row) => row.name === 'BASE_URL')).toMatchObject({ remove: true })
    const merged = environmentRowsFromText(raw, current, 'merge')
    expect(merged.find((row) => row.name === 'BASE_URL')).toMatchObject({ remove: false, value: current[1]!.value })
    expect(current[0]!.value).toBe('saved-secret')
  })

  it('interprets masks, omitted scopes and merge omissions against the saved app baseline, matching the API', () => {
    const saved = response(),
      drafts = environmentDraftRows(saved)
    drafts[0] = { ...drafts[0]!, value: 'unsaved-new-secret', build: true }
    drafts[1] = { ...drafts[1]!, value: 'unsaved-url', runtime: true }
    drafts.push({ name: 'UNSAVED_NEW', value: 'not-yet-saved', build: true, runtime: true, hasValue: false })
    const text = 'API_KEY=__POCKETHOST_MASKED__\n'
    const switched = environmentRowsFromText(text, drafts, 'merge', saved.variables)
    const direct = planWebEnvironment(
      {
        API_KEY: { value: 'saved-secret', build: false, runtime: true },
        BASE_URL: { value: saved.variables[1]!.value!, build: true, runtime: false },
      },
      [{ name: 'API_KEY', value: '', preserve: true, line: 1 }],
      'merge'
    )
    expect(switched.find((row) => row.name === 'API_KEY')).toMatchObject({ ...direct.next.API_KEY, hasValue: true })
    expect(switched.find((row) => row.name === 'BASE_URL')).toMatchObject({ ...direct.next.BASE_URL, hasValue: true })
    expect(switched.some((row) => row.name === 'UNSAVED_NEW')).toBe(false)
    const explicit = environmentRowsFromText("API_KEY='unsaved-new-secret'\n", drafts, 'replace', saved.variables)
    expect(explicit.find((row) => row.name === 'API_KEY')).toMatchObject({
      value: 'unsaved-new-secret',
      build: false,
      runtime: true,
    })
    expect(explicit.find((row) => row.name === 'BASE_URL')).toMatchObject({ remove: true, hasValue: true })
  })

  it('keeps unusual values in structured fields and refuses a misleading masked raw file', () => {
    const value = response()
    value.variables[0]!.value = 'a\'b`c"d'
    value.exportWarnings = ['API_KEY']
    const rows = environmentDraftRows(value)
    expect(rows[0]!.value).toBe('a\'b`c"d')
    expect(() => environmentRowsText(rows, { compact: true, baseline: value.variables })).toThrow('API_KEY')
    expect(() =>
      environmentDraftRows({ ...value, variables: [{ name: 'MISSING', build: true, runtime: true }] })
    ).toThrow('MISSING')
  })

  it('never passes revealed values or either plaintext file to the parent callback', () => {
    const safe = publicEnvironmentResponse(response())
    expect(safe.revealed).toBe(false)
    expect(safe.variables[0]).not.toHaveProperty('value')
    expect([safe.dotenv, safe.nextDotenv, safe.example]).toEqual(['', '', ''])
    expect(JSON.stringify(safe)).not.toContain('saved-secret')
    expect(JSON.stringify(safe)).not.toContain('must-not-be-edited')
    expect(safe.canApplyRuntime).toBe(true)
  })

  it('retains existing creation-mode import and annotated export compatibility', () => {
    const imported = localEnvironmentImport(
      "# pockethost: build=false runtime=true\nTOKEN='created-secret'\n",
      [],
      'replace'
    )
    expect(imported.rows[0]).toMatchObject({
      name: 'TOKEN',
      value: 'created-secret',
      build: false,
      runtime: true,
      hasValue: false,
    })
    expect(environmentRowsText(imported.rows)).toBe("# pockethost: build=false runtime=true\nTOKEN='created-secret'\n")
  })
})
