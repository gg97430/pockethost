export { nextWebCron, parseWebCron } from '../../../pockethost/src/common/webCron'
export type WebTaskRun = {
  id: string
  taskId: string
  taskName: string
  trigger: string
  status: string
  scheduledAt: string
  startedAt: string
  finishedAt: string
  deadline: string
  durationMs: number
  exitCode: number | null
  log: string
  truncated: boolean
  error: string
  created: string
}
export type WebScheduledTask = {
  id: string
  name: string
  command: string
  schedule: string
  timezone: 'UTC' | 'Indian/Reunion'
  catchUp: 'once' | 'skip'
  maxCatchUpDelayMinutes: number
  lastMissedAt: string
  lastSuccessAt: string
  late: boolean
  enabled: boolean
  timeoutSeconds: number
  nextRunAt: string
  lastRun: WebTaskRun | null
  updated: string
}
