/**
 * Applications web — source « Dépôt Git (clé de déploiement) ». Logique pure, sûre pour les hooks mothership (Goja JSVM
 * : synchrone, sans API Node) et pour le dashboard. Seules deux formes d'adresse SSH sont acceptées :
 * `utilisateur@hôte:chemin` et `ssh://[utilisateur@]hôte[:port]/chemin`. Tout le reste (https, git://, file:, ext::,
 * ssh+git://, IP littérales, `..`, caractères de contrôle, `-` initial) est refusé : la même règle est revérifiée par
 * le lecteur Python du contrôleur et par le worker.
 */
export type WebGitRemote = {
  host: string
  port: number
  sshUser: string
  /** Chemin tel qu'envoyé à git-upload-pack : relatif (`owner/repo.git`) ou absolu (`/srv/git/repo.git`). */
  path: string
  /** Clé d'unicité (une clé de déploiement par dépôt), voir canonicalGitRemote. */
  canonical: string
}
export type WebGitProvider = 'github' | 'gitlab' | 'bitbucket' | 'other'
export type WebGitHostKey = { type: string; key: string; fingerprint: string }

export const WEB_GIT_DEFAULT_PORT = 22
export const WEB_GIT_DEFAULT_USER = 'git'
export const WEB_GIT_HOST_KEY_TYPES = [
  'ssh-ed25519',
  'ecdsa-sha2-nistp256',
  'ecdsa-sha2-nistp384',
  'ecdsa-sha2-nistp521',
  'ssh-rsa',
] as const

const INVALID = 'Adresse SSH du dépôt invalide. Utilisez git@hôte:propriétaire/dépôt.git ou ssh://git@hôte/chemin.'
const HOST = /^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$/
const USER = /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,31}$/
const PATH = /^[A-Za-z0-9._~\/-]{1,250}$/
const fail = (message = INVALID): never => {
  throw new Error(message)
}

/** Hôte DNS public uniquement (pas d'IP littérale ni de nom local) ; l'adresse résolue est revérifiée côté Python. */
export const webGitHostValid = (host: string) =>
  HOST.test(host) && !/(^|\.)(localhost|local|internal|localdomain|home\.arpa|arpa)$/.test(host)

const cleanPath = (raw: string) => {
  // ssh://hôte/~user/x désigne ~user/x, comme le fait git.
  const path = raw.replace(/^\/(?=~)/, '').replace(/\/+$/, '')
  const relative = path.replace(/^\//, '')
  if (!PATH.test(path) || !relative || relative.startsWith('-') || relative.startsWith('/')) fail()
  for (const part of relative.split('/')) if (!part || part === '.' || part === '..') fail()
  if (relative === '.git' || relative.endsWith('/.git')) fail()
  return path
}

/** Analyse une adresse SSH de dépôt. Lève une Error au message français fixe (jamais l'entrée recopiée). */
export const parseGitRemote = (input: unknown): WebGitRemote => {
  if (typeof input !== 'string') return fail()
  const url = input.trim()
  if (!url || url.length > 500 || /[\x00-\x20\x7f%\\"'`]/.test(url) || url.startsWith('-')) fail()
  let user = WEB_GIT_DEFAULT_USER,
    host = '',
    port = WEB_GIT_DEFAULT_PORT,
    path = ''
  if (url.indexOf('://') >= 0) {
    const match = /^ssh:\/\/(?:([^@\/:]+)@)?([^@\/:\[\]]+)(?::([0-9]{1,5}))?(\/.*)$/.exec(url)
    if (!match) return fail()
    if (match[1] !== undefined) user = match[1]
    host = match[2]!.toLowerCase()
    if (match[3] !== undefined) {
      if (!/^[1-9][0-9]{0,4}$/.test(match[3]) || Number(match[3]) > 65535) fail()
      port = Number(match[3])
    }
    path = match[4]!
  } else {
    // Forme scp : le premier « : » sépare l'hôte du chemin, sans « / » avant lui.
    const match = /^(?:([^@\/:]+)@)?([^@\/:\[\]]+):([^:]*)$/.exec(url)
    if (!match) return fail()
    if (match[1] !== undefined) user = match[1]
    host = match[2]!.toLowerCase()
    path = match[3]!
  }
  if (!USER.test(user) || !webGitHostValid(host)) fail()
  path = cleanPath(path)
  const remote = { host, port, sshUser: user, path, canonical: '' }
  remote.canonical = canonicalGitRemote(remote)
  // `git:` + canonical doit tenir dans les champs repository (250 caractères).
  if (remote.canonical.length > 240) fail()
  return remote
}

/**
 * Forges dont les chemins ignorent la casse et où `ssh://hôte/o/r` désigne le même dépôt que `hôte:o/r`. Ailleurs (sshd
 * classique, auto-hébergé), un chemin relatif part du dossier personnel de l'utilisateur SSH, un chemin absolu non, et
 * la casse compte : ces informations restent dans la clé pour que deux dépôts distincts n'aient jamais la même source.
 */
const CASE_INSENSITIVE_FORGES = ['github.com', 'gitlab.com', 'bitbucket.org']
/**
 * Clé d'unicité d'un dépôt : `[utilisateur@]hôte[:port]/chemin` sans `.git`, hôte en minuscules ; l'utilisateur
 * n'apparaît que s'il diffère de `git`. GitHub, GitLab.com, Bitbucket : chemin en minuscules, sans `/` initial. Autres
 * hôtes : casse conservée, chemin absolu gardé tel quel (`hôte//srv/depot`) pour le distinguer du relatif
 * (`hôte/srv/depot`).
 */
export const canonicalGitRemote = (remote: Pick<WebGitRemote, 'host' | 'port' | 'path'> & { sshUser?: string }) => {
  const host = remote.host.toLowerCase(),
    user = remote.sshUser && remote.sshUser !== WEB_GIT_DEFAULT_USER ? `${remote.sshUser}@` : '',
    port = remote.port && remote.port !== WEB_GIT_DEFAULT_PORT ? `:${remote.port}` : '',
    path = CASE_INSENSITIVE_FORGES.indexOf(host) >= 0 ? remote.path.replace(/^\/+/, '').toLowerCase() : remote.path
  return `${user}${host}${port}/${path.replace(/\.git$/, '')}`
}

/** Valeur de `web_apps.config.repository` pour une source Git (ne peut pas égaler un `owner/repo` GitHub). */
export const webGitRepository = (remote: Pick<WebGitRemote, 'canonical'>) => `git:${remote.canonical}`

/** Adresse affichable, au format scp sur le port 22, ssh:// sinon. */
export const webGitDisplayUrl = (remote: Pick<WebGitRemote, 'host' | 'port' | 'sshUser' | 'path'>) =>
  remote.port === WEB_GIT_DEFAULT_PORT
    ? `${remote.sshUser}@${remote.host}:${remote.path}`
    : `ssh://${remote.sshUser}@${remote.host}:${remote.port}${remote.path.startsWith('/') ? '' : '/'}${remote.path}`

export const webGitProvider = (host: string): WebGitProvider => {
  const value = `${host || ''}`.toLowerCase()
  if (value === 'github.com') return 'github'
  if (value === 'gitlab.com') return 'gitlab'
  if (value === 'bitbucket.org') return 'bitbucket'
  return 'other'
}

/** Branche acceptée par validateConfig (même règle que pour GitHub). */
export const webGitBranchValid = (branch: unknown): branch is string =>
  typeof branch === 'string' &&
  /^[A-Za-z0-9_./-]{1,200}$/.test(branch) &&
  branch.indexOf('..') < 0 &&
  !branch.startsWith('-') &&
  !branch.startsWith('/') &&
  !branch.endsWith('/') &&
  !branch.endsWith('.lock')

/**
 * Clés d'hôte publiées par les hébergeurs (épinglage sans TOFU, port 22 uniquement). Sources officielles :
 *
 * - GitHub : https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints
 * - GitLab.com : https://docs.gitlab.com/user/gitlab_com/#ssh-known_hosts-entries
 * - Bitbucket Cloud : https://support.atlassian.com/bitbucket-cloud/docs/configure-ssh-and-two-step-verification/ («
 *   bitbucket.org known_hosts ») Les empreintes sont revérifiées par scripts/test-web-git-source-transport.py
 *   (ssh-keygen -lf) et webGit.test.ts.
 */
export const WEB_GIT_HOST_KEY_PRESETS: Record<string, WebGitHostKey[]> = {
  'github.com': [
    {
      type: 'ssh-ed25519',
      key: 'AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl',
      fingerprint: 'SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU',
    },
    {
      type: 'ecdsa-sha2-nistp256',
      key: 'AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQEezOmxkZMy7opKgwFB9nkt5YRrYMjNuG5N87uRgg6CLrbo5wAdT/y6v0mKV0U2w0WZ2YB/++Tpockg=',
      fingerprint: 'SHA256:p2QAMXNIC1TJYWeIOttrVc98/R1BUFWu3/LiyKgUfQM',
    },
    {
      type: 'ssh-rsa',
      key: 'AAAAB3NzaC1yc2EAAAADAQABAAABgQCj7ndNxQowgcQnjshcLrqPEiiphnt+VTTvDP6mHBL9j1aNUkY4Ue1gvwnGLVlOhGeYrnZaMgRK6+PKCUXaDbC7qtbW8gIkhL7aGCsOr/C56SJMy/BCZfxd1nWzAOxSDPgVsmerOBYfNqltV9/hWCqBywINIR+5dIg6JTJ72pcEpEjcYgXkE2YEFXV1JHnsKgbLWNlhScqb2UmyRkQyytRLtL+38TGxkxCflmO+5Z8CSSNY7GidjMIZ7Q4zMjA2n1nGrlTDkzwDCsw+wqFPGQA179cnfGWOWRVruj16z6XyvxvjJwbz0wQZ75XK5tKSb7FNyeIEs4TT4jk+S4dhPeAUC5y+bDYirYgM4GC7uEnztnZyaVWQ7B381AK4Qdrwt51ZqExKbQpTUNn+EjqoTwvqNj4kqx5QUCI0ThS/YkOxJCXmPUWZbhjpCg56i+2aB6CmK2JGhn57K5mj0MNdBXA4/WnwH6XoPWJzK5Nyu2zB3nAZp+S5hpQs+p1vN1/wsjk=',
      fingerprint: 'SHA256:uNiVztksCsDhcc0u9e8BujQXVUpKZIDTMczCvj3tD2s',
    },
  ],
  'gitlab.com': [
    {
      type: 'ssh-ed25519',
      key: 'AAAAC3NzaC1lZDI1NTE5AAAAIAfuCHKVTjquxvt6CM6tdG4SLp1Btn/nOeHHE5UOzRdf',
      fingerprint: 'SHA256:eUXGGm1YGsMAS7vkcx6JOJdOGHPem5gQp4taiCfCLB8',
    },
    {
      type: 'ecdsa-sha2-nistp256',
      key: 'AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBFSMqzJeV9rUzU4kWitGjeR4PWSa29SPqJ1fVkhtj3Hw9xjLVXVYrU9QlYWrOLXBpQ6KWjbjTDTdDkoohFzgbEY=',
      fingerprint: 'SHA256:HbW3g8zUjNSksFbqTiUWPWg2Bq1x8xdGUrliXFzSnUw',
    },
    {
      type: 'ssh-rsa',
      key: 'AAAAB3NzaC1yc2EAAAADAQABAAABAQCsj2bNKTBSpIYDEGk9KxsGh3mySTRgMtXL583qmBpzeQ+jqCMRgBqB98u3z++J1sKlXHWfM9dyhSevkMwSbhoR8XIq/U0tCNyokEi/ueaBMCvbcTHhO7FcwzY92WK4Yt0aGROY5qX2UKSeOvuP4D6TPqKF1onrSzH9bx9XUf2lEdWT/ia1NEKjunUqu1xOB/StKDHMoX4/OKyIzuS0q/T1zOATthvasJFoPrAjkohTyaDUz2LN5JoH839hViyEG82yB+MjcFV5MU3N1l1QL3cVUCh93xSaua1N85qivl+siMkPGbO5xR/En4iEY6K2XPASUEMaieWVNTRCtJ4S8H+9',
      fingerprint: 'SHA256:ROQFvPThGrW4RuWLoL9tq9I9zJ42fK4XywyRtbOz/EQ',
    },
  ],
  'bitbucket.org': [
    {
      type: 'ssh-ed25519',
      key: 'AAAAC3NzaC1lZDI1NTE5AAAAIIazEu89wgQZ4bqs3d63QSMzYVa0MuJ2e2gKTKqu+UUO',
      fingerprint: 'SHA256:ybgmFkzwOSotHTHLJgHO0QN8L0xErw6vd0VhFA9m3SM',
    },
    {
      type: 'ecdsa-sha2-nistp256',
      key: 'AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBPIQmuzMBuKdWeF4+a2sjSSpBK0iqitSQ+5BM9KhpexuGt20JpTVM7u5BDZngncgrqDMbWdxMWWOGtZ9UgbqgZE=',
      fingerprint: 'SHA256:FC73VB6C4OQLSCrjEayhMp9UMxS97caD/Yyi2bhW/J0',
    },
    {
      type: 'ssh-rsa',
      key: 'AAAAB3NzaC1yc2EAAAADAQABAAABgQDQeJzhupRu0u0cdegZIa8e86EG2qOCsIsD1Xw0xSeiPDlCr7kq97NLmMbpKTX6Esc30NuoqEEHCuc7yWtwp8dI76EEEB1VqY9QJq6vk+aySyboD5QF61I/1WeTwu+deCbgKMGbUijeXhtfbxSxm6JwGrXrhBdofTsbKRUsrN1WoNgUa8uqN1Vx6WAJw1JHPhglEGGHea6QICwJOAr/6mrui/oB7pkaWKHj3z7d1IC4KWLtY47elvjbaTlkN04Kc/5LFEirorGYVbt15kAUlqGM65pk6ZBxtaO3+30LVlORZkxOh+LKL/BvbZ/iRNhItLqNyieoQj/uh/7Iv4uyH/cV/0b4WDSd3DptigWq84lJubb9t/DnZlrJazxyDCulTmKdOR7vs9gMTo+uoIrPSb8ScTtvw65+odKAlBj59dhnVp9zd7QUojOpXlL62Aw56U4oO+FALuevvMjiWeavKhJqlR7i5n9srYcrNV7ttmDw7kf/97P5zauIhxcjX+xHv4M=',
      fingerprint: 'SHA256:46OSHA1Rmj8E8ERTC6xkNcmGOw9oFxYr0WF6zWW8l1E',
    },
  ],
}

/** Clés publiées pour cet hôte sur le port 22, sinon [] (TOFU avec confirmation explicite des empreintes). */
export const webGitHostKeyPreset = (host: string, port: number = WEB_GIT_DEFAULT_PORT): WebGitHostKey[] =>
  port === WEB_GIT_DEFAULT_PORT && Object.prototype.hasOwnProperty.call(WEB_GIT_HOST_KEY_PRESETS, host)
    ? WEB_GIT_HOST_KEY_PRESETS[host]!.map((k) => ({ ...k }))
    : []

/** Ensemble d'empreintes normalisé (trié, sans doublon) pour comparer une confirmation à un scan. */
export const webGitFingerprintSet = (values: unknown): string[] => {
  if (!Array.isArray(values)) return []
  const out: string[] = []
  for (const value of values)
    if (typeof value === 'string' && /^SHA256:[A-Za-z0-9+\/]{43}$/.test(value) && out.indexOf(value) < 0)
      out.push(value)
  return out.sort()
}
