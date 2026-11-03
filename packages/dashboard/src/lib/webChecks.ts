export { emptyApplicationChecks, validateApplicationChecks } from '../../../pockethost/src/common/webChecks'
export type { ApplicationChecks, JourneyCheck, PreflightChecks } from '../../../pockethost/src/common/webChecks'
export type CheckRun = {
  id: string
  journeyId: string
  journeyName: string
  status: string
  trigger: string
  scheduledAt: string
  startedAt: string
  finishedAt: string
  result: {
    status: string
    errorCode: string
    durationMs: number
    steps: { name: string; status: string; httpStatus: number; code: string; durationMs: number }[]
  } | null
}
