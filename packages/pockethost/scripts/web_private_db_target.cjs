#!/usr/bin/env node
'use strict'
// Runs in one immutable database network namespace. The TCP destination cannot
// be supplied by a caller, the controller, a URL, or the listening Unix client.
const fs = require('node:fs')
const net = require('node:net')
const { parseArgs } = require('node:util')

function namespace() {
  try {
    return fs.readlinkSync('/proc/self/ns/net')
  } catch {
    return 'namespace-unavailable'
  }
}
function readStatus(path) {
  const fd = fs.openSync(path, fs.constants.O_RDONLY | fs.constants.O_NONBLOCK | fs.constants.O_NOFOLLOW)
  try {
    const info = fs.fstatSync(fd)
    if (!info.isFile() || info.size > 1024) throw Error('status')
    const bytes = Buffer.alloc(1025),
      size = fs.readSync(fd, bytes, 0, bytes.length, null)
    if (size > 1024) throw Error('status')
    return JSON.parse(bytes.subarray(0, size).toString('utf8'))
  } finally {
    fs.closeSync(fd)
  }
}
function probe(port = 8090, timeoutMs = 750) {
  return new Promise((resolve) => {
    let settled = false
    const socket = net.createConnection({ host: '127.0.0.1', port })
    const timer = setTimeout(() => done(false), timeoutMs)
    function done(ready) {
      if (settled) return
      settled = true
      clearTimeout(timer)
      socket.destroy()
      resolve(ready)
    }
    socket.once('connect', () => done(true))
    socket.once('error', () => done(false))
  })
}
async function check(options = {}) {
  options = { socket: '/run/target/t.sock', status: '/tmp/kolab-db-target-status.json', tcpPort: 8090, ...options }
  try {
    const state = readStatus(options.status),
      info = fs.lstatSync(options.socket)
    if (
      state.version !== 1 ||
      state.ready !== true ||
      state.namespace !== namespace() ||
      !Number.isFinite(state.checkedAt) ||
      Date.now() - state.checkedAt < 0 ||
      Date.now() - state.checkedAt >= 6000 ||
      !info.isSocket() ||
      (info.mode & 0o777) !== 0o660 ||
      info.uid !== process.getuid() ||
      info.gid !== process.getgid() ||
      info.ino !== state.inode ||
      info.dev !== state.device
    )
      return false
    return await probe(options.tcpPort)
  } catch {
    return false
  }
}
function createTarget(options = {}) {
  // tcpPort exists solely for isolated module tests. The executable deliberately
  // exposes no destination flag or environment override.
  options = {
    socket: '/run/target/t.sock',
    status: '/tmp/kolab-db-target-status.json',
    tcpPort: 8090,
    pollMs: 2000,
    connectMs: 2000,
    idleMs: 120000,
    maxConnections: 128,
    ...options,
  }
  const connections = new Set()
  let stopping = false,
    refreshing = false,
    timer,
    socketIdentity,
    announcement = ''
  function removeStatus() {
    try {
      fs.unlinkSync(options.status)
    } catch {}
  }
  function announce(message) {
    if (message !== announcement) {
      announcement = message
      console.log(message)
    }
  }
  function accept(client) {
    if (stopping || connections.size >= options.maxConnections) {
      client.destroy()
      return
    }
    client.pause()
    const upstream = net.createConnection({ host: '127.0.0.1', port: options.tcpPort, allowHalfOpen: true })
    let finished = false,
      idleTimer
    const connection = { close }
    connections.add(connection)
    const connectTimer = setTimeout(close, options.connectMs)
    function close() {
      if (finished) return
      finished = true
      clearTimeout(connectTimer)
      clearTimeout(idleTimer)
      connections.delete(connection)
      client.destroy()
      upstream.destroy()
    }
    function activity() {
      clearTimeout(idleTimer)
      idleTimer = setTimeout(close, options.idleMs)
    }
    for (const stream of [client, upstream]) {
      stream.on('error', close)
      stream.on('close', close)
    }
    upstream.once('connect', () => {
      clearTimeout(connectTimer)
      activity()
      client.on('data', activity)
      upstream.on('data', activity)
      client.pipe(upstream)
      upstream.pipe(client)
      client.resume()
    })
  }
  const server = net.createServer({ allowHalfOpen: true, pauseOnConnect: true }, accept)
  async function refresh() {
    if (stopping || refreshing || !server.listening) return
    refreshing = true
    try {
      if (!(await probe(options.tcpPort))) {
        removeStatus()
        announce('UNAVAILABLE database')
        return
      }
      if (stopping) return
      const info = fs.lstatSync(options.socket)
      if (!info.isSocket() || info.ino !== socketIdentity.ino || info.dev !== socketIdentity.dev) throw Error('socket')
      const state = {
        version: 1,
        ready: true,
        checkedAt: Date.now(),
        namespace: namespace(),
        inode: info.ino,
        device: info.dev,
      }
      const temp = options.status + '.tmp'
      fs.writeFileSync(temp, JSON.stringify(state), { mode: 0o600 })
      fs.renameSync(temp, options.status)
      announce('READY ' + state.namespace)
    } catch {
      removeStatus()
      announce('UNAVAILABLE database')
    } finally {
      refreshing = false
    }
  }
  const ready = new Promise((resolve, reject) => {
    try {
      if (
        fs.existsSync(options.socket) ||
        (() => {
          try {
            return fs.lstatSync(options.socket).isSymbolicLink()
          } catch {
            return false
          }
        })()
      ) {
        const info = fs.lstatSync(options.socket)
        if (
          !info.isSocket() ||
          info.uid !== process.getuid() ||
          info.gid !== process.getgid() ||
          (info.mode & 0o777) !== 0o660
        )
          throw Error('socket collision')
        fs.unlinkSync(options.socket)
      }
      server.once('error', reject)
      server.listen({ path: options.socket, backlog: 128 }, () => {
        fs.chmodSync(options.socket, 0o660)
        socketIdentity = fs.lstatSync(options.socket)
        timer = setInterval(refresh, options.pollMs)
        refresh()
        resolve()
      })
    } catch (error) {
      reject(error)
    }
  })
  server.on('error', () => {
    removeStatus()
    announce('UNAVAILABLE listener')
  })
  async function stop() {
    stopping = true
    clearInterval(timer)
    removeStatus()
    for (const connection of connections) connection.close()
    await new Promise((resolve) => server.close(() => resolve()))
    try {
      const info = fs.lstatSync(options.socket)
      if (socketIdentity && info.isSocket() && info.ino === socketIdentity.ino && info.dev === socketIdentity.dev)
        fs.unlinkSync(options.socket)
    } catch {}
  }
  return { ready, stop, refresh, connections, server }
}
async function main() {
  const { values } = parseArgs({ options: { check: { type: 'boolean' } } })
  if (values.check) {
    process.exitCode = (await check()) ? 0 : 1
    return
  }
  const target = createTarget()
  for (const signal of ['SIGTERM', 'SIGINT', 'SIGHUP'])
    process.once(signal, () =>
      target.stop().finally(() => {
        process.exitCode = 0
      })
    )
  await target.ready
}
module.exports = { createTarget, check, probe }
if (require.main === module)
  main().catch(() => {
    console.error('UNAVAILABLE configuration')
    process.exitCode = 1
  })
