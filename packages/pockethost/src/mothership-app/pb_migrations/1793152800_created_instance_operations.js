/// <reference path="../src/types/types.d.ts" />
migrate(
  (app) => {
    const created = { name: 'created', type: 'autodate', onCreate: true, onUpdate: false }
    const instance = { name: 'instanceId', type: 'text', required: true, max: 15 }
    for (const definition of [
      {
        name: 'instance_action_log',
        fields: [
          instance,
          { name: 'ownerId', type: 'text', required: true, max: 15 },
          { name: 'actorId', type: 'text', max: 15 },
          { name: 'actorLabel', type: 'text', max: 254 },
          { name: 'action', type: 'text', required: true, max: 100 },
          { name: 'details', type: 'json', maxSize: 10000 },
          created,
        ],
        indexes: [
          'CREATE INDEX idx_instance_action_log_instance ON instance_action_log (instanceId, created DESC)',
          'CREATE INDEX idx_instance_action_log_owner ON instance_action_log (ownerId, created DESC)',
        ],
      },
      {
        name: 'instance_backup_checks',
        fields: [
          instance,
          { name: 'backupId', type: 'text', max: 15 },
          {
            name: 'status',
            type: 'select',
            required: true,
            maxSelect: 1,
            values: ['queued', 'running', 'passed', 'failed'],
          },
          { name: 'startedAt', type: 'date' },
          { name: 'finishedAt', type: 'date' },
          { name: 'result', type: 'json', maxSize: 10000 },
          created,
        ],
        indexes: [
          'CREATE INDEX idx_instance_backup_checks_instance ON instance_backup_checks (instanceId, created DESC)',
          'CREATE INDEX idx_instance_backup_checks_status ON instance_backup_checks (status, created)',
          "CREATE UNIQUE INDEX idx_instance_backup_checks_pending ON instance_backup_checks (instanceId) WHERE status != 'passed' AND status != 'failed'",
        ],
      },
      {
        name: 'instance_backup_check_policies',
        fields: [
          instance,
          { name: 'enabled', type: 'bool' },
          { name: 'intervalDays', type: 'number', min: 1, max: 30, onlyInt: true },
          { name: 'nextRunAt', type: 'date' },
          created,
        ],
        indexes: [
          'CREATE UNIQUE INDEX idx_instance_backup_check_policy_instance ON instance_backup_check_policies (instanceId)',
        ],
      },
    ])
      app.save(
        new Collection({
          ...definition,
          type: 'base',
          listRule: null,
          viewRule: null,
          createRule: null,
          updateRule: null,
          deleteRule: null,
        })
      )
    const policies = app.findCollectionByNameOrId('instance_monitoring_policies')
    for (const name of ['disk', 'latency']) {
      policies.fields.add(new BoolField({ name: name + 'Enabled' }))
      policies.fields.add(new NumberField({ name: name + 'Threshold', min: 1, max: name === 'disk' ? 99 : 60000 }))
      policies.fields.add(new NumberField({ name: name + 'HighStreak', min: 0 }))
      policies.fields.add(new NumberField({ name: name + 'NormalStreak', min: 0 }))
      policies.fields.add(new NumberField({ name: name + 'LastValue', min: 0 }))
    }
    app.save(policies)
    const incidents = app.findCollectionByNameOrId('instance_monitoring_incidents')
    incidents.fields.getByName('type').values = ['health', 'cpu', 'memory', 'backup', 'disk', 'latency', 'backup-check']
    app.save(incidents)
  },
  (app) => {
    for (const name of ['instance_action_log', 'instance_backup_checks', 'instance_backup_check_policies'])
      app.delete(app.findCollectionByNameOrId(name))
    const policies = app.findCollectionByNameOrId('instance_monitoring_policies')
    for (const prefix of ['disk', 'latency'])
      for (const suffix of ['Enabled', 'Threshold', 'HighStreak', 'NormalStreak', 'LastValue'])
        policies.fields.removeByName(prefix + suffix)
    app.save(policies)
  }
)
