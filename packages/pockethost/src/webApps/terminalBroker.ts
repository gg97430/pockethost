import { randomBytes } from 'node:crypto'
import { lstatSync, realpathSync } from 'node:fs'
import { createServer, request as httpRequest, type Server } from 'node:http'
import { createConnection, type Socket } from 'node:net'
import { isAbsolute, join, normalize } from 'node:path'
import type { Duplex } from 'node:stream'
import WebSocket, { WebSocketServer } from 'ws'
import {
  PORTABLE_TERMINAL_SUPERVISOR,
  terminalCleanupScript,
  terminalLoaderEnvironment,
  trustedTerminalNode,
} from './terminalTools'

export const TERMINAL_PROTOCOL = 'ph-web-terminal.v1'
const GRANT = /^ph-web-grant\.([A-Za-z0-9]{80})$/
const ID = /^[a-z0-9]{15}$/
const HEX = /^[a-f0-9]{64}$/
const MAX_FRAME = 64 * 1024
const MAX_BUFFER = 1024 * 1024
export type TerminalGrant = {
  sessionId: string
  appId: string
  actorId: string
  deploymentId: string
  containerName: string
  shell: '/bin/sh' | '/bin/bash'
  proof: string
  absoluteExpiresAt: string
}
export type ContainerInfo = {
  Id: string
  State: { Running: boolean }
  Config: { User: string; Labels?: Record<string, string>; Env?: string[] }
  HostConfig?: { Privileged?: boolean; Binds?: string[] | null; ReadonlyRootfs?: boolean }
  Mounts?: { Type?: string; Name?: string; Source?: string; Destination?: string; RW?: boolean }[]
}
export type TerminalControl = {
  claim(token: string): Promise<TerminalGrant>
  validate(grant: TerminalGrant, containerId: string): Promise<{ valid: boolean; absoluteExpiresAt?: string }>
  close(grant: TerminalGrant, reason: string): Promise<unknown>
}
export type TerminalEngine = {
  inspect(container: string): Promise<ContainerInfo>
  createExec(containerId: string, shell: string, marker: string, maxSeconds: number): Promise<string>
  attach(execId: string): Promise<Duplex>
  resize(execId: string, cols: number, rows: number): Promise<unknown>
  /** `since` : début de session (ms epoch) ; les processus détachés nés après sont aussi tués. */
  cleanup(containerId: string, marker: string, since: number): Promise<void>
}
export type TerminalBrokerOptions = {
  origin: string
  control: TerminalControl
  engine: TerminalEngine
  maxSessions?: number
  validationIntervalMs?: number
  idleTimeoutMs?: number
  maxDurationMs?: number
  onError?: (event: string) => void
}

/** Only fixed local control endpoints are called. Browser bearer tokens never reach this process. */
export function terminalControl(base: string, secret: string): TerminalControl {
  const url = new URL(base)
  if (
    url.protocol !== 'http:' ||
    url.hostname !== '127.0.0.1' ||
    !url.port ||
    url.username ||
    url.password ||
    url.pathname !== '/' ||
    url.search ||
    url.hash ||
    secret.length < 32
  )
    throw new Error('Invalid terminal control configuration')
  const send = async (path: string, body: unknown) => {
    const response = await fetch(`${url.origin}/api/web-apps/_terminal/${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Web-Worker': secret },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(8000),
      redirect: 'error',
    })
    if (!response.ok) throw new Error('Terminal authorization refused')
    return (await response.json()) as any
  }
  return {
    claim: (token) => send('claim', { token }),
    validate: (grant, containerId) => send('validate', { sessionId: grant.sessionId, proof: grant.proof, containerId }),
    close: (grant, reason) => send('close', { sessionId: grant.sessionId, proof: grant.proof, reason }),
  }
}

/** Only the worker's exact read-only private-DB readiness mounts may cross this boundary. */
const validTerminalMounts = (info: ContainerInfo, grant: TerminalGrant) => {
  if ((info.HostConfig?.Binds || []).length) return false
  const mounts = info.Mounts || [],
    binds = mounts.filter((mount) => mount.Type === 'bind')
  if (info.Config.Labels?.['pockethost.web.private-db.wait'] !== 'true') return binds.length === 0
  const root = process.env.WEB_APPS_ROOT || '/home/pockethost-web/data'
  if (!isAbsolute(root) || normalize(root) !== root || !ID.test(grant.appId) || !ID.test(grant.deploymentId))
    return false
  const home = join(root, 'private-db-runtime', grant.appId, `${grant.deploymentId}-web`)
  const expected: Record<string, string> = {
    '/run/kolab-ready': join(home, 'ready'),
    '/run/kolab-wait-env': join(home, 'wait-env'),
    '/run/kolab-wait.cjs': '/opt/pockethost-web/web_private_db_wait.cjs',
  }
  if (binds.length !== 3 || new Set(binds.map((mount) => mount.Destination)).size !== 3) return false
  return mounts.every((mount) => {
    const destination = mount.Destination || ''
    const overlaps = Object.keys(expected).some(
      (path) => destination === path || destination.startsWith(path + '/') || path.startsWith(destination + '/')
    )
    if (mount.Type !== 'bind') return !overlaps
    return mount.RW === false && Object.hasOwn(expected, destination) && mount.Source === expected[destination]
  })
}

export const validPublishedContainer = (info: ContainerInfo, grant: TerminalGrant) => {
  const labels = info.Config?.Labels || {}
  return (
    HEX.test(info.Id || '') &&
    info.State?.Running === true &&
    info.Config?.User === '10001:10001' &&
    labels['pockethost.web'] === 'true' &&
    labels['pockethost.web.app'] === grant.appId &&
    labels['pockethost.web.deployment'] === grant.deploymentId &&
    info.HostConfig?.Privileged !== true &&
    info.HostConfig?.ReadonlyRootfs === true &&
    validTerminalMounts(info, grant)
  )
}

/** Docker's native TTY provides the PTY. No host shell or node-pty is involved. */
export class DockerTerminalEngine implements TerminalEngine {
  constructor(readonly socketPath: string) {}
  private json(method: string, path: string, body?: unknown, timeout = 5000): Promise<any> {
    return new Promise((resolve, reject) => {
      const data = body === undefined ? undefined : Buffer.from(JSON.stringify(body))
      const request = httpRequest(
        {
          socketPath: this.socketPath,
          method,
          path,
          headers: data ? { 'Content-Type': 'application/json', 'Content-Length': data.length } : {},
        },
        (response) => {
          let chunks: Buffer[] = [],
            length = 0
          response.on('data', (chunk: Buffer) => {
            length += chunk.length
            if (length > 1024 * 1024) {
              response.destroy()
              reject(new Error('Docker response too large'))
            } else chunks.push(chunk)
          })
          response.on('end', () => {
            if (!response.statusCode || response.statusCode >= 300) {
              reject(new Error('Docker terminal operation failed'))
              return
            }
            try {
              resolve(length ? JSON.parse(Buffer.concat(chunks).toString('utf8')) : {})
            } catch {
              reject(new Error('Invalid Docker response'))
            }
          })
          response.on('error', reject)
        }
      )
      request.setTimeout(timeout, () => request.destroy(new Error('Docker terminal timeout')))
      request.on('error', reject)
      request.end(data)
    })
  }
  async verifyRootless(): Promise<void> {
    const match = /^\/run\/user\/([1-9][0-9]*)\/docker\.sock$/.exec(this.socketPath)
    if (
      !match ||
      !process.getuid ||
      process.getuid() === 0 ||
      process.getuid() !== Number(match[1]) ||
      !lstatSync(this.socketPath).isSocket() ||
      realpathSync(this.socketPath) !== this.socketPath
    )
      throw new Error('A dedicated non-root Docker socket is required')
    const info = await this.json('GET', '/info')
    if (
      !Array.isArray(info.SecurityOptions) ||
      !info.SecurityOptions.some((value: string) => value.includes('rootless')) ||
      `${info.CgroupVersion}` !== '2' ||
      info.CgroupDriver !== 'systemd'
    )
      throw new Error('Rootless Docker with cgroup v2/systemd is required')
  }
  inspect(container: string) {
    if (!/^ph-web-[a-z0-9]{15}-[a-z0-9]{15}$/.test(container))
      return Promise.reject(new Error('Invalid terminal container'))
    return this.json('GET', `/containers/${container}/json`)
  }
  async createExec(containerId: string, shell: string, marker: string, maxSeconds = 1800) {
    if (
      !HEX.test(containerId) ||
      !['/bin/sh', '/bin/bash'].includes(shell) ||
      !/^[A-Za-z0-9]{64}$/.test(marker) ||
      !Number.isInteger(maxSeconds) ||
      maxSeconds < 1 ||
      maxSeconds > 1800
    )
      throw new Error('Invalid terminal exec')
    const info = (await this.json('GET', `/containers/${containerId}/json`)) as ContainerInfo
    const loader = trustedTerminalNode(info, process.env.WEB_APPS_STORAGE_HELPER_IMAGE || ''),
      environment = terminalLoaderEnvironment(info)
    const command = [
      ...loader.command,
      '--max-old-space-size=32',
      '-e',
      PORTABLE_TERMINAL_SUPERVISOR,
      shell,
      `${maxSeconds}`,
    ]
    const result = await this.json('POST', `/containers/${containerId}/exec`, {
      AttachStdin: true,
      AttachStdout: true,
      AttachStderr: true,
      Tty: true,
      Privileged: false,
      Env: [
        `PH_WEB_TERMINAL_SESSION=${marker}`,
        'TERM=xterm-256color',
        ...environment.neutral,
        'PH_WEB_TERMINAL_LOADERS=' + JSON.stringify(environment.original),
      ],
      User: '10001:10001',
      Cmd: command,
    })
    if (!HEX.test(result.Id || '')) throw new Error('Invalid Docker exec identifier')
    return result.Id
  }
  attach(execId: string): Promise<Duplex> {
    if (!HEX.test(execId)) return Promise.reject(new Error('Invalid Docker exec identifier'))
    // Use a raw socket: Docker may hijack with either HTTP101 or HTTP200.
    return new Promise((resolve, reject) => {
      const socket = createConnection(this.socketPath),
        payload = Buffer.from('{"Detach":false,"Tty":true}')
      let header = Buffer.alloc(0),
        done = false
      const fail = () => {
        if (!done) {
          done = true
          socket.destroy()
          reject(new Error('Docker terminal attach failed'))
        }
      }
      socket.setTimeout(8000, fail)
      socket.once('error', fail)
      const read = (chunk: Buffer) => {
        header = Buffer.concat([header, chunk])
        const end = header.indexOf('\r\n\r\n')
        if (end < 0) {
          if (header.length > 16384) fail()
          return
        }
        const text = header.subarray(0, end).toString('latin1')
        if (
          !/^HTTP\/1\.[01] (?:101|200)\b/.test(text) ||
          !/content-type:\s*application\/vnd\.docker\.raw-stream/i.test(text)
        ) {
          fail()
          return
        }
        done = true
        socket.pause()
        socket.setTimeout(0)
        socket.removeListener('data', read)
        socket.removeListener('error', fail)
        socket.on('error', () => {})
        const rest = header.subarray(end + 4)
        if (rest.length) socket.unshift(rest)
        resolve(socket)
      }
      socket.on('data', read)
      socket.once('connect', () =>
        socket.write(
          Buffer.concat([
            Buffer.from(
              `POST /exec/${execId}/start HTTP/1.1\r\nHost: docker\r\nConnection: Upgrade\r\nUpgrade: tcp\r\nContent-Type: application/json\r\nContent-Length: ${payload.length}\r\n\r\n`
            ),
            payload,
          ])
        )
      )
      socket.once('close', () => {
        if (!done) fail()
      })
    })
  }
  resize(execId: string, cols: number, rows: number) {
    if (
      !HEX.test(execId) ||
      !Number.isInteger(cols) ||
      !Number.isInteger(rows) ||
      cols < 1 ||
      cols > 500 ||
      rows < 1 ||
      rows > 500
    )
      return Promise.reject(new Error('Invalid terminal size'))
    return this.json('POST', `/exec/${execId}/resize?h=${rows}&w=${cols}`)
  }
  async cleanup(containerId: string, marker: string, since: number) {
    if (!HEX.test(containerId) || !/^[A-Za-z0-9]{64}$/.test(marker) || !Number.isFinite(since)) return
    // The marker is unique to this exec and its children, but a child can drop it (env -u, setsid). Never trust a
    // writable PID file or signal a host PID: see terminalCleanupTargets (ancestry, start time, Unix session).
    const code = terminalCleanupScript(marker, since - 2000)
    const info = (await this.json('GET', `/containers/${containerId}/json`)) as ContainerInfo
    const loader = trustedTerminalNode(info, process.env.WEB_APPS_STORAGE_HELPER_IMAGE || ''),
      environment = terminalLoaderEnvironment(info)
    const created = await this.json('POST', `/containers/${containerId}/exec`, {
      AttachStdout: false,
      AttachStderr: false,
      AttachStdin: false,
      Tty: false,
      Privileged: false,
      User: '10001:10001',
      Env: environment.neutral,
      Cmd: [...loader.command, '--max-old-space-size=32', '-e', code],
    })
    if (HEX.test(created.Id || '')) await this.json('POST', `/exec/${created.Id}/start`, { Detach: true, Tty: false })
  }
}

type LiveSession = {
  grant: TerminalGrant
  ws: WebSocket
  stream?: Duplex
  execId?: string
  containerId: string
  marker: string
  closing: boolean
  lastActivity: number
  awaitingPong?: boolean
  startedAt: number
  timer?: ReturnType<typeof setInterval>
}
export function createTerminalBroker(options: TerminalBrokerOptions): {
  server: Server
  close(): Promise<void>
  sessionCount(): number
} {
  const origin = new URL(options.origin)
  if (origin.protocol !== 'https:' || origin.origin !== options.origin || origin.username || origin.password)
    throw new Error('Exact HTTPS terminal origin required')
  const sessions = new Map<string, LiveSession>()
  const pendingSockets = new Set<Duplex>()
  const validationEvery = options.validationIntervalMs || 30000,
    idleTimeout = options.idleTimeoutMs || 600000,
    absoluteTimeout = options.maxDurationMs || 1800000
  let pending = 0,
    shuttingDown = false
  const server = createServer((req, res) => {
    if (req.method === 'GET' && req.url === '/health') {
      res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
      res.end(JSON.stringify({ status: 'ok' }))
      return
    }
    res.writeHead(404)
    res.end()
  })
  const wss = new WebSocketServer({
    noServer: true,
    maxPayload: MAX_FRAME,
    perMessageDeflate: false,
    handleProtocols: () => TERMINAL_PROTOCOL,
  })
  const send = (session: LiveSession, value: unknown) => {
    if (session.ws.readyState === WebSocket.OPEN) session.ws.send(JSON.stringify(value))
  }
  const finish = async (session: LiveSession, reason: string, code = 1000, message = '') => {
    if (session.closing) return
    session.closing = true
    if (session.timer) clearInterval(session.timer)
    session.stream?.pause()
    if (message) send(session, { type: 'error', message })
    send(session, { type: 'exit', reason })
    if (session.ws.readyState === WebSocket.OPEN) session.ws.close(code, 'Terminal fermé')
    try {
      if (session.execId) await options.engine.cleanup(session.containerId, session.marker, session.startedAt)
    } catch {}
    session.stream?.destroy()
    try {
      await options.control.close(session.grant, reason)
    } catch {}
    sessions.delete(session.grant.sessionId)
    const kill = setTimeout(() => session.ws.terminate(), 1000)
    kill.unref()
  }
  server.on('upgrade', async (req, socket, head) => {
    const reject = (code: number) => {
      socket.write(`HTTP/1.1 ${code} Refused\r\nConnection: close\r\nContent-Length: 0\r\n\r\n`)
      socket.destroy()
    }
    if (
      shuttingDown ||
      req.method !== 'GET' ||
      req.url !== '/api/web-terminal/connect' ||
      req.headers.origin !== options.origin
    ) {
      reject(403)
      return
    }
    const protocols = `${req.headers['sec-websocket-protocol'] || ''}`.split(',').map((value) => value.trim())
    const match = protocols.length === 2 && protocols[0] === TERMINAL_PROTOCOL ? GRANT.exec(protocols[1] || '') : null
    if (!match) {
      reject(401)
      return
    }
    // Même plafond que le contrôleur (HandleWebTerminalSession, 16 terminaux ouverts sur le serveur).
    if (sessions.size + pending >= (options.maxSessions || 16)) {
      reject(429)
      return
    }
    pending++
    pendingSockets.add(socket)
    socket.on('error', () => {})
    socket.pause()
    let grant: TerminalGrant | undefined
    try {
      grant = await options.control.claim(match[1]!)
      if (
        !ID.test(grant.sessionId) ||
        !ID.test(grant.appId) ||
        !ID.test(grant.deploymentId) ||
        !ID.test(grant.actorId) ||
        !/^[A-Za-z0-9]{80}$/.test(grant.proof) ||
        grant.containerName !== `ph-web-${grant.appId}-${grant.deploymentId}` ||
        !['/bin/sh', '/bin/bash'].includes(grant.shell) ||
        !Number.isFinite(Date.parse(grant.absoluteExpiresAt)) ||
        Date.parse(grant.absoluteExpiresAt) <= Date.now()
      )
        throw new Error('Invalid terminal claim')
      if (sessions.has(grant.sessionId)) throw new Error('Terminal session already connected')
      const info = await options.engine.inspect(grant.containerName)
      if (!validPublishedContainer(info, grant)) throw new Error('Container changed')
      const checked = await options.control.validate(grant, info.Id)
      if (!checked.valid) throw new Error('Terminal revoked')
      if (socket.destroyed || shuttingDown) {
        await options.control.close(grant, shuttingDown ? 'broker_shutdown' : 'disconnected')
        socket.destroy()
        return
      }
      const boundGrant = grant
      wss.handleUpgrade(req, socket as Socket, head, (ws) => {
        const session: LiveSession = {
          grant: boundGrant,
          ws,
          containerId: info.Id,
          marker: randomBytes(32).toString('hex'),
          closing: false,
          lastActivity: Date.now(),
          startedAt: Date.now(),
        }
        sessions.set(boundGrant.sessionId, session)
        let size = { cols: 80, rows: 24 },
          input = '',
          validating = false
        ws.on('pong', () => {
          session.awaitingPong = false
        })
        ws.on('error', () => void finish(session, 'disconnected'))
        ws.on('close', () => void finish(session, 'disconnected'))
        ws.on('message', (data, binary) => {
          if (session.closing) return
          if (binary) {
            void finish(session, 'protocol_error', 1008)
            return
          }
          let value: any
          try {
            value = JSON.parse(data.toString())
          } catch {
            void finish(session, 'protocol_error', 1008)
            return
          }
          if (!value || typeof value !== 'object' || Array.isArray(value)) {
            void finish(session, 'protocol_error', 1008)
            return
          }
          if (value.type === 'close') {
            void finish(session, 'disconnected')
            return
          }
          if (value.type === 'resize') {
            if (
              !Number.isInteger(value.cols) ||
              !Number.isInteger(value.rows) ||
              value.cols < 1 ||
              value.cols > 500 ||
              value.rows < 1 ||
              value.rows > 500
            ) {
              void finish(session, 'protocol_error', 1008)
              return
            }
            size = { cols: value.cols, rows: value.rows }
            if (session.execId)
              void options.engine
                .resize(session.execId, size.cols, size.rows)
                .catch(() => finish(session, 'container_changed', 1001))
            return
          }
          if (value.type !== 'input' || typeof value.data !== 'string' || Buffer.byteLength(value.data) > MAX_FRAME) {
            void finish(session, 'protocol_error', 1008)
            return
          }
          session.lastActivity = Date.now()
          if (session.stream) {
            if (session.stream.writableLength > MAX_BUFFER) {
              void finish(session, 'protocol_error', 1008)
              return
            }
            session.stream.write(value.data)
          } else {
            input += value.data
            if (Buffer.byteLength(input) > MAX_FRAME) void finish(session, 'protocol_error', 1008)
          }
        })
        const validate = async () => {
          if (session.closing || validating) return
          if (Date.now() - session.lastActivity > idleTimeout) {
            void finish(session, 'idle')
            return
          }
          if (
            Date.now() - session.startedAt > absoluteTimeout ||
            Date.parse(boundGrant.absoluteExpiresAt) <= Date.now()
          ) {
            void finish(session, 'expired')
            return
          }
          if (session.awaitingPong) {
            void finish(session, 'disconnected')
            return
          }
          session.awaitingPong = true
          if (ws.readyState === WebSocket.OPEN) ws.ping()
          validating = true
          try {
            const fresh = await options.engine.inspect(boundGrant.containerName)
            if (
              fresh.Id !== info.Id ||
              !validPublishedContainer(fresh, boundGrant) ||
              !(await options.control.validate(boundGrant, info.Id)).valid
            )
              await finish(session, 'access_revoked', 1008)
          } catch {
            await finish(session, 'access_revoked', 1008)
          } finally {
            validating = false
          }
        }
        session.timer = setInterval(() => void validate(), validationEvery)
        void (async () => {
          try {
            session.execId = await options.engine.createExec(
              info.Id,
              boundGrant.shell,
              session.marker,
              Math.max(1, Math.min(1800, Math.ceil((Date.parse(boundGrant.absoluteExpiresAt) - Date.now()) / 1000)))
            )
            if (session.closing) {
              await options.engine.cleanup(info.Id, session.marker, session.startedAt)
              return
            }
            session.stream = await options.engine.attach(session.execId)
            if (session.closing) {
              session.stream.destroy()
              await options.engine.cleanup(info.Id, session.marker, session.startedAt)
              return
            }
            session.stream.once('error', () => void finish(session, 'shell_exited'))
            session.stream.once('end', () => void finish(session, 'shell_exited'))
            session.stream.once('close', () => void finish(session, 'shell_exited'))
            await options.engine.resize(session.execId, size.cols, size.rows)
            session.stream.on('data', (chunk: Buffer) => {
              if (session.closing) return
              session.lastActivity = Date.now()
              if (ws.readyState !== WebSocket.OPEN || ws.bufferedAmount > MAX_BUFFER) {
                void finish(session, 'protocol_error', 1008)
                return
              }
              for (let offset = 0; offset < chunk.length; offset += 16384) {
                if (session.closing || ws.bufferedAmount > MAX_BUFFER) {
                  void finish(session, 'protocol_error', 1008)
                  break
                }
                ws.send(chunk.subarray(offset, offset + 16384), { binary: true }, (error) => {
                  if (error) void finish(session, 'disconnected')
                })
              }
            })
            send(session, { type: 'ready', sessionId: boundGrant.sessionId, shell: boundGrant.shell })
            if (input) {
              session.stream.write(input)
              input = ''
            }
            session.stream.resume()
          } catch {
            options.onError?.('terminal_open_failed')
            await finish(
              session,
              'connect_failed',
              1011,
              'Le shell du conteneur est indisponible. Vérifiez que le site est toujours en ligne.'
            )
          }
        })()
      })
      socket.resume()
    } catch {
      if (grant) {
        try {
          await options.control.close(grant, 'connect_failed')
        } catch {}
      }
      reject(401)
    } finally {
      pendingSockets.delete(socket)
      pending--
    }
  })
  return {
    server,
    sessionCount: () => sessions.size,
    async close() {
      shuttingDown = true
      for (const socket of pendingSockets) socket.destroy()
      await Promise.all([...sessions.values()].map((session) => finish(session, 'broker_shutdown', 1001)))
      wss.close()
      await new Promise<void>((resolve) => server.close(() => resolve()))
    },
  }
}

export async function startTerminalBroker() {
  const socket = process.env.WEB_APPS_DOCKER_SOCKET || '',
    secret = process.env.WEB_APPS_WORKER_SECRET || ''
  const origin = process.env.WEB_APPS_TERMINAL_ORIGIN || 'https://app.monappli.re'
  const port = Number(process.env.WEB_APPS_TERMINAL_PORT || '3192')
  if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('Invalid terminal listener')
  const engine = new DockerTerminalEngine(socket)
  await engine.verifyRootless()
  const broker = createTerminalBroker({
    origin,
    engine,
    control: terminalControl(process.env.WEB_APPS_CONTROL_URL || 'http://127.0.0.1:8091', secret),
  })
  await new Promise<void>((resolve, reject) => {
    broker.server.once('error', reject)
    broker.server.listen(port, '127.0.0.1', resolve)
  })
  let closing = false
  const stop = () => {
    if (!closing) {
      closing = true
      void broker.close().finally(() => process.exit(0))
    }
  }
  process.once('SIGTERM', stop)
  process.once('SIGINT', stop)
  return broker
}
