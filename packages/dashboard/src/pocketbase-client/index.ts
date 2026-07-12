import { PUBLIC_MOTHERSHIP_URL } from '$lib/appEnv'
import { createPocketbaseClient, type PocketbaseClient } from './PocketbaseClient'

export type {
  DashboardInstanceMetric,
  DashboardInstanceMetricsResponse,
  InstanceBackup,
  InstanceBackupPolicy,
  InstanceBackupPolicyResponse,
  InstanceLitestreamPolicy,
  InstanceLitestreamPolicyResponse,
  InstanceMetricResponse,
  InstanceOverview,
  InstanceOverviewBackup,
  OperatorAdminOverview,
  OperatorDiskCleanupEntry,
  OperatorDiskCleanupResult,
  OperatorSettings,
  OperatorUser,
  UpdateInstanceBackupPolicyInput,
  UpdateInstanceLitestreamPolicyInput,
  UploadProgress,
} from './PocketbaseClient'

export const client = (() => {
  let clientInstance: PocketbaseClient | undefined
  return () => {
    if (clientInstance) return clientInstance

    const url = PUBLIC_MOTHERSHIP_URL
    clientInstance = createPocketbaseClient({ url })
    return clientInstance
  }
})()
