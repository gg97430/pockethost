export const restoreActivityState = (
  backupId: string,
  activeRestoreIds: string[],
  observedAt: number,
  operationStartedAt = 0
): boolean | null => {
  if (!backupId || !Number.isFinite(observedAt) || observedAt <= 0) return null
  if (operationStartedAt > 0 && observedAt < operationStartedAt) return null
  return activeRestoreIds.includes(backupId)
}
