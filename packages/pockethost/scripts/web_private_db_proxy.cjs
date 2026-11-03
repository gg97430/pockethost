#!/usr/bin/env node
'use strict'
// Trusted sidecar: only this application's Unix socket and explicit loopback ports.
const fs = require('node:fs')
const net = require('node:net')
const crypto = require('node:crypto')
const { parseArgs } = require('node:util')

const ID = /^[a-z0-9]{15}$/
const TOKEN = /^(?:[A-Za-z0-9]{64}|[A-Za-z0-9]{80})$/
const REVISION = /^[A-Za-z0-9_-]{1,128}$/
const CONFIG_LIMIT = 16384
const HANDSHAKE_LIMIT = 256

function readFile(path, limit) {
  const descriptor = fs.openSync(path, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK)
  try {
    const info = fs.fstatSync(descriptor)
    if (!info.isFile() || info.size > limit) throw Error('configuration')
    const bytes = Buffer.alloc(limit + 1)
    let size = 0
    while (size <= limit) {
      const count = fs.readSync(descriptor, bytes, size, bytes.length - size, null)
      if (!count) break
      size += count
    }
    if (size > limit) throw Error('configuration')
    return bytes.subarray(0, size)
  } finally {
    fs.closeSync(descriptor)
  }
}

function configuration(path) {
  const raw = readFile(path, CONFIG_LIMIT)
  const value = JSON.parse(raw.toString('utf8'))
  if (!value || value.version !== 1 || !Array.isArray(value.bindings) || value.bindings.length > 4)
    throw Error('configuration')
  const ids = new Set(),
    ports = new Set()
  const bindings = value.bindings.map((item) => {
    if (
      !item ||
      typeof item.id !== 'string' ||
      typeof item.token !== 'string' ||
      typeof item.revision !== 'string' ||
      !ID.test(item.id) ||
      !TOKEN.test(item.token) ||
      !REVISION.test(item.revision) ||
      !Number.isInteger(item.port) ||
      item.port < 1024 ||
      item.port > 65535 ||
      ids.has(item.id) ||
      ports.has(item.port)
    )
      throw Error('configuration')
    ids.add(item.id)
    ports.add(item.port)
    return { id: item.id, port: item.port, token: item.token, revision: item.revision }
  })
  const limits = value.limits
  if (
    limits !== undefined &&
    (!limits ||
      typeof limits !== 'object' ||
      Array.isArray(limits) ||
      Object.keys(limits).sort().join(',') !== 'perApp,perBinding' ||
      !Number.isInteger(limits.perApp) ||
      limits.perApp < 1 ||
      limits.perApp > 256 ||
      !Number.isInteger(limits.perBinding) ||
      limits.perBinding < 1 ||
      limits.perBinding > limits.perApp)
  )
    throw Error('limits')
  return { bindings, limits, hash: crypto.createHash('sha256').update(raw).digest('hex') }
}

function check(options) {
  try {
    const config = configuration(options.config)
    const state = JSON.parse(readFile(options.status, 1024).toString('utf8'))
    return (
      state.version === 1 &&
      state.ready === true &&
      state.configHash === config.hash &&
      typeof state.checkedAt === 'number' &&
      Date.now() - state.checkedAt >= 0 &&
      Date.now() - state.checkedAt < 6000
    )
  } catch {
    return false
  }
}

async function probe(options, identifiers) {
  try {
    const config = configuration(options.config),
      ids = identifiers.split(',')
    if (!ids.length || ids.length > 4 || new Set(ids).size !== ids.length || ids.some((id) => !ID.test(id)))
      return false
    const selected = ids.map((id) => config.bindings.find((binding) => binding.id === id))
    if (selected.some((binding) => !binding)) return false
    const results = await Promise.all(
      selected.map(
        (binding) =>
          new Promise((resolve) => {
            let finished = false,
              response = Buffer.alloc(0)
            const upstream = net.createConnection({ path: options.socket })
            const timer = setTimeout(() => done(false), Math.min(options.handshakeMs || 2000, 2000))
            function done(ready) {
              if (finished) return
              finished = true
              clearTimeout(timer)
              upstream.destroy()
              resolve(ready)
            }
            upstream.once('error', () => done(false))
            upstream.once('close', () => done(false))
            upstream.once('connect', () => {
              // This is an authorization/readiness handshake only. No HTTP request,
              // application payload, or database credentials are sent to the target.
              upstream.write(JSON.stringify({ version: 1, bindingId: binding.id, token: binding.token }) + '\n')
            })
            upstream.on('data', (chunk) => {
              if (response.length + chunk.length > 1024) {
                done(false)
                return
              }
              response = Buffer.concat([response, chunk])
              const end = response.indexOf(10)
              if (end !== -1) done(end === 2 && response.subarray(0, end).equals(Buffer.from('OK')))
            })
          })
      )
    )
    return results.every(Boolean)
  } catch {
    return false
  }
}

function createProxy(options = {}) {
  options = {
    config: '/run/kolab-config/config.json',
    socket: '/run/kolab-db.sock',
    status: '/tmp/kolab-db-proxy-status.json',
    pollMs: 2000,
    handshakeMs: 2000,
    idleMs: 120000,
    // Normal SSR read bursts and HTTP keep-alive share a binding. The existing
    // per-application cap of 128 still bounds the complete proxy.
    perBinding: 128,
    perApp: 128,
    ...options,
  }
  const servers = new Map(),
    connections = new Set()
  let stopping = false,
    refreshing = false,
    timer,
    announced = '',
    currentHash = ''
  const namespace = (() => {
    try {
      return fs.readlinkSync('/proc/self/ns/net')
    } catch {
      return 'namespace-unavailable'
    }
  })()

  function removeStatus() {
    try {
      fs.unlinkSync(options.status)
    } catch {}
  }
  function announce(message) {
    if (announced !== message) {
      announced = message
      console.log(message)
    }
  }
  function destroyFor(bindingId) {
    for (const connection of connections) if (!bindingId || connection.binding.id === bindingId) connection.destroy()
  }
  function connect(entry, client) {
    client.pause()
    if (stopping || entry.closed || entry.connections >= options.perBinding || connections.size >= options.perApp) {
      client.destroy()
      return
    }
    const binding = { ...entry.binding }
    entry.connections += 1
    const upstream = net.createConnection({ path: options.socket, allowHalfOpen: true })
    let completed = false,
      handshake = Buffer.alloc(0),
      idleTimer
    const handshakeTimer = setTimeout(() => close(), options.handshakeMs)
    const connection = { binding, destroy: close }
    connections.add(connection)
    function close() {
      if (completed) return
      completed = true
      clearTimeout(handshakeTimer)
      clearTimeout(idleTimer)
      connections.delete(connection)
      entry.connections -= 1
      client.destroy()
      upstream.destroy()
    }
    function activity() {
      clearTimeout(idleTimer)
      idleTimer = setTimeout(close, options.idleMs)
    }
    client.on('error', close)
    upstream.on('error', close)
    client.on('close', close)
    upstream.on('close', close)
    upstream.on('connect', () => {
      // Credentials are never placed in environment variables, argv or log messages.
      upstream.write(JSON.stringify({ version: 1, bindingId: binding.id, token: binding.token }) + '\n')
    })
    function authenticate(chunk) {
      handshake = Buffer.concat([handshake, chunk])
      const end = handshake.indexOf(10)
      if (end === -1) {
        if (handshake.length > HANDSHAKE_LIMIT) close()
        return
      }
      if (end > HANDSHAKE_LIMIT || handshake.subarray(0, end).toString('ascii') !== 'OK') {
        close()
        return
      }
      clearTimeout(handshakeTimer)
      upstream.pause()
      upstream.removeListener('data', authenticate)
      const remaining = handshake.subarray(end + 1)
      handshake = Buffer.alloc(0)
      if (remaining.length) upstream.unshift(remaining)
      activity()
      client.on('data', activity)
      upstream.on('data', activity)
      client.pipe(upstream)
      upstream.pipe(client)
      client.resume()
      upstream.resume()
    }
    upstream.on('data', authenticate)
  }
  function closeEntry(entry) {
    entry.closed = true
    destroyFor(entry.binding.id)
    entry.server.close()
  }
  async function listen(binding) {
    const entry = { binding, server: null, connections: 0, closed: false }
    entry.server = net.createServer({ allowHalfOpen: true, pauseOnConnect: true }, (client) => connect(entry, client))
    return new Promise((resolve, reject) => {
      const failed = (error) => {
        if (servers.get(binding.id) === entry) servers.delete(binding.id)
        closeEntry(entry)
        removeStatus()
        announce('UNAVAILABLE ' + (error.code === 'EADDRINUSE' ? 'port_in_use' : 'listener'))
        reject(error)
      }
      entry.server.once('error', failed)
      entry.server.listen({ host: '127.0.0.1', port: binding.port, backlog: 64 }, () => {
        entry.server.removeListener('error', failed)
        entry.server.on('error', () => {
          closeEntry(entry)
          servers.delete(binding.id)
          removeStatus()
          announce('UNAVAILABLE listener')
        })
        servers.set(binding.id, entry)
        resolve(entry)
      })
    })
  }
  async function refresh() {
    if (stopping || refreshing) return
    refreshing = true
    try {
      const config = configuration(options.config),
        wanted = new Map(config.bindings.map((binding) => [binding.id, binding]))
      if (config.limits) Object.assign(options, config.limits)
      for (const [id, entry] of servers) {
        const replacement = wanted.get(id)
        if (!replacement || JSON.stringify(entry.binding) !== JSON.stringify(replacement)) {
          closeEntry(entry)
          servers.delete(id)
        }
      }
      for (const binding of config.bindings) if (!servers.has(binding.id)) await listen(binding)
      if (stopping) return
      currentHash = config.hash
      const state = { version: 1, ready: true, configHash: currentHash, checkedAt: Date.now(), namespace }
      const temp = options.status + '.tmp'
      fs.writeFileSync(temp, JSON.stringify(state), { mode: 0o600 })
      fs.renameSync(temp, options.status)
      announce('READY ' + namespace)
    } catch {
      removeStatus()
      // Invalid or torn configuration must not keep old capabilities/listeners alive.
      for (const entry of servers.values()) closeEntry(entry)
      servers.clear()
      destroyFor()
      if (!announced.startsWith('UNAVAILABLE port_in_use')) announce('UNAVAILABLE configuration')
    } finally {
      refreshing = false
    }
  }
  async function stop() {
    stopping = true
    clearInterval(timer)
    removeStatus()
    for (const entry of servers.values()) closeEntry(entry)
    servers.clear()
    destroyFor()
  }
  timer = setInterval(refresh, options.pollMs)
  refresh()
  return { stop, refresh, servers, connections, options }
}

async function main() {
  const { values } = parseArgs({
    options: {
      config: { type: 'string' },
      socket: { type: 'string' },
      status: { type: 'string' },
      check: { type: 'boolean' },
      probe: { type: 'string' },
      'poll-ms': { type: 'string' },
      'handshake-ms': { type: 'string' },
      'idle-ms': { type: 'string' },
    },
  })
  const options = {
    config: values.config || '/run/kolab-config/config.json',
    socket: values.socket || '/run/kolab-db.sock',
    status: values.status || '/tmp/kolab-db-proxy-status.json',
  }
  for (const [flag, property] of [
    ['poll-ms', 'pollMs'],
    ['handshake-ms', 'handshakeMs'],
    ['idle-ms', 'idleMs'],
  ]) {
    if (values[flag] !== undefined) {
      const number = Number(values[flag])
      if (!Number.isSafeInteger(number) || number < 10 || number > 300000) throw Error('configuration')
      options[property] = number
    }
  }
  if (values.check && values.probe !== undefined) throw Error('configuration')
  if (values.check) {
    process.exitCode = check(options) ? 0 : 1
    return
  }
  if (values.probe !== undefined) {
    process.exitCode = (await probe(options, values.probe)) ? 0 : 1
    return
  }
  const proxy = createProxy(options)
  for (const signal of ['SIGTERM', 'SIGINT', 'SIGHUP'])
    process.once(signal, () => {
      proxy.stop().finally(() => {
        process.exitCode = 0
      })
    })
}

module.exports = { createProxy, configuration, check, probe }
if (require.main === module) {
  main().catch(() => {
    console.error('UNAVAILABLE configuration')
    process.exitCode = 1
  })
}
