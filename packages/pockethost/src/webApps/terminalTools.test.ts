import { describe, expect, it } from 'vitest'
import type { ContainerInfo } from './terminalBroker'
import {
  terminalCleanupScript,
  terminalCleanupTargets,
  terminalLoaderEnvironment,
  trustedTerminalNode,
} from './terminalTools'

const image = `sha256:${'a'.repeat(64)}`
const name = `ph-web-tools-${'a'.repeat(20)}`
const info = () =>
  ({
    Config: { Env: [], Labels: { 'pockethost.web.tooling': name } },
    Mounts: [{ Type: 'volume', Name: name, Destination: '/__pockethost_tools', RW: false }],
  }) as unknown as ContainerInfo

describe('trusted terminal tooling', () => {
  it('uses the approved immutable volume and its own loader', () => {
    const result = trustedTerminalNode(info(), image)
    expect(result.portable).toBe(true)
    expect(result.command).toEqual([
      '/__pockethost_tools/ld.so',
      '--inhibit-cache',
      '--library-path',
      '/__pockethost_tools/lib',
      '/__pockethost_tools/node',
    ])
  })
  it('rejects writable, bind, mismatched and unlabeled tools', () => {
    for (const patch of [{ RW: true }, { Type: 'bind' }, { Name: 'other' }]) {
      const value = info()
      Object.assign(value.Mounts![0]!, patch)
      expect(() => trustedTerminalNode(value, image)).toThrow('Untrusted')
    }
    const value = info()
    value.Config.Labels = {}
    expect(() => trustedTerminalNode(value, image)).toThrow('Untrusted')
    expect(() => trustedTerminalNode(info(), 'node:latest')).toThrow('Unconfigured')
    const nested = info()
    nested.Mounts!.push({ Type: 'volume', Name: 'untrusted', Destination: '/__pockethost_tools/lib', RW: true })
    expect(() => trustedTerminalNode(nested, image)).toThrow('Untrusted')
  })
  it('keeps legacy containers usable without trusting a spoofed mount', () => {
    const value = info()
    value.Mounts = []
    expect(trustedTerminalNode(value, '')).toEqual({ command: ['/usr/local/bin/node'], portable: false })
  })
  it('neutralizes all process loader inputs before running the supervisor', () => {
    const value = info()
    value.Config.Env = [
      'LD_PRELOAD=/data/evil.so',
      'LD_AUDIT=/data/audit.so',
      'LD_CUSTOM=bad',
      'DYLD_INSERT_LIBRARIES=bad',
      'NODE_OPTIONS=--require=/data/evil.js',
      'NODE_PATH=/data',
      'OPENSSL_CONF=/data/conf',
      'GLIBC_TUNABLES=bad',
      'APP_VALUE=preserved',
    ]
    const result = terminalLoaderEnvironment(value)
    for (const entry of value.Config.Env.slice(0, -1)) {
      const at = entry.indexOf('='),
        key = entry.slice(0, at)
      expect(result.neutral).toContain(`${key}=`)
      expect(result.original[key]).toBe(entry.slice(at + 1))
    }
    expect(result.original.APP_VALUE).toBeUndefined()
  })
  it('kills terminal processes that dropped the marker or detached, never the site or other executions', () => {
    const since = 1_000_000,
      before = since - 60_000,
      after = since + 5_000
    const row = (pid: number, ppid: number, sid: number, start: number, ours = false) => ({
      pid,
      ppid,
      sid,
      start,
      ours,
    })
    const rows = [
      row(1, 0, 1, before),
      row(2, 1, 1, after), // site PID 1 and a worker it forked during the session
      row(7, 1, 7, before),
      row(8, 7, 7, after), // dumb-init style site session: new children keep its session
      row(100, 0, 100, after, true),
      row(101, 100, 100, after, true), // our supervisor and shell
      row(102, 101, 100, after), // child of our shell that dropped the marker (env -u)
      row(120, 1, 120, after),
      row(121, 120, 120, after), // env -u … setsid daemon reparented to PID 1, and its child
      row(130, 1, 100, after), // double fork without setsid: orphan keeps our (new) session
      row(200, 0, 200, after),
      row(201, 200, 200, after), // scheduled task or another terminal: another exec tree
      row(300, 1, 300, before), // detached before this session: not ours to kill
      row(400, 0, 400, after), // the cleanup helper itself
    ]
    expect(terminalCleanupTargets(rows, 400, since).sort((a, b) => a - b)).toEqual([100, 101, 102, 120, 121, 130])
    // Unknown boot time (start 0): only marked processes and their descendants.
    expect(
      terminalCleanupTargets(
        rows.map((r) => ({ ...r, start: 0 })),
        400,
        since
      ).sort((a, b) => a - b)
    ).toEqual([100, 101, 102])
    expect(() => new Function(terminalCleanupScript('a'.repeat(64), since))).not.toThrow()
  })
})
