import { parseEnv } from 'node:util'
import { describe, expect, it } from 'vitest'
import { formatWebDotenv, parseWebDotenv, planWebEnvironment, WEB_ENV_MASK } from './webEnvironment'
describe('literal web environment dotenv editor', () => {
  it('supports BOM, export, comments, equals, hashes, dollars and quoted multiline', () => {
    const values = parseWebDotenv(
      "\uFEFF# config\r\nexport API=\"https://x.invalid/?a=b#c\" # comment\nPASS=abc=xyz # ignored\nLITERAL='${TOKEN} $HOME $(command)'\nKEY='first\nsecond'\nEMPTY=\n"
    )
    expect(values.map((x) => [x.name, x.value])).toEqual([
      ['API', 'https://x.invalid/?a=b#c'],
      ['PASS', 'abc=xyz'],
      ['LITERAL', '${TOKEN} $HOME $(command)'],
      ['KEY', 'first\nsecond'],
      ['EMPTY', ''],
    ])
  })
  it('preserves unquoted masks, never stores masks for new variables, permits quoted literal marker', () => {
    const current = { KEY: { value: 'private-secret', build: false, runtime: true } }
    expect(planWebEnvironment(current, parseWebDotenv('KEY=' + WEB_ENV_MASK), 'replace').next.KEY).toEqual(current.KEY)
    expect(() => planWebEnvironment({}, parseWebDotenv('KEY=' + WEB_ENV_MASK), 'merge')).toThrow('aucune valeur')
    expect(planWebEnvironment(current, parseWebDotenv("KEY='" + WEB_ENV_MASK + "'"), 'merge').next.KEY!.value).toBe(
      WEB_ENV_MASK
    )
  })
  it('blind plans never confirm that a guessed value equals the stored secret', () => {
    const current = { DB_PASSWORD: { value: 'hunter2', build: false, runtime: true } }
    const plan = (guess: string, blind: boolean) =>
      planWebEnvironment(current, parseWebDotenv(`DB_PASSWORD='${guess}'`), 'merge', { blind })
    expect(plan('hunter2', false).changes.preserved).toEqual(['DB_PASSWORD'])
    for (const guess of ['hunter2', 'wrong'])
      expect({ changes: plan(guess, true).changes, rebuild: plan(guess, true).requiresRebuild }).toEqual({
        changes: { added: [], updated: ['DB_PASSWORD'], removed: [], preserved: [] },
        rebuild: false,
      })
    expect(plan('hunter2', true).next.DB_PASSWORD!.value).toBe('hunter2')
    // A masked (preserved) entry stays « conservée » : it reveals nothing.
    expect(
      planWebEnvironment(current, parseWebDotenv('DB_PASSWORD=' + WEB_ENV_MASK), 'merge', { blind: true }).changes
        .preserved
    ).toEqual(['DB_PASSWORD'])
  })
  it('preserves scope absent annotations and uses explicit independent scopes', () => {
    const current = { KEY: { value: 'secret', build: false, runtime: true } }
    expect(planWebEnvironment(current, parseWebDotenv("KEY='next'\nNEW=local"), 'merge').next).toMatchObject({
      KEY: { build: false, runtime: true },
      NEW: { build: true, runtime: true },
    })
    expect(parseWebDotenv('# coolify: literal buildtime=false runtime=true\nKEY=value')[0]).toMatchObject({
      build: false,
      runtime: true,
    })
    expect(() => parseWebDotenv('# pockethost: build=true\n\nKEY=value')).toThrow('immédiatement')
    expect(() => parseWebDotenv('# coolify: literal=false\nKEY=value')).toThrow('interpolation')
  })
  it('merge keeps absent keys, replace reports removals and explicit empty differs from mask', () => {
    const current = { A: { value: 'a', build: true, runtime: true }, B: { value: 'b', build: false, runtime: true } }
    const merge = planWebEnvironment(current, parseWebDotenv('B='), 'merge')
    expect(merge.next.A!.value).toBe('a')
    expect(merge.next.B!.value).toBe('')
    expect(merge.requiresRebuild).toBe(false)
    const replace = planWebEnvironment(current, parseWebDotenv('B='), 'replace')
    expect(replace.changes.removed).toEqual(['A'])
    expect(replace.requiresRebuild).toBe(true)
  })
  it('rejects ambiguous duplicate/reserved/malformed input without echoing values', () => {
    for (const text of [
      'A=secret\nA=secret2',
      'NODE_OPTIONS=private',
      'bad.name=private',
      'A="private',
      'A=ok\n# pockethost: unknown=true\nB=private',
      'A=ok\u0000private',
    ])
      expect(() => parseWebDotenv(text)).toThrow()
    try {
      parseWebDotenv('bad.name=private-value')
    } catch (e) {
      expect(String(e)).not.toContain('private-value')
    }
    expect(() => parseWebDotenv(Array.from({ length: 101 }, (_, i) => `VAR_${i}=x`).join('\n'))).toThrow(
      '100 variables'
    )
  })
  it('exports typical local values losslessly to both our parser and Node native dotenv', () => {
    for (const value of [
      '',
      'a=b#c',
      '$HOME ${TOKEN} $(cmd)',
      'line1\nline2',
      'C:\\new\\folder\\',
      'a\'b"c',
      'one\\n two',
    ]) {
      const env = { KEY: { value, build: false, runtime: true } },
        text = formatWebDotenv(env, true)
      expect(text.warnings).toEqual([])
      expect(parseWebDotenv(text.text)[0]!.value).toBe(value)
      expect(parseEnv(text.text).KEY).toBe(value)
    }
  })
  it('flags values impossible to export safely and never reveals in masked/example output', () => {
    const env = { SECRET: { value: 'all\'"`quotes', build: true, runtime: true } }
    expect(formatWebDotenv(env, true).warnings).toEqual(['SECRET'])
    expect(formatWebDotenv({ CR: { value: 'line1\r\nline2', build: false, runtime: true } }, true).warnings).toEqual([
      'CR',
    ])
    expect(formatWebDotenv(env).text).not.toContain('quotes')
    expect(formatWebDotenv(env, false, true).text).toContain("SECRET=''")
  })
  it('exports explicitly for Next.js dotenv-expand without changing the literal editor or reimport', () => {
    const value = '$HOME ${TOKEN} $(cmd) \\$HOME \\\\$TOKEN $$ tail$'
    const values = { SECRET: { value, build: false, runtime: true } }
    const literal = formatWebDotenv(values, true)
    const next = formatWebDotenv(values, true, false, 'nextjs')
    expect(literal.text).toContain(value)
    expect(next.text).toMatch(/^# pockethost-export: nextjs\n/)
    expect(next.text).toContain(value.replace(/\$/g, '\\$'))
    expect(parseWebDotenv(next.text)[0]!.value).toBe(value)
    expect(formatWebDotenv(values, false, false, 'nextjs').text).not.toContain('$HOME')
  })
})
