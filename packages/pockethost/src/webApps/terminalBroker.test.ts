import { mkdtempSync, realpathSync, rmSync } from 'node:fs'
import { createServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { Duplex } from 'node:stream'
import { afterEach, describe, expect, it } from 'vitest'
import WebSocket from 'ws'
import {
  createTerminalBroker,
  DockerTerminalEngine,
  TERMINAL_PROTOCOL,
  terminalControl,
  validPublishedContainer,
  type ContainerInfo,
  type TerminalGrant,
} from './terminalBroker'
const app = 'a'.repeat(15),
  deployment = 'b'.repeat(15),
  actor = 'c'.repeat(15),
  sessionId = 'd'.repeat(15),
  containerId = '1'.repeat(64)
const token = 't'.repeat(80)
const grant = (): TerminalGrant => ({
  appId: app,
  deploymentId: deployment,
  actorId: actor,
  sessionId,
  containerName: `ph-web-${app}-${deployment}`,
  shell: '/bin/sh',
  proof: 'p'.repeat(80),
  absoluteExpiresAt: new Date(Date.now() + 60000).toISOString(),
})
const info = (): ContainerInfo => ({
  Id: containerId,
  State: { Running: true },
  Config: {
    User: '10001:10001',
    Labels: { 'pockethost.web': 'true', 'pockethost.web.app': app, 'pockethost.web.deployment': deployment },
  },
  HostConfig: { Privileged: false, ReadonlyRootfs: true, Binds: [] },
})
const cleanups: (() => Promise<void>)[] = []
afterEach(async () => {
  for (const cleanup of cleanups.splice(0)) await cleanup()
})
const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))
async function waitFor(check: () => boolean) {
  for (let i = 0; i < 100; i++) {
    if (check()) return
    await delay(10)
  }
  throw new Error('Expected terminal event')
}
async function fixture(options: { interval?: number; idle?: number; max?: number; claimBarrier?: Promise<void> } = {}) {
  let claimed = false,
    validated = true,
    claims = 0,
    closed = 0,
    cleaned = 0,
    current = info()
  const resize: number[][] = [],
    input: Buffer[] = []
  const stream = new Duplex({
    read() {},
    write(chunk, _encoding, done) {
      input.push(Buffer.from(chunk))
      this.push(chunk)
      done()
    },
  })
  const broker = createTerminalBroker({
    origin: 'https://app.monappli.re',
    validationIntervalMs: options.interval || 1000,
    idleTimeoutMs: options.idle || 60000,
    maxDurationMs: options.max || 60000,
    control: {
      async claim(value) {
        claims++
        await options.claimBarrier
        if (claimed || value !== token) throw new Error('Invalid grant')
        claimed = true
        return grant()
      },
      async validate() {
        return { valid: validated }
      },
      async close() {
        closed++
        return {}
      },
    },
    engine: {
      async inspect() {
        return current
      },
      async createExec() {
        return 'e'.repeat(64)
      },
      async attach() {
        return stream
      },
      async resize(_id, cols, rows) {
        resize.push([cols, rows])
        return {}
      },
      async cleanup() {
        cleaned++
      },
    },
  })
  await new Promise<void>((resolve) => broker.server.listen(0, '127.0.0.1', resolve))
  const port = (broker.server.address() as any).port
  cleanups.push(() => broker.close())
  const connect = (
    origin = 'https://app.monappli.re',
    protocols = [TERMINAL_PROTOCOL, 'ph-web-grant.' + token],
    path = '/api/web-terminal/connect'
  ) => new WebSocket(`ws://127.0.0.1:${port}${path}`, protocols, { origin })
  return {
    broker,
    connect,
    resize,
    input,
    stream,
    counts: () => ({ claims, closed, cleaned }),
    revoke: () => {
      validated = false
    },
    replace: () => {
      current = { ...current, Id: '2'.repeat(64) }
    },
    badLabels: () => {
      current.Config.Labels!['pockethost.web.app'] = 'f'.repeat(15)
    },
  }
}
function rejected(ws: WebSocket): Promise<number> {
  return new Promise((resolve, reject) => {
    ws.on('unexpected-response', (_request, response) => {
      response.resume()
      ws.terminate()
      resolve(response.statusCode!)
    })
    ws.on('open', () => {
      ws.close()
      reject(new Error('Unexpected terminal connection'))
    })
    ws.on('error', () => {})
  })
}
function ready(ws: WebSocket): Promise<void> {
  return new Promise((resolve, reject) => {
    ws.on('message', (chunk, binary) => {
      if (!binary && JSON.parse(chunk.toString()).type === 'ready') resolve()
    })
    ws.on('error', reject)
  })
}
describe('web terminal broker boundaries', () => {
  it('refuses origin/path/subprotocol mismatches before claiming any grant', async () => {
    const f = await fixture()
    expect(await rejected(f.connect('https://evil.example'))).toBe(403)
    expect(await rejected(f.connect(undefined, [TERMINAL_PROTOCOL]))).toBe(401)
    expect(await rejected(f.connect(undefined, undefined, '/api/web-terminal/connect?token=secret'))).toBe(403)
    expect(f.counts().claims).toBe(0)
  })
  it('provides interactive raw-byte output, input and resize, then cleans the exec on disconnect', async () => {
    const f = await fixture(),
      ws = f.connect(),
      outputs: Buffer[] = []
    ws.on('message', (chunk, binary) => {
      if (binary) outputs.push(Buffer.from(chunk as any))
    })
    await ready(ws)
    expect(ws.protocol).toBe(TERMINAL_PROTOCOL)
    ws.send(JSON.stringify({ type: 'resize', cols: 132, rows: 41 }))
    ws.send(JSON.stringify({ type: 'input', data: 'é\r' }))
    await waitFor(() => outputs.length > 0)
    expect(Buffer.concat(outputs).toString()).toBe('é\r')
    expect(f.resize).toContainEqual([132, 41])
    expect(Buffer.concat(f.input).toString()).toBe('é\r')
    ws.send(JSON.stringify({ type: 'close' }))
    await waitFor(() => f.counts().cleaned === 1)
    expect(f.counts().closed).toBe(1)
  })
  it('sends bounded output frames for large terminal chunks', async () => {
    const f = await fixture(),
      ws = f.connect(),
      lengths: number[] = []
    ws.on('message', (value, binary) => {
      if (binary) lengths.push(Buffer.byteLength(value as any))
    })
    await ready(ws)
    f.stream.push(Buffer.alloc(70000, 65))
    await waitFor(() => lengths.reduce((sum, n) => sum + n, 0) === 70000)
    expect(Math.max(...lengths)).toBeLessThanOrEqual(16384)
    ws.close()
  })
  it('does not replay a consumed grant', async () => {
    const f = await fixture(),
      first = f.connect()
    await ready(first)
    expect(await rejected(f.connect())).toBe(401)
    first.close()
  })
  it('rejects container labels of another application', async () => {
    const f = await fixture()
    f.badLabels()
    expect(await rejected(f.connect())).toBe(401)
    expect(f.counts().closed).toBe(1)
  })
  it('closes when the actor is revoked', async () => {
    const f = await fixture({ interval: 20 }),
      ws = f.connect()
    await ready(ws)
    f.revoke()
    await waitFor(() => f.counts().cleaned === 1)
    expect(f.broker.sessionCount()).toBe(0)
  })
  it('closes when the published container changes', async () => {
    const f = await fixture({ interval: 20 }),
      ws = f.connect()
    await ready(ws)
    f.replace()
    await waitFor(() => f.counts().cleaned === 1)
  })
  it('closes idle and absolute-expired terminals', async () => {
    for (const options of [
      { interval: 20, idle: 30 },
      { interval: 20, max: 30 },
    ]) {
      const f = await fixture(options),
        ws = f.connect()
      await ready(ws)
      await waitFor(() => f.counts().cleaned === 1)
    }
  })
  it('rejects invalid client frames without invoking another command or user', async () => {
    const f = await fixture(),
      ws = f.connect()
    await ready(ws)
    ws.send(JSON.stringify({ type: 'exec', container: 'mothership', command: 'whoami', user: 'root' }))
    await waitFor(() => f.counts().cleaned === 1)
    expect(f.input.length).toBe(0)
  })
  it('closes malformed JSON values without crashing the broker', async () => {
    const f = await fixture(),
      ws = f.connect()
    await ready(ws)
    ws.send('null')
    await waitFor(() => f.counts().cleaned === 1)
    expect(f.broker.sessionCount()).toBe(0)
  })
  it('bounds queued output when a terminal stops consuming', async () => {
    const f = await fixture(),
      ws = f.connect()
    await ready(ws)
    ;(ws as any)._socket.pause()
    f.stream.push(Buffer.alloc(8 * 1024 * 1024, 65))
    await waitFor(() => f.counts().cleaned === 1)
    ws.terminate()
    await waitFor(() => f.broker.sessionCount() === 0)
  })
  it('revokes a claim still pending when the broker shuts down', async () => {
    let release!: () => void
    const barrier = new Promise<void>((resolve) => (release = resolve))
    const f = await fixture({ claimBarrier: barrier }),
      ws = f.connect()
    ws.on('error', () => {})
    await waitFor(() => f.counts().claims === 1)
    await f.broker.close()
    release()
    await waitFor(() => f.counts().closed === 1)
    expect(f.counts().cleaned).toBe(0)
    expect(f.broker.sessionCount()).toBe(0)
  })
  it('accepts 16 concurrent terminals like the controller, then refuses before claiming a grant', async () => {
    let release!: () => void
    const barrier = new Promise<void>((resolve) => (release = resolve))
    const f = await fixture({ claimBarrier: barrier })
    for (let i = 0; i < 16; i++) f.connect().on('error', () => {})
    await waitFor(() => f.counts().claims === 16)
    expect(await rejected(f.connect())).toBe(429)
    expect(f.counts().claims).toBe(16)
    await f.broker.close()
    release()
  })
  it('enforces websocket frame limits', async () => {
    const f = await fixture(),
      ws = f.connect()
    await ready(ws)
    ws.send(JSON.stringify({ type: 'input', data: 'x'.repeat(70000) }))
    await waitFor(() => f.counts().cleaned === 1)
  })
})

describe('Docker terminal transport', () => {
  it('accepts only the exact read-only readiness mounts belonging to this publication', () => {
    const current = info()
    current.Config.Labels!['pockethost.web.private-db.wait'] = 'true'
    const root = process.env.WEB_APPS_ROOT || '/home/pockethost-web/data'
    const home = `${root}/private-db-runtime/${app}/${deployment}-web`
    current.Mounts = [
      { Type: 'bind', Source: `${home}/ready`, Destination: '/run/kolab-ready', RW: false },
      { Type: 'bind', Source: `${home}/wait-env`, Destination: '/run/kolab-wait-env', RW: false },
      {
        Type: 'bind',
        Source: '/opt/pockethost-web/web_private_db_wait.cjs',
        Destination: '/run/kolab-wait.cjs',
        RW: false,
      },
    ]
    expect(validPublishedContainer(current, grant())).toBe(true)
    const clone = () => structuredClone(current)
    const writable = clone()
    writable.Mounts![0]!.RW = true
    const otherTenant = clone()
    otherTenant.Mounts![0]!.Source = `${root}/private-db-runtime/${actor}/${deployment}-web/ready`
    const otherVersion = clone()
    otherVersion.Mounts![1]!.Source = `${root}/private-db-runtime/${app}/${actor}-web/wait-env`
    const wrongScript = clone()
    wrongScript.Mounts![2]!.Source = '/etc/passwd'
    const extra = clone()
    extra.Mounts!.push({ Type: 'bind', Source: '/etc', Destination: '/host', RW: false })
    const nested = clone()
    nested.Mounts!.push({ Type: 'volume', Destination: '/run/kolab-ready/injected', RW: true })
    const duplicate = clone()
    duplicate.Mounts![1] = { ...duplicate.Mounts![0]! }
    const missing = clone()
    missing.Mounts!.pop()
    const unlabelled = clone()
    delete unlabelled.Config.Labels!['pockethost.web.private-db.wait']
    const legacyBind = clone()
    legacyBind.HostConfig!.Binds = ['/etc:/host:ro']
    for (const invalid of [
      writable,
      otherTenant,
      otherVersion,
      wrongScript,
      extra,
      nested,
      duplicate,
      missing,
      unlabelled,
      legacyBind,
    ])
      expect(validPublishedContainer(invalid, grant())).toBe(false)
  })
  it('never permits host/root or mismatched published container metadata', () => {
    expect(validPublishedContainer(info(), grant())).toBe(true)
    for (const altered of [
      { ...info(), Id: 'bad' },
      { ...info(), State: { Running: false } },
      { ...info(), Config: { ...info().Config, User: 'root' } },
      { ...info(), HostConfig: { Privileged: true } },
    ])
      expect(validPublishedContainer(altered, grant())).toBe(false)
    expect(() => terminalControl('https://external.example', 'x'.repeat(40))).toThrow()
    expect(() => terminalControl('http://127.0.0.1:8091/path', 'x'.repeat(40))).toThrow()
    expect(() => terminalControl('http://127.0.0.1:8091', 'short')).toThrow()
  })
  it('accepts Docker HTTP101 and HTTP200 native TTY hijacks and retains bytes after headers', async () => {
    for (const status of [101, 200]) {
      const directory = mkdtempSync(join(realpathSync(tmpdir()), 'terminal-docker-')),
        socket = join(directory, 'docker.sock')
      const engineServer = createServer((stream) => {
        stream.once('data', () =>
          stream.write(`HTTP/1.1 ${status} OK\r\nContent-Type: application/vnd.docker.raw-stream\r\n\r\ninitialTTY`)
        )
      })
      await new Promise<void>((resolve) => engineServer.listen(socket, resolve))
      const engine = new DockerTerminalEngine(socket),
        stream = await engine.attach('e'.repeat(64)),
        received: Buffer[] = []
      stream.on('data', (chunk) => received.push(chunk))
      stream.resume()
      await waitFor(() => received.length > 0)
      expect(Buffer.concat(received).toString()).toBe('initialTTY')
      stream.destroy()
      await new Promise<void>((resolve) => engineServer.close(() => resolve()))
      rmSync(directory, { recursive: true, force: true })
    }
  })
})
