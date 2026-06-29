export const resetInstancesIdle = (app: core.App) => {
  const records = (() => {
    try {
      return app.findRecordsByFilter(`instances`, `status != 'idle'`).filter((r): r is models.Record => !!r)
    } catch {
      return []
    }
  })()
  let reset = 0

  for (const record of records) {
    record.set(`status`, `idle`)
    app.save(record)
    reset++
  }

  return reset
}
