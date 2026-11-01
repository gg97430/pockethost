/// <reference path="../pb_data/types.d.ts" />
migrate(
  (app) => {
    const users = app.findCollectionByNameOrId('users')
    const instances = app.findCollectionByNameOrId('instances')
    users.fields.add(
      new RelationField({
        id: 'usrbases001',
        name: 'managedInstances',
        collectionId: instances.id,
        maxSelect: 1000,
        cascadeDelete: false,
      })
    )
    // Profile edits and public signup must never grant operator privileges or base access.
    if (users.createRule !== null)
      users.createRule = `(${`${users.createRule}` || '1=1'}) && @request.body.superAdmin:isset = false && @request.body.managedInstances:isset = false`
    if (users.updateRule !== null)
      users.updateRule = `(${`${users.updateRule}` || '1=1'}) && @request.body.superAdmin:changed = false && @request.body.managedInstances:changed = false`
    app.save(users)

    const access =
      '@request.auth.id != "" && @request.auth.suspension = "" && (uid = @request.auth.id || @request.auth.superAdmin = true || @request.auth.managedInstances.id ?= id)'
    instances.listRule = access
    instances.viewRule = access
    app.save(instances)
  },
  (app) => {
    const instances = app.findCollectionByNameOrId('instances')
    instances.listRule = 'uid=@request.auth.id'
    instances.viewRule = 'uid = @request.auth.id'
    app.save(instances)
    const users = app.findCollectionByNameOrId('users')
    for (const key of ['createRule', 'updateRule']) {
      const suffix =
        key === 'createRule'
          ? ' && @request.body.superAdmin:isset = false && @request.body.managedInstances:isset = false'
          : ' && @request.body.superAdmin:changed = false && @request.body.managedInstances:changed = false'
      if (users[key] !== null && `${users[key]}`.endsWith(suffix)) {
        const original = `${users[key]}`.slice(1, -suffix.length - 1)
        users[key] = original === '1=1' ? '' : original
      }
    }
    users.fields.removeById('usrbases001')
    app.save(users)
  }
)
