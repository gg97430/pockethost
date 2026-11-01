onBootstrap((e) => {
  e.next()
  const { mkLog } = /** @type {Lib} */ (require(`${__hooks}/_ph_lib.js`))

  const log = mkLog(`admin-sync`)

  const { id, email, tokenKey, passwordHash, preserveExistingNativeIdentity } = (() => {
    try {
      return /** @type{{id:string, email:string, tokenKey:string,passwordHash:string,preserveExistingNativeIdentity?:boolean}} */ (
        JSON.parse(process.env.ADMIN_SYNC)
      )
    } catch (e) {
      return { id: '', email: '', tokenKey: '', passwordHash: '' }
    }
  })()

  if (!email) {
    log(`Not active - skipped`)
    return
  }
  // A dashboard ownership merge must never rewrite an imported native account.
  // This also preserves manual native password changes and all MFA state.
  if (preserveExistingNativeIdentity === true) {
    log(`Native admin identity preserved - sync skipped`)
    return
  }

  const update = (app) =>
    app
      .db()
      .newQuery(
        `
        insert or replace into _superusers (id, email, tokenKey, password) values ({:id}, {:email}, {:tokenKey}, {:passwordHash})
          `
      )
      .bind({ id, email, tokenKey, passwordHash })
      .execute()

  try {
    e.app.runInTransaction((app) => {
      const previous = app.findRecordsByFilter('_superusers', 'email = {:email}', '', 1, 0, { email })[0]
      update(app)
      if (!previous || previous.id === id) return
      // A transfer to another PocketHost changes the owner id, while the same email keeps its TOTP enrollment.
      try {
        app.findCollectionByNameOrId('ph_two_factor')
      } catch {
        return
      }
      const states = app.findRecordsByFilter(
        'ph_two_factor',
        'authCollection = {:collection} && authRecord = {:record}',
        '',
        1,
        0,
        {
          collection: previous.collection().id,
          record: previous.id,
        }
      )
      for (const state of states) {
        state.set('authRecord', id)
        app.save(state)
      }
    })
    log(`Success updating admin credentials ${email}`)
  } catch (e) {
    log(`Failed to update admin credentials ${email}: ${e}`)
  }
})
