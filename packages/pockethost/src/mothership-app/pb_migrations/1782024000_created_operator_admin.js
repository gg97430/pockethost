/// <reference path="../pb_data/types.d.ts" />
migrate(
  (app) => {
    const users = app.findCollectionByNameOrId('users')

    if (users.fields.fieldNames().indexOf('superAdmin') < 0) {
      users.fields.add(
        new BoolField({
          help: 'Acces au backoffice operateur.',
          hidden: false,
          system: false,
          id: 'usradm001',
          name: 'superAdmin',
          required: false,
          presentable: false,
        })
      )
      users.indexes.push('CREATE INDEX `idx_users_super_admin` ON `users` (`superAdmin`)')
      app.save(users)
    }

    const hasSuperAdmin = (() => {
      try {
        app.findFirstRecordByFilter('users', 'superAdmin = true')
        return true
      } catch {
        return false
      }
    })()

    if (!hasSuperAdmin) {
      const initialEmail = $os.getenv('PH_INITIAL_SUPERADMIN_EMAIL') || $os.getenv('MOTHERSHIP_ADMIN_USERNAME') || ''
      const firstAdmin = (() => {
        if (initialEmail) {
          try {
            return app.findFirstRecordByData('users', 'email', initialEmail)
          } catch {}
        }
        try {
          return app.findFirstRecordByFilter('users', 'verified = true', 'created')
        } catch {}
        try {
          return app.findFirstRecordByFilter('users', 'id != ""', 'created')
        } catch {}
        return null
      })()

      if (firstAdmin) {
        firstAdmin.set('superAdmin', true)
        app.save(firstAdmin)
      }
    }

    const settings = app.findCollectionByNameOrId('settings')
    const existingSettings = (() => {
      try {
        return app.findFirstRecordByData('settings', 'name', 'operator_settings')
      } catch {
        return null
      }
    })()

    if (!existingSettings) {
      const record = new Record(settings)
      record.set('name', 'operator_settings')
      record.set('value', {
        publicSignupEnabled: false,
        autoVerifyUsers: true,
        defaultUserQuota: 250,
        defaultSubscription: 'free',
        defaultInstancePower: true,
        defaultInstanceDevMode: true,
        defaultSyncAdmin: true,
        defaultAutoVacuum: true,
        supportEmail: '',
        maintenanceMessage: '',
        notes: '',
      })
      app.save(record)
    }
  },
  (app) => {
    const settingsRecord = (() => {
      try {
        return app.findFirstRecordByData('settings', 'name', 'operator_settings')
      } catch {
        return null
      }
    })()
    if (settingsRecord) app.delete(settingsRecord)

    const users = app.findCollectionByNameOrId('users')
    if (users.fields.fieldNames().indexOf('superAdmin') >= 0) {
      users.fields.removeById('usradm001')
      users.indexes = users.indexes.filter((index) => index.indexOf('idx_users_super_admin') < 0)
      app.save(users)
    }
  }
)
