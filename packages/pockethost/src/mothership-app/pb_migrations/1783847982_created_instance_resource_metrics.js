/// <reference path="../src/types/types.d.ts" />
migrate(
  (app) => {
    const instances = app.findCollectionByNameOrId('etae8tuiaxl6xfv')

    instances.fields.add(
      new BoolField({
        help: 'Enregistre une mesure CPU/RAM par minute pendant 7 jours.',
        hidden: false,
        system: false,
        id: 'metrics7d1',
        name: 'metricsHistoryEnabled',
        required: false,
        presentable: false,
      })
    )

    app.save(instances)

    const metrics = new Collection({
      id: 'instmetrics7d01',
      name: 'instance_resource_metrics',
      type: 'base',
      system: false,
      fields: [
        {
          id: 'irmInst1',
          name: 'instance',
          type: 'relation',
          system: false,
          hidden: false,
          required: true,
          presentable: false,
          collectionId: 'etae8tuiaxl6xfv',
          cascadeDelete: true,
          minSelect: 0,
          maxSelect: 1,
        },
        {
          id: 'irmCpu01',
          name: 'cpuPercent',
          type: 'number',
          system: false,
          hidden: false,
          required: false,
          presentable: false,
          min: 0,
          max: null,
          onlyInt: false,
        },
        {
          id: 'irmMem01',
          name: 'memoryBytes',
          type: 'number',
          system: false,
          hidden: false,
          required: false,
          presentable: false,
          min: 0,
          max: null,
          onlyInt: true,
        },
        {
          id: 'irmMlim1',
          name: 'memoryLimitBytes',
          type: 'number',
          system: false,
          hidden: false,
          required: false,
          presentable: false,
          min: 0,
          max: null,
          onlyInt: true,
        },
        {
          id: 'irmMper1',
          name: 'memoryPercent',
          type: 'number',
          system: false,
          hidden: false,
          required: false,
          presentable: false,
          min: 0,
          max: null,
          onlyInt: false,
        },
      ],
      indexes: [
        'CREATE INDEX `idx_instance_resource_metrics_instance_created` ON `instance_resource_metrics` (`instance`, `created`)',
        'CREATE INDEX `idx_instance_resource_metrics_created` ON `instance_resource_metrics` (`created`)',
      ],
      listRule: null,
      viewRule: null,
      createRule: null,
      updateRule: null,
      deleteRule: null,
    })

    return app.save(metrics)
  },
  (app) => {
    const metrics = app.findCollectionByNameOrId('instmetrics7d01')
    app.delete(metrics)

    const instances = app.findCollectionByNameOrId('etae8tuiaxl6xfv')
    instances.fields.removeById('metrics7d1')
    return app.save(instances)
  }
)
