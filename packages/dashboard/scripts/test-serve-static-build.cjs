#!/usr/bin/env node
// Real localhost HTTP against a temporary build. No application code or production service is started.
const assert = require('node:assert/strict')
const { spawn } = require('node:child_process')
const fs = require('node:fs/promises')
const http = require('node:http')
const os = require('node:os')
const path = require('node:path')
const { once } = require('node:events')
const { before, after, test } = require('node:test')

const noStore = 'no-cache, no-store, must-revalidate'
const immutableCache = 'public, max-age=31536000, immutable'
let directory, build, upstream, server, port

const request = (pathname, method = 'GET') =>
  new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port, path: pathname, method }, (res) => {
      const chunks = []
      res.on('data', (chunk) => chunks.push(chunk))
      res.on('end', () =>
        resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks).toString() })
      )
      res.on('error', reject)
    })
    req.setTimeout(5000, () => req.destroy(new Error('Fixture HTTP timeout')))
    req.on('error', reject)
    req.end()
  })

before(async () => {
  directory = await fs.mkdtemp(path.join(os.tmpdir(), 'kolab-static-cache-'))
  build = path.join(directory, 'build')
  const files = {
    'index.html': '<html>public entry</html>',
    '404.html': '<html>application fallback</html>',
    '_app/immutable/chunks/fixture-abcd1234.js': 'console.log("public immutable fixture")',
    '_app/immutable/assets/fixture-abcd1234.css': 'body{color:navy}',
    '_app/immutable/private.html': '<html>never cache HTML</html>',
    '_app/version.json': '{"version":"mutable"}',
    '_app/immutable-other/private.txt': 'prefix lookalike',
    'private.txt': 'synthetic private fixture',
  }
  for (const [name, contents] of Object.entries(files)) {
    const file = path.join(build, name)
    await fs.mkdir(path.dirname(file), { recursive: true })
    await fs.writeFile(file, contents)
  }
  await fs.symlink(path.join(build, 'private.txt'), path.join(build, '_app/immutable/chunks/outside.js'))
  upstream = http.createServer((req, res) => {
    res.writeHead(200, {
      'cache-control': 'no-store',
      'content-type': 'application/json',
      'set-cookie': 'fixture=private; HttpOnly',
    })
    res.end(JSON.stringify({ fixture: 'private API response', path: req.url }))
  })
  upstream.listen(0, '127.0.0.1')
  await once(upstream, 'listening')
  const reserve = http.createServer()
  reserve.listen(0, '127.0.0.1')
  await once(reserve, 'listening')
  port = reserve.address().port
  await new Promise((resolve) => reserve.close(resolve))
  server = spawn(process.execPath, [path.join(__dirname, 'serve-static-build.cjs'), build], {
    env: {
      ...process.env,
      HOST: '127.0.0.1',
      PORT: String(port),
      MOTHERSHIP_PROXY_TARGET: `http://127.0.0.1:${upstream.address().port}`,
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('Static fixture startup timeout')), 5000)
    server.once('error', (error) => {
      clearTimeout(timeout)
      reject(error)
    })
    server.once('exit', (code) => {
      clearTimeout(timeout)
      reject(new Error(`Static fixture exited ${code}`))
    })
    server.stdout.once('data', () => {
      clearTimeout(timeout)
      resolve()
    })
  })
})

after(async () => {
  if (server && server.exitCode === null && server.signalCode === null) {
    const exited = once(server, 'exit')
    server.kill('SIGTERM')
    await exited
  }
  if (upstream) await new Promise((resolve) => upstream.close(resolve))
  if (directory) await fs.rm(directory, { recursive: true, force: true })
})

test('successful immutable JS/CSS GET and HEAD receive the public immutable policy', async () => {
  for (const [file, method] of [
    ['_app/immutable/chunks/fixture-abcd1234.js', 'GET'],
    ['_app/immutable/assets/fixture-abcd1234.css', 'GET'],
    ['_app/immutable/chunks/fixture-abcd1234.js', 'HEAD'],
  ]) {
    const response = await request('/' + file, method)
    assert.equal(response.status, 200)
    assert.equal(response.headers['cache-control'], immutableCache)
    if (method === 'HEAD') assert.equal(response.body, '')
  }
})

test('HTML, application fallback and the mutable version file retain no-store', async () => {
  for (const file of [
    '/',
    '/index.html',
    '/applications/nested/route',
    '/_app/version.json',
    '/_app/immutable/private.html',
  ]) {
    const response = await request(file)
    assert.equal(response.status, 200)
    assert.equal(response.headers['cache-control'], noStore)
  }
})

test('a missing immutable asset retains 404 and no-store including without a fallback file', async () => {
  let response = await request('/_app/immutable/chunks/missing.js')
  assert.equal(response.status, 404)
  assert.equal(response.headers['cache-control'], noStore)
  await fs.rename(path.join(build, '404.html'), path.join(build, '404.saved'))
  try {
    response = await request('/_app/immutable/chunks/missing.js')
    assert.equal(response.status, 404)
    assert.equal(response.headers['cache-control'], noStore)
  } finally {
    await fs.rename(path.join(build, '404.saved'), path.join(build, '404.html'))
  }
})

test('encoded traversal and a prefix lookalike cannot cache a file outside immutable', async () => {
  for (const file of [
    '/_app/immutable/chunks/%2e%2e%2f%2e%2e%2f%2e%2e%2fprivate.txt',
    '/_app/immutable-other/private.txt',
  ]) {
    const response = await request(file)
    assert.equal(response.status, 200)
    assert.equal(response.headers['cache-control'], noStore)
  }
})

test('canonical resolution excludes a symlink target outside immutable', async () => {
  const response = await request('/_app/immutable/chunks/outside.js')
  assert.equal(response.status, 200)
  assert.equal(response.headers['cache-control'], noStore)
})

test('other methods keep their existing status and no-store', async () => {
  const response = await request('/_app/immutable/chunks/fixture-abcd1234.js', 'POST')
  assert.equal(response.status, 200)
  assert.equal(response.headers['cache-control'], noStore)
})

test('API requests preserve the upstream private cache policy and cookie', async () => {
  const response = await request('/api/private')
  assert.equal(response.status, 200)
  assert.equal(response.headers['cache-control'], 'no-store')
  assert.deepEqual(response.headers['set-cookie'], ['fixture=private; HttpOnly'])
  assert.equal(JSON.parse(response.body).fixture, 'private API response')
})
