routerAdd(
  'GET',
  '/api/web-apps',
  (e) => require(`${__hooks}/mothership`).HandleWebAppsList(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps',
  (e) => require(`${__hooks}/mothership`).HandleWebAppCreate(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/web-apps/settings',
  (e) => require(`${__hooks}/mothership`).HandleWebSettingsGet(e),
  $apis.requireAuth('users')
)
routerAdd(
  'PUT',
  '/api/web-apps/settings',
  (e) => require(`${__hooks}/mothership`).HandleWebSettingsPut(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/web-apps/repositories',
  (e) => require(`${__hooks}/mothership`).HandleWebRepositories(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/web-apps/repositories/{repositoryId}/branches',
  (e) => require(`${__hooks}/mothership`).HandleWebBranches(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/github/webhook',
  (e) => require(`${__hooks}/mothership`).HandleWebGithubWebhook(e),
  $apis.bodyLimit(10000000)
)
routerAdd(
  'POST',
  '/api/web-apps/github/webhook/{connectionId}',
  (e) => require(`${__hooks}/mothership`).HandleWebGithubConnectionWebhook(e),
  $apis.bodyLimit(10000000)
)
routerAdd(
  'GET',
  '/api/web-apps/github/connections',
  (e) => require(`${__hooks}/mothership`).HandleWebGithubConnectionsList(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/github/connections',
  (e) => require(`${__hooks}/mothership`).HandleWebGithubConnectionCreate(e),
  $apis.requireAuth('users')
)
routerAdd(
  'PATCH',
  '/api/web-apps/github/connections/{connectionId}',
  (e) => require(`${__hooks}/mothership`).HandleWebGithubConnectionUpdate(e),
  $apis.requireAuth('users')
)
routerAdd(
  'DELETE',
  '/api/web-apps/github/connections/{connectionId}',
  (e) => require(`${__hooks}/mothership`).HandleWebGithubConnectionDelete(e),
  $apis.requireAuth('users')
)
routerAdd('POST', '/api/web-apps/_worker/claim', (e) => require(`${__hooks}/mothership`).HandleWebWorkerClaim(e))
routerAdd('POST', '/api/web-apps/_worker/project-limits', (e) =>
  require(`${__hooks}/mothership`).HandleWebWorkerProjectLimits(e)
)
routerAdd('POST', '/api/web-apps/_worker/report', (e) => require(`${__hooks}/mothership`).HandleWebWorkerReport(e))
routerAdd('POST', '/api/web-apps/_worker/deletions/claim', (e) =>
  require(`${__hooks}/mothership`).HandleWebDeletionClaim(e)
)
routerAdd('POST', '/api/web-apps/_worker/deletions/report', (e) =>
  require(`${__hooks}/mothership`).HandleWebDeletionReport(e)
)
routerAdd(
  'DELETE',
  '/api/web-apps/{id}',
  (e) => require(`${__hooks}/mothership`).HandleWebAppDelete(e),
  $apis.requireAuth('users')
)
onRecordCreate((e) => require(`${__hooks}/mothership`).GuardWebDeletingRecord(e))
onRecordUpdate((e) => require(`${__hooks}/mothership`).GuardWebDeletingRecord(e))
routerAdd(
  'GET',
  '/api/web-apps/{id}',
  (e) => require(`${__hooks}/mothership`).HandleWebAppGet(e),
  $apis.requireAuth('users')
)
routerAdd(
  'PATCH',
  '/api/web-apps/{id}',
  (e) => require(`${__hooks}/mothership`).HandleWebAppUpdate(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/web-apps/{id}/deployments',
  (e) => require(`${__hooks}/mothership`).HandleWebDeployments(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/web-apps/{id}/deployments/{deploymentId}/logs',
  (e) => require(`${__hooks}/mothership`).HandleWebDeploymentLogs(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/{id}/{action}',
  (e) => require(`${__hooks}/mothership`).HandleWebAppAction(e),
  $apis.requireAuth('users')
)

routerAdd(
  'GET',
  '/api/web-apps/{id}/domains',
  (e) => require(`${__hooks}/mothership`).HandleWebDomainsList(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/{id}/domains',
  (e) => require(`${__hooks}/mothership`).HandleWebDomainCreate(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/{id}/domains/{domainId}/verify',
  (e) => require(`${__hooks}/mothership`).HandleWebDomainVerify(e),
  $apis.requireAuth('users')
)
routerAdd(
  'DELETE',
  '/api/web-apps/{id}/domains/{domainId}',
  (e) => require(`${__hooks}/mothership`).HandleWebDomainDelete(e),
  $apis.requireAuth('users')
)
cronAdd('web-app-maintenance', '17 */6 * * *', () => require(`${__hooks}/mothership`).HandleWebMaintenance())
// Livraisons GitHub App reçues sans connexion par l'ancien code (retour arrière du code seul) : principale.
onBootstrap((e) => {
  e.next()
  require(`${__hooks}/mothership`).normalizeWebDeliveryConnections()
})

onRecordCreateRequest((e) => require(`${__hooks}/mothership`).GuardWebInstanceDomainCreate(e), 'instances')
onRecordUpdateRequest((e) => require(`${__hooks}/mothership`).GuardWebInstanceDomainUpdate(e), 'instances')

routerAdd(
  'POST',
  '/api/web-apps/github/connect/start',
  (e) => require(`${__hooks}/mothership`).HandleWebGithubConnectStart(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/github/connect/manifest',
  (e) => require(`${__hooks}/mothership`).HandleWebGithubConnectManifest(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/github/connect/install/start',
  (e) => require(`${__hooks}/mothership`).HandleWebGithubConnectInstallStart(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/github/connect/install',
  (e) => require(`${__hooks}/mothership`).HandleWebGithubConnectInstall(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/web-apps/github/connect/status',
  (e) => require(`${__hooks}/mothership`).HandleWebGithubConnectStatus(e),
  $apis.requireAuth('users')
)
cronAdd('web-github-connect-cleanup', '43 * * * *', () =>
  require(`${__hooks}/mothership`).HandleWebGithubConnectMaintenance()
)

routerAdd(
  'POST',
  '/api/web-apps/{id}/deployments/{deploymentId}/cancel',
  (e) => require(`${__hooks}/mothership`).HandleWebDeploymentCancel(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/{id}/deployments/{deploymentId}/retry',
  (e) => require(`${__hooks}/mothership`).HandleWebDeploymentRetry(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/web-apps/{id}/runtime',
  (e) => require(`${__hooks}/mothership`).HandleWebRuntime(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/web-apps/{id}/webhooks',
  (e) => require(`${__hooks}/mothership`).HandleWebWebhookDeliveries(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/github/connect/install/check',
  (e) => require(`${__hooks}/mothership`).HandleWebGithubConnectInstallCheck(e),
  $apis.requireAuth('users')
)

routerAdd(
  'GET',
  '/api/web-apps/github/health',
  (e) => require(`${__hooks}/mothership`).HandleWebGithubHealth(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/web-apps/{id}/environment',
  (e) => require(`${__hooks}/mothership`).HandleWebEnvironmentGet(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/{id}/environment/reveal',
  (e) => require(`${__hooks}/mothership`).HandleWebEnvironmentReveal(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/{id}/environment/preview',
  (e) => require(`${__hooks}/mothership`).HandleWebEnvironmentPreview(e),
  $apis.requireAuth('users')
)
routerAdd(
  'PUT',
  '/api/web-apps/{id}/environment',
  (e) => require(`${__hooks}/mothership`).HandleWebEnvironmentPut(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/{id}/terminal/session',
  (e) => require(`${__hooks}/mothership`).HandleWebTerminalSession(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/web-apps/{id}/terminal/sessions',
  (e) => require(`${__hooks}/mothership`).HandleWebTerminalHistory(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/{id}/terminal/sessions/{sessionId}/close',
  (e) => require(`${__hooks}/mothership`).HandleWebTerminalRevoke(e),
  $apis.requireAuth('users')
)
routerAdd('POST', '/api/web-apps/_terminal/claim', (e) => require(`${__hooks}/mothership`).HandleWebTerminalClaim(e))
routerAdd('POST', '/api/web-apps/_terminal/validate', (e) =>
  require(`${__hooks}/mothership`).HandleWebTerminalValidate(e)
)
routerAdd('POST', '/api/web-apps/_terminal/close', (e) => require(`${__hooks}/mothership`).HandleWebTerminalClose(e))
cronAdd('web-terminal-cleanup', '*/5 * * * *', () => require(`${__hooks}/mothership`).HandleWebTerminalMaintenance())
routerAdd(
  'POST',
  '/api/web-apps/{id}/apply-runtime',
  (e) => require(`${__hooks}/mothership`).HandleWebApplyRuntime(e),
  $apis.requireAuth('users')
)

routerAdd(
  'GET',
  '/api/web-apps/{id}/health',
  (e) => require(`${__hooks}/mothership`).HandleWebHealthGet(e),
  $apis.requireAuth('users')
)
routerAdd(
  'PATCH',
  '/api/web-apps/{id}/health',
  (e) => require(`${__hooks}/mothership`).HandleWebHealthUpdate(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/web-apps/{id}/health/history',
  (e) => require(`${__hooks}/mothership`).HandleWebHealthHistory(e),
  $apis.requireAuth('users')
)
routerAdd('POST', '/api/web-apps/_health/config', (e) => require(`${__hooks}/mothership`).HandleWebHealthConfig(e))
routerAdd('POST', '/api/web-apps/_health/report', (e) => require(`${__hooks}/mothership`).HandleWebHealthReport(e))
routerAdd(
  'GET',
  '/api/web-apps/{id}/tasks',
  (e) => require(`${__hooks}/mothership`).HandleWebTasksList(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/{id}/tasks',
  (e) => require(`${__hooks}/mothership`).HandleWebTasksCreate(e),
  $apis.requireAuth('users')
)
routerAdd(
  'PATCH',
  '/api/web-apps/{id}/tasks/{taskId}',
  (e) => require(`${__hooks}/mothership`).HandleWebTaskUpdate(e),
  $apis.requireAuth('users')
)
routerAdd(
  'DELETE',
  '/api/web-apps/{id}/tasks/{taskId}',
  (e) => require(`${__hooks}/mothership`).HandleWebTaskDelete(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/{id}/tasks/{taskId}/run',
  (e) => require(`${__hooks}/mothership`).HandleWebTaskRun(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/web-apps/{id}/task-runs',
  (e) => require(`${__hooks}/mothership`).HandleWebTaskRuns(e),
  $apis.requireAuth('users')
)
routerAdd('POST', '/api/web-apps/_tasks/claim', (e) => require(`${__hooks}/mothership`).HandleWebTaskClaim(e))
routerAdd('POST', '/api/web-apps/_tasks/validate', (e) => require(`${__hooks}/mothership`).HandleWebTaskValidate(e))
routerAdd('POST', '/api/web-apps/_tasks/report', (e) => require(`${__hooks}/mothership`).HandleWebTaskReport(e))
cronAdd('web-health-cleanup', '17 * * * *', () => require(`${__hooks}/mothership`).HandleWebHealthMaintenance())

routerAdd(
  'GET',
  '/api/web-apps/{id}/backups',
  (e) => require(`${__hooks}/mothership`).HandleWebBackupsList(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/{id}/backups',
  (e) => require(`${__hooks}/mothership`).HandleWebBackupCreate(e),
  $apis.requireAuth('users')
)
routerAdd(
  'PUT',
  '/api/web-apps/{id}/backups/settings',
  (e) => require(`${__hooks}/mothership`).HandleWebBackupSettings(e),
  $apis.requireAuth('users')
)
routerAdd(
  'DELETE',
  '/api/web-apps/{id}/backups/{backupId}',
  (e) => require(`${__hooks}/mothership`).HandleWebBackupDelete(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/web-apps/{id}/backups/{backupId}/download',
  (e) => require(`${__hooks}/mothership`).HandleWebBackupDownload(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/{id}/backups/{backupId}/restore/preview',
  (e) => require(`${__hooks}/mothership`).HandleWebBackupRestorePreview(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/{id}/backups/{backupId}/restore',
  (e) => require(`${__hooks}/mothership`).HandleWebBackupRestore(e),
  $apis.requireAuth('users')
)
routerAdd('POST', '/api/web-apps/_worker/backups/claim', (e) =>
  require(`${__hooks}/mothership`).HandleWebBackupClaim(e)
)
routerAdd('POST', '/api/web-apps/_worker/backups/report', (e) =>
  require(`${__hooks}/mothership`).HandleWebBackupReport(e)
)
routerAdd('POST', '/api/web-apps/_worker/backups/pins', (e) => require(`${__hooks}/mothership`).HandleWebBackupPins(e))

routerAdd(
  'POST',
  '/api/web-apps/{id}/backups/{backupId}/recover',
  (e) => require(`${__hooks}/mothership`).HandleWebBackupRecover(e),
  $apis.requireAuth('users')
)

routerAdd(
  'GET',
  '/api/web-apps/{id}/runtime-logs',
  (e) => require(`${__hooks}/mothership`).HandleWebRuntimeLogs(e),
  $apis.requireAuth('users')
)
routerAdd('POST', '/api/web-apps/_observability/config', (e) =>
  require(`${__hooks}/mothership`).HandleWebRuntimeLogConfig(e)
)
routerAdd(
  'GET',
  '/api/web-apps/{id}/notifications',
  (e) => require(`${__hooks}/mothership`).HandleWebNotificationsGet(e),
  $apis.requireAuth('users')
)
routerAdd(
  'PUT',
  '/api/web-apps/{id}/notifications',
  (e) => require(`${__hooks}/mothership`).HandleWebNotificationsPut(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/web-apps/{id}/notifications/history',
  (e) => require(`${__hooks}/mothership`).HandleWebNotificationHistory(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/{id}/notifications/test',
  (e) => require(`${__hooks}/mothership`).HandleWebNotificationTest(e),
  $apis.requireAuth('users')
)
routerAdd('POST', '/api/web-apps/_notifications/claim', (e) =>
  require(`${__hooks}/mothership`).HandleWebNotificationClaim(e)
)
routerAdd('POST', '/api/web-apps/_notifications/report', (e) =>
  require(`${__hooks}/mothership`).HandleWebNotificationReport(e)
)
routerAdd('POST', '/api/web-apps/_notifications/email', (e) =>
  require(`${__hooks}/mothership`).HandleWebNotificationEmail(e)
)
// Canal d'alertes de la plateforme (superAdmins) et chien de garde (worker muet, service de reprise).
routerAdd(
  'GET',
  '/api/web-platform-notifications',
  (e) => require(`${__hooks}/mothership`).HandleWebPlatformNotificationsGet(e),
  $apis.requireAuth('users')
)
routerAdd(
  'PUT',
  '/api/web-platform-notifications',
  (e) => require(`${__hooks}/mothership`).HandleWebPlatformNotificationsPut(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-platform-notifications/test',
  (e) => require(`${__hooks}/mothership`).HandleWebPlatformNotificationTest(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/web-platform-notifications/history',
  (e) => require(`${__hooks}/mothership`).HandleWebPlatformNotificationHistory(e),
  $apis.requireAuth('users')
)
cronAdd('web-alert-watchdog', '* * * * *', () => require(`${__hooks}/mothership`).HandleWebAlertWatchdog())
// Poussée du service de reprise (X-Web-Worker + boucle locale), pas une route de navigateur : pas de requireAuth.
routerAdd('POST', '/api/web-apps/_platform/recovery-events', (e) =>
  require(`${__hooks}/mothership`).HandleWebPlatformRecoveryEvent(e)
)
routerAdd(
  'GET',
  '/api/web-apps/{id}/environments',
  (e) => require(`${__hooks}/mothership`).HandleWebAppEnvironments(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/{id}/project',
  (e) => require(`${__hooks}/mothership`).HandleWebAppProjectCreate(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/{id}/project/attach',
  (e) => require(`${__hooks}/mothership`).HandleWebAppProjectAttach(e),
  $apis.requireAuth('users')
)
routerAdd(
  'DELETE',
  '/api/web-apps/{id}/project',
  (e) => require(`${__hooks}/mothership`).HandleWebAppProjectDetach(e),
  $apis.requireAuth('users')
)
routerAdd(
  'PUT',
  '/api/web-apps/{id}/project',
  (e) => require(`${__hooks}/mothership`).HandleWebAppProjectMove(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/{id}/environments/clone',
  (e) => require(`${__hooks}/mothership`).HandleWebEnvironmentClone(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/{id}/environments/promotion/preview',
  (e) => require(`${__hooks}/mothership`).HandleWebEnvironmentPromotionPreview(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/{id}/environments/promote',
  (e) => require(`${__hooks}/mothership`).HandleWebEnvironmentPromote(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/web-projects',
  (e) => require(`${__hooks}/mothership`).HandleWebProjectsList(e),
  $apis.requireAuth('users')
)
routerAdd(
  'PATCH',
  '/api/web-projects/{projectId}',
  (e) => require(`${__hooks}/mothership`).HandleWebProjectUpdate(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/web-projects/groups',
  (e) => require(`${__hooks}/mothership`).HandleWebProjectGroups(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-projects',
  (e) => require(`${__hooks}/mothership`).HandleWebProjectCreate(e),
  $apis.requireAuth('users')
)
routerAdd(
  'DELETE',
  '/api/web-projects/{projectId}',
  (e) => require(`${__hooks}/mothership`).HandleWebProjectDelete(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/web-projects/{projectId}/variables/{environment}',
  (e) => require(`${__hooks}/mothership`).HandleWebProjectVariablesGet(e),
  $apis.requireAuth('users')
)
routerAdd(
  'PUT',
  '/api/web-projects/{projectId}/variables/{environment}',
  (e) => require(`${__hooks}/mothership`).HandleWebProjectVariablesPut(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-projects/{projectId}/variables/{environment}/reveal',
  (e) => require(`${__hooks}/mothership`).HandleWebProjectVariablesReveal(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-projects/{projectId}/variables/{environment}/preview',
  (e) => require(`${__hooks}/mothership`).HandleWebProjectVariablesPreview(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-projects/{projectId}/previews/{number}/refresh',
  (e) => require(`${__hooks}/mothership`).HandleWebPreviewRefresh(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-projects/{projectId}/previews/{previewId}/close',
  (e) => require(`${__hooks}/mothership`).HandleWebPreviewClose(e),
  $apis.requireAuth('users')
)
cronAdd('web-preview-cleanup', '*/5 * * * *', () => require(`${__hooks}/mothership`).HandleWebPreviewMaintenance())

routerAdd(
  'GET',
  '/api/web-apps/{id}/runtime/profile',
  (e) => require(`${__hooks}/mothership`).HandleWebRuntimeGet(e),
  $apis.requireAuth('users')
)
routerAdd(
  'PATCH',
  '/api/web-apps/{id}/runtime/profile',
  (e) => require(`${__hooks}/mothership`).HandleWebRuntimeUpdate(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/{id}/runtime/profile/preview',
  (e) => require(`${__hooks}/mothership`).HandleWebRuntimePreview(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/web-apps/{id}/storage',
  (e) => require(`${__hooks}/mothership`).HandleWebStorageGet(e),
  $apis.requireAuth('users')
)
routerAdd(
  'PATCH',
  '/api/web-apps/{id}/storage',
  (e) => require(`${__hooks}/mothership`).HandleWebStorageUpdate(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/{id}/storage/{name}/{operation}',
  (e) => require(`${__hooks}/mothership`).HandleWebStorageAction(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/{id}/storage/{name}/restore/preview',
  (e) => require(`${__hooks}/mothership`).HandleWebStorageRestorePreview(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/web-apps/{id}/storage/history',
  (e) => require(`${__hooks}/mothership`).HandleWebStorageHistory(e),
  $apis.requireAuth('users')
)
routerAdd('POST', '/api/web-apps/_worker/storage/claim', (e) =>
  require(`${__hooks}/mothership`).HandleWebStorageClaim(e)
)
routerAdd('POST', '/api/web-apps/_worker/storage/report', (e) =>
  require(`${__hooks}/mothership`).HandleWebStorageReport(e)
)
routerAdd(
  'GET',
  '/api/web-apps/{id}/deploy-tokens',
  (e) => require(`${__hooks}/mothership`).HandleWebCiTokensList(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/{id}/deploy-tokens',
  (e) => require(`${__hooks}/mothership`).HandleWebCiTokensCreate(e),
  $apis.requireAuth('users')
)
routerAdd(
  'DELETE',
  '/api/web-apps/{id}/deploy-tokens/{tokenId}',
  (e) => require(`${__hooks}/mothership`).HandleWebCiTokenRevoke(e),
  $apis.requireAuth('users')
)
routerAdd('POST', '/api/web-apps/ci/deploy', (e) => require(`${__hooks}/mothership`).HandleWebCiDeploy(e))
cronAdd('web-ci-cleanup', '23 * * * *', () => require(`${__hooks}/mothership`).HandleWebCiMaintenance())
cronAdd('web-storage-cleanup', '*/5 * * * *', () => require(`${__hooks}/mothership`).HandleWebStorageMaintenance())

routerAdd(
  'GET',
  '/api/web-platform-recovery',
  (e) => require(`${__hooks}/mothership`).HandleWebPlatformRecoveryStatus(e),
  $apis.requireAuth('users')
)

routerAdd(
  'GET',
  '/api/web-platform-recovery/runs',
  (e) => require(`${__hooks}/mothership`).HandleWebPlatformRecoveryRuns(e),
  $apis.requireAuth('users')
)

routerAdd(
  'PUT',
  '/api/web-platform-recovery/settings',
  (e) => require(`${__hooks}/mothership`).HandleWebPlatformRecoverySettings(e),
  $apis.requireAuth('users')
)

routerAdd(
  'POST',
  '/api/web-platform-recovery/captures',
  (e) => require(`${__hooks}/mothership`).HandleWebPlatformRecoveryCapture(e),
  $apis.requireAuth('users')
)

routerAdd(
  'POST',
  '/api/web-platform-recovery/captures/{backupId}/drill',
  (e) => require(`${__hooks}/mothership`).HandleWebPlatformRecoveryDrill(e),
  $apis.requireAuth('users')
)

routerAdd(
  'GET',
  '/api/web-access/me',
  (e) => require(`${__hooks}/mothership`).HandleWebAccessMe(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/web-projects/{projectId}/members',
  (e) => require(`${__hooks}/mothership`).HandleWebProjectMembersList(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-projects/{projectId}/members',
  (e) => require(`${__hooks}/mothership`).HandleWebProjectMemberAdd(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/web-projects/{projectId}/members/audit',
  (e) => require(`${__hooks}/mothership`).HandleWebProjectMembersAudit(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-projects/{projectId}/members/leave',
  (e) => require(`${__hooks}/mothership`).HandleWebProjectMemberLeave(e),
  $apis.requireAuth('users')
)
routerAdd(
  'PATCH',
  '/api/web-projects/{projectId}/members/{memberId}',
  (e) => require(`${__hooks}/mothership`).HandleWebProjectMemberUpdate(e),
  $apis.requireAuth('users')
)
routerAdd(
  'DELETE',
  '/api/web-projects/{projectId}/members/{memberId}',
  (e) => require(`${__hooks}/mothership`).HandleWebProjectMemberRemove(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/web-access/users/{userId}/projects',
  (e) => require(`${__hooks}/mothership`).HandleWebUserProjectsGet(e),
  $apis.requireAuth('users')
)
routerAdd(
  'PUT',
  '/api/web-access/users/{userId}/projects',
  (e) => require(`${__hooks}/mothership`).HandleWebUserProjectsPut(e),
  $apis.requireAuth('users')
)

// Dépôt Git (clé de déploiement). Le webhook (sans session : HMAC ou jeton vérifié par le handler) est enregistré en
// premier ; une seule route générique `{sourceId}/{action}` (des routes littérales `/{sourceId}/scan` entreraient en
// conflit avec `webhook/{webhookId}` dans le mux Go).
routerAdd(
  'POST',
  '/api/web-git-sources/webhook/{webhookId}',
  (e) => {
    const m = require(`${__hooks}/mothership`)
    return m.HandleWebGitSourceWebhook(e, m.queueWebGitPush)
  },
  $apis.bodyLimit(10000000)
)
routerAdd(
  'POST',
  '/api/web-git-sources',
  (e) => require(`${__hooks}/mothership`).HandleWebGitSourceCreate(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/web-git-sources/{sourceId}',
  (e) => require(`${__hooks}/mothership`).HandleWebGitSourceGet(e),
  $apis.requireAuth('users')
)
routerAdd(
  'DELETE',
  '/api/web-git-sources/{sourceId}',
  (e) => require(`${__hooks}/mothership`).HandleWebGitSourceDelete(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/web-git-sources/{sourceId}/branches',
  (e) => require(`${__hooks}/mothership`).HandleWebGitSourceBranches(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-git-sources/{sourceId}/{action}',
  (e) => require(`${__hooks}/mothership`).HandleWebGitSourceAction(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/instance/{id}/private-access',
  (e) => require(`${__hooks}/mothership`).HandleInstancePrivateAccess(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/instance/{id}/private-access',
  (e) => require(`${__hooks}/mothership`).HandleInstancePrivateAccessPut(e),
  $apis.requireAuth('users')
)
routerAdd(
  'DELETE',
  '/api/instance/{id}/private-access/{bindingId}',
  (e) => require(`${__hooks}/mothership`).HandleInstancePrivateAccessDelete(e),
  $apis.requireAuth('users')
)
routerAdd(
  'GET',
  '/api/web-apps/{id}/private-databases',
  (e) => require(`${__hooks}/mothership`).HandleWebPrivateDatabases(e),
  $apis.requireAuth('users')
)
routerAdd(
  'DELETE',
  '/api/web-apps/{id}/private-databases/{bindingId}',
  (e) => require(`${__hooks}/mothership`).HandleWebPrivateDatabasesDelete(e),
  $apis.requireAuth('users')
)
routerAdd('GET', '/api/web-apps/_private-db/worker', (e) =>
  require(`${__hooks}/mothership`).HandleWebPrivateDatabaseWorker(e)
)
routerAdd('GET', '/api/web-apps/_private-db/broker', (e) =>
  require(`${__hooks}/mothership`).HandleWebPrivateDatabaseBroker(e)
)
routerAdd('POST', '/api/web-apps/_private-db/status', (e) =>
  require(`${__hooks}/mothership`).HandleWebPrivateDatabaseStatus(e)
)

routerAdd(
  'GET',
  '/api/web-apps/cloudflare/settings',
  (e) => require(`${__hooks}/mothership`).HandleWebCloudflareGet(e),
  $apis.requireAuth('users')
)
routerAdd(
  'PUT',
  '/api/web-apps/cloudflare/settings',
  (e) => require(`${__hooks}/mothership`).HandleWebCloudflarePut(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/{id}/domains/{domainId}/cloudflare/preview',
  (e) => require(`${__hooks}/mothership`).HandleWebCloudflarePreview(e),
  $apis.requireAuth('users')
)
routerAdd(
  'POST',
  '/api/web-apps/{id}/domains/{domainId}/cloudflare/apply',
  (e) => require(`${__hooks}/mothership`).HandleWebCloudflareApply(e),
  $apis.requireAuth('users')
)
