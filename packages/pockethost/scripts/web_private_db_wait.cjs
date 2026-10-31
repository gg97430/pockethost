#!/usr/bin/env node
'use strict'
// Run with the trusted tooling Node/loader, never an executable from the application image.
const fs = require('node:fs')
const { spawn } = require('node:child_process')
const { constants } = require('node:os')
const RESTORED_ENVIRONMENT = new Set(['NODE_OPTIONS', 'NODE_PATH', 'LD_PRELOAD', 'LD_LIBRARY_PATH'])

function childEnvironment(path) {
  const environment = { ...process.env }
  for (const name of RESTORED_ENVIRONMENT) delete environment[name]
  let descriptor
  try {
    descriptor = fs.openSync(path, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK)
  } catch (error) {
    if (error.code === 'ENOENT') return environment
    throw error
  }
  try {
    const info = fs.fstatSync(descriptor)
    if (!info.isFile() || info.size > 262144) throw Error('environment')
    const bytes = Buffer.alloc(262145),
      size = fs.readSync(descriptor, bytes, 0, bytes.length, 0)
    if (size > 262144) throw Error('environment')
    const value = JSON.parse(bytes.subarray(0, size).toString('utf8'))
    if (
      !value ||
      value.version !== 1 ||
      !value.environment ||
      typeof value.environment !== 'object' ||
      Array.isArray(value.environment)
    )
      throw Error('environment')
    for (const [name, content] of Object.entries(value.environment)) {
      if (!RESTORED_ENVIRONMENT.has(name) || typeof content !== 'string' || content.includes('\0'))
        throw Error('environment')
      environment[name] = content
    }
    return environment
  } finally {
    fs.closeSync(descriptor)
  }
}

async function main(argv) {
  let readyFile = '/run/kolab-ready/ready',
    namespaceFile = '/proc/self/ns/net',
    pidStatFile = '/proc/1/stat',
    environmentFile = '/run/kolab-wait-env/config.json',
    timeoutMs = 45000
  const remaining = [...argv]
  while (remaining.length && remaining[0] !== '--') {
    const flag = remaining.shift(),
      value = remaining.shift()
    if (!value) throw Error('arguments')
    if (flag === '--ready-file') readyFile = value
    else if (flag === '--netns-file') namespaceFile = value
    else if (flag === '--pid-stat-file') pidStatFile = value
    else if (flag === '--environment-file') environmentFile = value
    else if (flag === '--timeout-ms' && /^\d+$/.test(value)) timeoutMs = Number(value)
    else throw Error('arguments')
  }
  if (remaining[0] === '--') remaining.shift()
  if (!remaining.length || !Number.isSafeInteger(timeoutMs) || timeoutMs < 10 || timeoutMs > 120000)
    throw Error('arguments')
  let child,
    interrupted = '',
    killTimer
  const signals = ['SIGTERM', 'SIGINT', 'SIGHUP']
  function terminate(signal) {
    interrupted = signal
    if (child?.pid) {
      try {
        process.kill(-child.pid, signal)
      } catch {}
      if (!killTimer)
        killTimer = setTimeout(() => {
          try {
            process.kill(-child.pid, 'SIGKILL')
          } catch {}
        }, 8000)
    }
  }
  for (const signal of signals) process.on(signal, terminate)
  const namespace = fs.readlinkSync(namespaceFile)
  if (!/^net:\[\d+\]$/.test(namespace)) throw Error('namespace')
  // Docker can retain a network namespace across restart. PID 1 is the trusted
  // init process; its kernel start ticks identify this particular container run.
  const pidStat = fs.readFileSync(pidStatFile, 'utf8'),
    end = pidStat.lastIndexOf(')')
  if (end < 0) throw Error('identity')
  const ticks = pidStat
    .slice(end + 1)
    .trim()
    .split(/\s+/)[19]
  if (!/^\d+$/.test(ticks || '')) throw Error('identity')
  const identity = namespace + ':' + ticks
  const started = Date.now()
  let ready = false
  while (!interrupted && Date.now() - started < timeoutMs) {
    let descriptor
    try {
      descriptor = fs.openSync(readyFile, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK)
      const info = fs.fstatSync(descriptor)
      if (info.isFile() && info.size <= 128) {
        const bytes = Buffer.alloc(129),
          size = fs.readSync(descriptor, bytes, 0, bytes.length, 0)
        ready = size <= 128 && bytes.subarray(0, size).toString('ascii') === identity
      }
    } catch {
    } finally {
      if (descriptor !== undefined) fs.closeSync(descriptor)
    }
    if (ready) break
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  if (interrupted) {
    process.exitCode = 128 + (constants.signals[interrupted] || 15)
    return
  }
  if (!ready) {
    console.error('La liaison privée de la base ne répond pas au démarrage.')
    process.exitCode = 75
    return
  }
  const environment = childEnvironment(environmentFile)
  await new Promise((resolve) => {
    child = spawn(remaining[0], remaining.slice(1), {
      stdio: 'inherit',
      shell: false,
      detached: true,
      env: environment,
    })
    child.once('error', () => {
      console.error('La commande du site ne peut pas démarrer.')
      process.exitCode = 127
      resolve()
    })
    child.once('exit', (code, signal) => {
      clearTimeout(killTimer)
      process.exitCode = Number.isInteger(code) ? code : 128 + (constants.signals[signal] || 15)
      resolve()
    })
  })
  for (const signal of signals) process.removeListener(signal, terminate)
}

if (require.main === module)
  main(process.argv.slice(2)).catch(() => {
    console.error('Démarrage privé indisponible.')
    process.exitCode = 75
  })
module.exports = { main }
