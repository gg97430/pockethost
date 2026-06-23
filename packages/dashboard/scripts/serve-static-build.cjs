#!/usr/bin/env node

const { createReadStream } = require('node:fs')
const fs = require('node:fs/promises')
const http = require('node:http')
const path = require('node:path')

const root = path.resolve(process.argv[2] || 'build')
const host = process.env.HOST || '127.0.0.1'
const port = Number(process.env.PORT || '5174')

const mimeTypes = {
  '.css': 'text/css; charset=utf-8',
  '.gif': 'image/gif',
  '.html': 'text/html; charset=utf-8',
  '.ico': 'image/x-icon',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.txt': 'text/plain; charset=utf-8',
  '.webp': 'image/webp',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
}

const isInsideRoot = (filePath) => {
  const relative = path.relative(root, filePath)
  return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative))
}

const statFile = async (filePath) => {
  if (!isInsideRoot(filePath)) return null
  try {
    const stat = await fs.stat(filePath)
    return stat.isFile() ? stat : null
  } catch {
    return null
  }
}

const requestCandidates = (pathname) => {
  const decoded = decodeURIComponent(pathname)
  const withoutTrailingSlash = decoded.replace(/\/+$/, '') || '/'
  const normalized = path.normalize(decoded).replace(/^(\.\.[/\\])+/, '')
  const normalizedClean = path.normalize(withoutTrailingSlash).replace(/^(\.\.[/\\])+/, '')
  const exact = path.join(root, normalized)
  const clean = normalizedClean === '/' ? '' : normalizedClean.replace(/^[/\\]+/, '')

  return [
    exact,
    clean ? path.join(root, `${clean}.html`) : path.join(root, 'index.html'),
    clean ? path.join(root, clean, 'index.html') : path.join(root, 'index.html'),
  ]
}

const resolveRequest = async (pathname) => {
  for (const filePath of requestCandidates(pathname)) {
    const stat = await statFile(filePath)
    if (stat) return { filePath, stat, status: 200 }
  }

  const fallback = path.join(root, '404.html')
  const stat = await statFile(fallback)
  if (stat) return { filePath: fallback, stat, status: 404 }

  return null
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url || '/', `http://${req.headers.host || `${host}:${port}`}`)
    const resolved = await resolveRequest(url.pathname)

    if (!resolved) {
      res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' })
      res.end('Not found')
      return
    }

    const ext = path.extname(resolved.filePath)
    res.writeHead(resolved.status, {
      'cache-control': 'no-cache, no-store, must-revalidate',
      'content-length': resolved.stat.size,
      'content-type': mimeTypes[ext] || 'application/octet-stream',
    })

    if (req.method === 'HEAD') {
      res.end()
      return
    }

    createReadStream(resolved.filePath).pipe(res)
  } catch (error) {
    res.writeHead(500, { 'content-type': 'text/plain; charset=utf-8' })
    res.end(error instanceof Error ? error.message : String(error))
  }
})

server.listen(port, host, () => {
  console.log(`Serving ${root} at http://${host}:${port}`)
})
