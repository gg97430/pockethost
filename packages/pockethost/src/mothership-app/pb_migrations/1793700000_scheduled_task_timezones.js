/// <reference path="../src/types/types.d.ts" />
migrate(
  (app) => {
    const collection = app.findCollectionByNameOrId('web_scheduled_tasks')
    for (const field of [
      { name: 'timezone', type: 'text', max: 40 },
      { name: 'catchUp', type: 'text', max: 10 },
      { name: 'maxCatchUpDelayMinutes', type: 'number', min: 0, max: 1440, onlyInt: true },
      { name: 'lastMissedAt', type: 'date' },
    ])
      if (!collection.fields.getByName(field.name)) collection.fields.add(new Field(field))
    app.save(collection)
    const runs = app.findCollectionByNameOrId('web_task_runs')
    const index = 'CREATE INDEX idx_web_task_success ON web_task_runs (taskId,status,finishedAt DESC)'
    if (!runs.indexes.includes(index)) {
      runs.indexes.push(index)
      app.save(runs)
    }
  },
  () => {
    /* Additive metadata stays available after code rollback. */
  }
)
