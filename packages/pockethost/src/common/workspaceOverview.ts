import type { PlatformResourceHost } from './platformResources'

export type WorkspaceOverview = {
  collectedAt: string
  webEnabled: boolean
  canCreateBase: boolean
  canCreateApp: boolean
  counts: {
    databases: number
    databasesRunning: number
    databasesOff: number
    databasesFailed: number
    applications: number
    applicationsRunning: number
    applicationsStopped: number
    applicationsFailed: number
    projects: number
    projectsWithErrors: number
    deploymentsInProgress: number
    deploymentsQueued: number
    autoDeploy: number
  }
  deployments24h: { ready: number; failed: number; cancelled: number }
  recentDeployments: {
    id: string
    appId: string
    appName: string
    action: string
    status: string
    commit: string
    created: string
    finishedAt: string
  }[]
  attention: {
    id: string
    name: string
    kind: 'database' | 'application'
    status: string
    servingPreviousVersion: boolean
  }[]
  resources: { host: PlatformResourceHost; collectedAt: string; stale: boolean } | null
}
