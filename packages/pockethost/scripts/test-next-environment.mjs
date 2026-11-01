#!/usr/bin/env node
// Install @next/env outside the checkout, then pass its resolved entry point:
// node test-next-environment.mjs /tmp/ph-next-env-check/node_modules/@next/env/dist/index.js
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { formatWebDotenv, parseWebDotenv } from '../src/common/webEnvironment.ts'

const require = createRequire(import.meta.url)
const nextEnv = process.argv[2] || require.resolve('@next/env')
const values = [
  '',
  'a=b#c',
  'password$HOME',
  '${TOKEN} $HOME $(command) $$ tail$',
  String.raw`one\$HOME two\\$HOME three\\\$HOME`,
  'C:\\new\\folder\\',
  String.raw`literal\n and \r`,
  'line1\n$HOME\nline3',
  'single\' double" $HOME',
  "single' backtick` $HOME",
  'double" backtick` $HOME',
  "quote' suffix\\",
]
const env = Object.fromEntries(values.map((value, i) => [`PH_NEXT_EXPORT_${i}`, { value, build: true, runtime: true }]))
const expected = Object.fromEntries(Object.entries(env).map(([name, item]) => [name, item.value]))
const exported = formatWebDotenv(env, true, false, 'nextjs')
assert.deepEqual(exported.warnings, [])
assert.deepEqual(Object.fromEntries(parseWebDotenv(exported.text).map(({ name, value }) => [name, value])), expected)
// Test the real Next.js loader in a clean process, including its dotenv-expand.
const loader = String.raw`
const assert = require('node:assert/strict'), fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const {modulePath,text,expected} = JSON.parse(fs.readFileSync(0,'utf8'));
const directory = fs.mkdtempSync(path.join(os.tmpdir(),'ph-next-env-roundtrip-'));
try {
  fs.writeFileSync(path.join(directory,'.env.local'),text,{mode:0o600});
  const {parsedEnv} = require(modulePath).loadEnvConfig(directory,true,console,true);
  assert.deepEqual(parsedEnv,expected);
} finally { fs.rmSync(directory,{recursive:true,force:true}); }
`
const result = spawnSync(process.execPath, ['-e', loader], {
  input: JSON.stringify({ modulePath: nextEnv, text: exported.text, expected }),
  env: {
    PATH: process.env.PATH,
    HOME: 'must-not-expand-home',
    TOKEN: 'must-not-expand-token',
    NODE_ENV: 'development',
  },
  encoding: 'utf8',
  timeout: 10000,
})
assert.equal(result.status, 0, result.stderr || result.stdout)
console.log(JSON.stringify({ passed: true, loader: '@next/env', values: values.length, reimport: true }))
