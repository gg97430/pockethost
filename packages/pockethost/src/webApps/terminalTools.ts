import type { ContainerInfo } from './terminalBroker'

const ROOT = '/__pockethost_tools'
const loaderName = (key: string) =>
  /^(?:LD_|DYLD_)/.test(key) ||
  [
    'NODE_OPTIONS',
    'NODE_PATH',
    'NODE_EXTRA_CA_CERTS',
    'OPENSSL_CONF',
    'OPENSSL_MODULES',
    'OPENSSL_ENGINES',
    'GLIBC_TUNABLES',
    'LIBPATH',
    'SHLIB_PATH',
  ].includes(key)
/** The process supervising the terminal must never load code through site environment variables. */
export const terminalLoaderEnvironment = (info: ContainerInfo) => {
  const original: Record<string, string> = Object.create(null)
  for (const entry of info.Config.Env || []) {
    const at = entry.indexOf('=')
    if (at > 0 && loaderName(entry.slice(0, at))) original[entry.slice(0, at)] = entry.slice(at + 1)
  }
  const keys = new Set([
    ...Object.keys(original),
    'NODE_OPTIONS',
    'NODE_PATH',
    'LD_PRELOAD',
    'LD_LIBRARY_PATH',
    'LD_AUDIT',
    'GLIBC_TUNABLES',
    'OPENSSL_CONF',
    'OPENSSL_MODULES',
    'OPENSSL_ENGINES',
  ])
  return { original, neutral: [...keys].map((key) => `${key}=`) }
}
export const trustedTerminalNode = (info: ContainerInfo, helperImage: string) => {
  const mounted = (info.Mounts || []).filter((mount) => mount.Destination === ROOT)
  if (!mounted.length) return { command: ['/usr/local/bin/node'], portable: false }
  if (!/^sha256:[a-f0-9]{64}$/.test(helperImage)) throw new Error('Unconfigured terminal tooling')
  const name = `ph-web-tools-${helperImage.slice(7, 27)}`
  if (
    (info.Mounts || []).some((mount) => mount.Destination?.startsWith(ROOT + '/')) ||
    mounted.length !== 1 ||
    mounted[0]!.Type !== 'volume' ||
    mounted[0]!.Name !== name ||
    mounted[0]!.RW !== false ||
    info.Config.Labels?.['pockethost.web.tooling'] !== name
  )
    throw new Error('Untrusted terminal tooling')
  return {
    command: [`${ROOT}/ld.so`, '--inhibit-cache', '--library-path', `${ROOT}/lib`, `${ROOT}/node`],
    portable: true,
  }
}
/** Runs entirely inside the unprivileged container, including scratch images with the approved tools volume. */
export const PORTABLE_TERMINAL_SUPERVISOR = String.raw`
const fs=require('fs'),{spawn}=require('child_process');
const [shell,limit]=process.argv.slice(1), seconds=Number(limit), marker=process.env.PH_WEB_TERMINAL_SESSION;
if(!['/bin/sh','/bin/bash'].includes(shell)||!Number.isInteger(seconds)||seconds<1||seconds>1800||!/^[a-f0-9]{64}$/.test(marker||''))process.exit(125);
const env={...process.env,...JSON.parse(process.env.PH_WEB_TERMINAL_LOADERS||'{}')};delete env.PH_WEB_TERMINAL_LOADERS;
let command=shell,args=['-i'];
if(!fs.existsSync(shell)){
 if(shell!=='/bin/sh')process.exit(127);
 command='/__pockethost_tools/ld.so';args=['--inhibit-cache','--library-path','/__pockethost_tools/lib','/__pockethost_tools/sh','-i'];
}
let ending=false;
function targets(){return fs.readdirSync('/proc').filter(x=>/^[1-9][0-9]*$/.test(x)&&+x>1&&+x!==process.pid).filter(x=>{try{return fs.readFileSync('/proc/'+x+'/environ').toString().split(String.fromCharCode(0)).includes('PH_WEB_TERMINAL_SESSION='+marker)}catch{return false}})}
function signal(name){for(const pid of targets())try{process.kill(+pid,name)}catch{}}
function end(code){if(ending)return;ending=true;signal('SIGHUP');setTimeout(()=>{signal('SIGKILL');process.exit(code)},500)}
process.on('SIGINT',()=>{});process.on('SIGTERM',()=>end(143));process.on('SIGHUP',()=>end(129));
const child=spawn(command,args,{env,stdio:'inherit'});child.on('error',()=>end(127));child.on('exit',(code)=>end(code??1));setTimeout(()=>end(124),seconds*1000);
`
export type TerminalProcess = { pid: number; ppid: number; sid: number; start: number; ours: boolean }
/**
 * Processus à tuer en fin de session (exécuté dans le conteneur via toString : fonction autonome, sans dépendance). Le
 * marqueur d'environnement ne suffit pas : `env -u PH_WEB_TERMINAL_SESSION setsid …` le retire et le processus détaché
 * est rattaché au PID 1. On tue donc aussi tout processus né pendant la session dans une session Unix neuve, hors de
 * l'arbre d'une autre exécution Docker (tâche, autre terminal, worker). `start` = démarrage en ms epoch (0 = inconnu).
 */
export function terminalCleanupTargets(rows: TerminalProcess[], self: number, since: number): number[] {
  const byPid = new Map(rows.map((row) => [row.pid, row]))
  const init = byPid.get(1)
  // Sessions déjà ouvertes avant le terminal (leur meneur vit encore) : celles du site, jamais détournables par l'attaquant.
  const baseline = new Set([0, init ? init.sid : -1])
  for (const row of rows) if (row.sid === row.pid && row.start > 0 && row.start < since) baseline.add(row.pid)
  const kinds = new Map()
  const kind = (pid: number): string => {
    if (kinds.has(pid)) return kinds.get(pid)
    kinds.set(pid, 'app')
    const row = byPid.get(pid)
    let value = 'app'
    if (!row) value = 'app'
    else if (row.ours) value = 'ours'
    else if (pid === self) value = 'self'
    else if (pid !== 1 && row.ppid === 0) value = 'foreign'
    else if (row.ppid > 1) value = kind(row.ppid)
    kinds.set(pid, value)
    return value
  }
  return rows
    .filter(
      (row) =>
        row.pid > 1 &&
        row.pid !== self &&
        (kind(row.pid) === 'ours' || (kind(row.pid) === 'app' && row.start >= since && !baseline.has(row.sid)))
    )
    .map((row) => row.pid)
}
/** Script de nettoyage (Node, UID du site) : HUP puis KILL, deux passes pour les processus relancés entre-temps. */
export const terminalCleanupScript = (marker: string, since: number) =>
  `const fs=require('fs');const marker=${JSON.stringify(`PH_WEB_TERMINAL_SESSION=${marker}`)};const since=${Math.floor(since)};const pick=(${terminalCleanupTargets.toString()});` +
  `function table(){let btime=0;try{btime=+/^btime ([0-9]+)$/m.exec(fs.readFileSync('/proc/stat','utf8'))[1]*1000}catch{}const rows=[];for(const x of fs.readdirSync('/proc')){if(!/^[1-9][0-9]*$/.test(x))continue;try{const s=fs.readFileSync('/proc/'+x+'/stat','utf8'),f=s.slice(s.lastIndexOf(')')+2).split(' ');let ours=false;try{ours=fs.readFileSync('/proc/'+x+'/environ').toString().split(String.fromCharCode(0)).includes(marker)}catch{}rows.push({pid:+x,ppid:+f[1],sid:+f[3],start:btime?btime+(+f[19])*10:0,ours})}catch{}}return rows}` +
  `function kill(signal){for(const pid of pick(table(),process.pid,since)){try{process.kill(pid,signal)}catch{}}}kill('SIGHUP');setTimeout(()=>kill('SIGKILL'),300);setTimeout(()=>kill('SIGKILL'),1000)`
