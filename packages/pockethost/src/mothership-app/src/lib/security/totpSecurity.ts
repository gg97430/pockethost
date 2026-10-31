import { totpUri, verifyTotp } from '$common/totp'
import qrcode from 'qrcode-generator'

const TABLE = 'ph_two_factor'
const fields = () => [
  { name: 'authCollection', type: 'text', required: true, max: 100 },
  { name: 'authRecord', type: 'text', required: true, max: 100 },
  { name: 'enabled', type: 'bool' },
  { name: 'secret', type: 'text', hidden: true, max: 1000 },
  { name: 'pendingSecret', type: 'text', hidden: true, max: 1000 },
  { name: 'nonce', type: 'text', hidden: true, max: 100 },
  { name: 'recoveryHashes', type: 'json', hidden: true, maxSize: 10000 },
  { name: 'lastCounter', type: 'number', onlyInt: true },
  { name: 'failures', type: 'number', onlyInt: true },
  { name: 'lockedUntil', type: 'date' },
  { name: 'pendingUntil', type: 'date' },
]

export const createTotpSecurity = (config: { authCollection: string; issuer: string }) => {
  const keyPath = () => `${$app.dataDir()}/.pockethost-totp.key`
  const readKey = () => toString($os.readFile(keyPath())).trim()
  const decrypt = (value: string) => toString($security.decrypt(value, readKey()))
  const data = (e: core.RequestEvent) => JSON.parse(JSON.stringify(e.requestInfo().body || {})) as Record<string, any>
  const json = (e: core.RequestEvent, value: unknown) => {
    e.response.header().set('Cache-Control', 'no-store')
    return e.json(200, value)
  }
  const findState = (record: core.Record, app: any = $app): core.Record | null => {
    return (
      app.findRecordsByFilter(TABLE, 'authCollection = {:collection} && authRecord = {:id}', '', 1, 0, {
        collection: record.collection().id,
        id: record.id,
      })[0] || null
    )
  }
  const requireAuth = (e: core.RequestEvent) => {
    if (!e.auth || e.auth.collection().name !== config.authCollection) throw new UnauthorizedError('Connexion requise.')
    return e.auth
  }
  const recoveryHashes = (state: core.Record) => JSON.parse(state.getString('recoveryHashes') || '[]') as string[]
  const failure = (state: core.Record, app: any) => {
    const attempts = state.getInt('failures') + 1
    state.set('failures', attempts)
    if (attempts >= 5) state.set('lockedUntil', new Date(Date.now() + 5 * 60_000).toISOString())
    app.save(state)
  }
  const assertUnlocked = (state: core.Record) => {
    if (Date.parse(state.getString('lockedUntil')) > Date.now())
      throw new TooManyRequestsError('Trop de tentatives. Réessayez dans cinq minutes.')
  }
  const ensureState = (record: core.Record) => {
    let state = findState(record)
    if (state) return state
    $app.runInTransaction((app) => {
      state = findState(record, app)
      if (state) return
      state = new Record(app.findCollectionByNameOrId(TABLE))
      state.set('authCollection', record.collection().id)
      state.set('authRecord', record.id)
      state.set('lastCounter', -1)
      state.set('recoveryHashes', [])
      app.save(state)
    })
    return state!
  }
  const assertPassword = (e: core.RequestEvent, record: core.Record) => {
    const state = ensureState(record)
    assertUnlocked(state)
    if (record.validatePassword(`${data(e).password || ''}`)) return
    $app.runInTransaction((app) => failure(findState(record, app)!, app))
    throw new UnauthorizedError('Mot de passe incorrect.')
  }
  const generateRecovery = () =>
    Array.from({ length: 10 }, () => {
      const raw = $security.randomStringWithAlphabet(20, 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789')
      return raw.match(/.{1,5}/g)!.join('-')
    })
  const hashRecovery = (code: string) => $security.sha256(code.replace(/[\s-]/g, '').toUpperCase())
  const codeError = (message: string) =>
    new ApiError(401, message, {
      totpRequired: new ValidationError('totp_required', message),
    })
  const verify = (
    record: core.Record,
    raw: string,
    pending = false,
    onSuccess?: (state: core.Record, app: any) => void
  ) => {
    const code = raw.trim().replace(/\s/g, '')
    if (!code) throw codeError('Saisissez le code de votre application d’authentification ou un code de secours.')
    let success = false
    let result: core.Record | null = null
    $app.runInTransaction((app) => {
      const state = findState(record, app)
      if (!state) throw codeError('Authentification à deux facteurs non configurée.')
      assertUnlocked(state)
      if (pending && Date.parse(state.getString('pendingUntil')) <= Date.now())
        throw new BadRequestError('Configuration expirée. Recommencez.')
      const encrypted = state.getString(pending ? 'pendingSecret' : 'secret')
      if (!encrypted || (!pending && !state.getBool('enabled')))
        throw new BadRequestError('Authentification à deux facteurs non configurée.')
      const counter = /^\d{6}$/.test(code)
        ? verifyTotp(decrypt(encrypted), code, pending ? -1 : state.getInt('lastCounter'))
        : null
      let hashes = recoveryHashes(state)
      let usedRecovery = -1
      if (counter === null && !pending && code.length <= 80) {
        const digest = hashRecovery(code)
        usedRecovery = hashes.findIndex((hash) => $security.equal(hash, digest))
      }
      if (counter === null && usedRecovery < 0) {
        failure(state, app)
        return
      }
      if (counter !== null) state.set('lastCounter', counter)
      if (usedRecovery >= 0) {
        hashes = hashes.filter((_, index) => index !== usedRecovery)
        state.set('recoveryHashes', hashes)
      }
      state.set('failures', 0)
      state.set('lockedUntil', '')
      onSuccess?.(state, app)
      app.save(state)
      success = true
      result = state
    })
    if (!success) throw codeError('Code incorrect, expiré ou déjà utilisé.')
    return result!
  }
  const claims = (token: string) =>
    JSON.parse(JSON.stringify($security.parseUnverifiedJWT(token))) as Record<string, any>
  const markToken = (token: string, record: core.Record, state: core.Record) => {
    const payload = claims(token)
    return $security.createJWT(
      { ...payload, ph_totp: state.getString('nonce') },
      record.getString('tokenKey') + record.collection().authToken.secret,
      Math.max(1, Number(payload.exp) - Math.floor(Date.now() / 1000))
    )
  }
  const requestHeaders = (e: core.RequestEvent) =>
    JSON.parse(JSON.stringify(e.requestInfo().headers || {})) as Record<string, string>
  const tokenFromRequest = (e: core.RequestEvent) =>
    (requestHeaders(e).authorization || '').replace(/^Bearer\s+/i, '').trim()
  const hasProof = (token: string, state: core.Record) => {
    try {
      return $security.equal(`${claims(token).ph_totp || ''}`, state.getString('nonce'))
    } catch {
      return false
    }
  }
  const rotateToken = (record: core.Record, app: any, state?: core.Record) => {
    const current = app.findRecordById(record.collection().id, record.id)
    current.set('tokenKey', $security.randomString(50))
    app.save(current)
    const token = current.newAuthToken()
    return state ? markToken(token, current, state) : token
  }

  return {
    // Called only after a server-side exchange of an authorized, instance-bound dashboard ticket.
    trustedToken: (record: core.Record) => {
      const state = findState(record)
      const token = record.newAuthToken()
      return state?.getBool('enabled') ? markToken(token, record, state) : token
    },
    initialize: () => {
      try {
        $app.findCollectionByNameOrId(TABLE)
      } catch {
        const collection = new Collection({
          name: TABLE,
          type: 'base',
          listRule: null,
          viewRule: null,
          createRule: null,
          updateRule: null,
          deleteRule: null,
          fields: fields(),
          indexes: ['CREATE UNIQUE INDEX idx_ph_two_factor_account ON ph_two_factor (authCollection, authRecord)'],
        })
        $app.save(collection)
      }
      if (
        !(() => {
          try {
            $os.stat(keyPath())
            return true
          } catch {
            return false
          }
        })()
      ) {
        if ($app.findRecordsByFilter(TABLE, 'secret != "" || pendingSecret != ""', '', 1).length) {
          throw new Error('Clé TOTP manquante : restaurez .pockethost-totp.key avec la base de données.')
        }
        $os.writeFile(keyPath(), $security.randomString(32), 0o600 as any)
      }
      if (readKey().length !== 32) throw new Error('Clé de chiffrement TOTP invalide.')
      // A root-running instance container must leave this private file readable by the host backup service.
      const hostUid = $os.getenv('PH_HOST_UID')
      const hostGid = $os.getenv('PH_HOST_GID')
      if (/^\d+$/.test(hostUid) && /^\d+$/.test(hostGid)) {
        $os.cmd('chown', `${hostUid}:${hostGid}`, keyPath()).combinedOutput()
      }
    },
    status: (e: core.RequestEvent) => {
      const record = requireAuth(e)
      const state = findState(record)
      return json(e, {
        enabled: state?.getBool('enabled') || false,
        recoveryCodesRemaining: state ? recoveryHashes(state).length : 0,
      })
    },
    setup: (e: core.RequestEvent) => {
      const record = requireAuth(e)
      assertPassword(e, record)
      const state = ensureState(record)
      if (state.getBool('enabled')) throw new BadRequestError('Le TOTP est déjà activé.')
      const secret = $security.randomStringWithAlphabet(32, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567')
      state.set('pendingSecret', $security.encrypt(secret, readKey()))
      state.set('pendingUntil', new Date(Date.now() + 15 * 60_000).toISOString())
      $app.save(state)
      const uri = totpUri(secret, config.issuer, record.getString('email'))
      const qr = qrcode(0, 'M')
      qr.addData(uri)
      qr.make()
      return json(e, { secret, uri, qrSvg: qr.createSvgTag({ cellSize: 4, margin: 4, scalable: true }) })
    },
    confirm: (e: core.RequestEvent) => {
      const record = requireAuth(e)
      assertPassword(e, record)
      const codes = generateRecovery()
      let token = ''
      verify(record, `${data(e).code || ''}`, true, (state, app) => {
        if (state.getBool('enabled')) throw new BadRequestError('Le TOTP est déjà activé.')
        state.set('secret', state.getString('pendingSecret'))
        state.set('pendingSecret', '')
        state.set('pendingUntil', '')
        state.set('enabled', true)
        state.set('nonce', $security.randomString(48))
        state.set('recoveryHashes', codes.map(hashRecovery))
        token = rotateToken(record, app, state)
      })
      return json(e, { enabled: true, token, recoveryCodes: codes })
    },
    disable: (e: core.RequestEvent) => {
      const record = requireAuth(e)
      assertPassword(e, record)
      let token = ''
      verify(record, `${data(e).code || ''}`, false, (state, app) => {
        state.set('enabled', false)
        state.set('secret', '')
        state.set('pendingSecret', '')
        state.set('nonce', '')
        state.set('recoveryHashes', [])
        token = rotateToken(record, app)
      })
      return json(e, { enabled: false, token })
    },
    regenerate: (e: core.RequestEvent) => {
      const record = requireAuth(e)
      assertPassword(e, record)
      const codes = generateRecovery()
      verify(record, `${data(e).code || ''}`, false, (state) => state.set('recoveryHashes', codes.map(hashRecovery)))
      return json(e, { recoveryCodes: codes })
    },
    authenticate: (e: core.RecordAuthRequestEvent) => {
      if (!e.record) return e.next()
      const state = findState(e.record)
      if (!state?.getBool('enabled')) return e.next()
      if (!e.authMethod && hasProof(tokenFromRequest(e), state)) {
        e.token = markToken(e.token, e.record, state)
        return e.next()
      }
      const verified = verify(e.record, `${data(e).totpCode || requestHeaders(e).x_pockethost_totp || ''}`)
      e.token = markToken(e.token, e.record, verified)
      return e.next()
    },
    guard: (e: core.RequestEvent) => {
      const path = e.request?.url?.path || ''
      if (/\/auth-with-(password|oauth2|otp)$/.test(path)) return e.next()
      const token = tokenFromRequest(e)
      if (!token) return e.next()
      let record: core.Record | undefined
      try {
        const payload = claims(token)
        const collection = $app.findCachedCollectionByNameOrId(config.authCollection)
        if (payload.collectionId === collection.id && payload.type === 'auth')
          record = $app.findAuthRecordByToken(token, 'auth')
      } catch {}
      if (!record) return e.next()
      const state = findState(record)
      if (state?.getBool('enabled') && !hasProof(token, state))
        throw codeError('Une connexion avec le second facteur est requise.')
      return e.next()
    },
  }
}
