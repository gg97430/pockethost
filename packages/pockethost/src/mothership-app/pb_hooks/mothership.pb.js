
//#region src/lib/handlers/adminPlugins/hooks.ts
$app.onServe().bindFunc((e) => {
	e.uiExtensions.push({
		name: "live",
		fs: $os.dirFS(`${__hooks}/../pb_admin_ext/live`)
	});
	e.next();
});
onBootstrap((e) => {
	e.next();
	require(`${__hooks}/mothership`).initLiveViewStatsAtBoot();
});
routerAdd("POST", "/api/admin/live/platform/refresh", (e) => {
	return require(`${__hooks}/mothership`).HandleLivePlatformRefresh(e);
}, $apis.requireSuperuserAuth());
cronAdd("live-view-stats", "* * * * *", () => {
	const mothership = require(`${__hooks}/mothership`);
	mothership.handleLiveViewStatsCron();
	mothership.handleLivePlatformStatsCron();
});
onRealtimeSubscribeRequest((e) => {
	const mothership = require(`${__hooks}/mothership`);
	const platformTopic = mothership.LIVE_PLATFORM_TOPIC;
	const viewStatsTopic = mothership.LIVE_VIEW_STATS_TOPIC;
	for (const sub of e.subscriptions) if ((sub === platformTopic || sub === viewStatsTopic) && !e.hasSuperuserAuth()) throw new ForbiddenError("Superuser required for live platform stats");
	e.next();
	for (const sub of e.subscriptions) {
		if (sub === platformTopic) mothership.sendLivePlatformStatsToClient(e.client);
		if (sub === viewStatsTopic) mothership.sendLiveViewStatsToClient(e.client);
	}
});
onRecordAfterCreateSuccess((e) => {
	e.next();
	require(`${__hooks}/mothership`).handleLivePlatformInstanceCreate(e);
}, "instances");
onRecordAfterUpdateSuccess((e) => {
	e.next();
	require(`${__hooks}/mothership`).handleLivePlatformInstanceUpdate(e);
}, "instances");
onRecordAfterDeleteSuccess((e) => {
	e.next();
	require(`${__hooks}/mothership`).handleLivePlatformInstanceDelete(e);
}, "instances");
onRecordAfterCreateSuccess((e) => {
	e.next();
	require(`${__hooks}/mothership`).handleLivePlatformUserCreate(e);
}, "users");
onRecordAfterUpdateSuccess((e) => {
	e.next();
	require(`${__hooks}/mothership`).handleLivePlatformUserUpdate(e);
}, "users");
onRecordAfterDeleteSuccess((e) => {
	e.next();
	require(`${__hooks}/mothership`).handleLivePlatformUserDelete(e);
}, "users");

//#endregion
//#region src/lib/handlers/edge/hooks.ts
routerAdd("POST", "/api/edge/heartbeat", (e) => {
	return require(`${__hooks}/mothership`).HandleEdgeHeartbeat(e);
}, $apis.requireSuperuserAuth());
cronAdd("edges-stale", "* * * * *", () => {
	require(`${__hooks}/mothership`).markStaleEdges();
});

//#endregion
//#region src/lib/handlers/instance/hooks.ts
routerAdd("GET", "/api/instance/{id}/litestream/status", (e) => require(`${__hooks}/mothership`).HandleInstanceLitestreamTelemetry(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/instance/{id}/litestream/verify", (e) => require(`${__hooks}/mothership`).HandleInstanceLitestreamVerify(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/instance/{id}/diagnostics", (e) => require(`${__hooks}/mothership`).HandleInstanceDiagnostics(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/instance/{id}/performance", (e) => require(`${__hooks}/mothership`).HandleInstancePerformance(e), $apis.requireAuth("users"));
const DEFAULT_BACKUP_IMPORT_LIMIT_BYTES = 12 * 1024 * 1024 * 1024;
routerAdd("GET", "/api/instance/{id}/console", (e) => require(`${__hooks}/mothership`).HandleInstanceConsoleInfo(e));
routerAdd("POST", "/api/instance/{id}/console", (e) => require(`${__hooks}/mothership`).HandleInstanceConsoleTicket(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/instance/{id}/console/consume", (e) => require(`${__hooks}/mothership`).HandleInstanceConsoleConsume(e));
cronAdd("console_ticket_cleanup", "*/5 * * * *", () => require(`${__hooks}/mothership`).PruneInstanceConsoleTickets());
const DEFAULT_BACKUP_IMPORT_CHUNK_LIMIT_BYTES = 64 * 1024 * 1024;
const backupImportBodyLimitBytes = () => {
	const raw = $os.getenv("INSTANCE_BACKUP_UPLOAD_LIMIT_BYTES") || "";
	if (!raw) return DEFAULT_BACKUP_IMPORT_LIMIT_BYTES;
	const value = Number(raw);
	if (!Number.isFinite(value) || value < 0) return DEFAULT_BACKUP_IMPORT_LIMIT_BYTES;
	return Math.floor(value);
};
const backupImportChunkLimitBytes = () => {
	const raw = $os.getenv("INSTANCE_BACKUP_CHUNK_LIMIT_BYTES") || "";
	if (!raw) return DEFAULT_BACKUP_IMPORT_CHUNK_LIMIT_BYTES;
	const value = Number(raw);
	if (!Number.isFinite(value) || value <= 0) return DEFAULT_BACKUP_IMPORT_CHUNK_LIMIT_BYTES;
	return Math.floor(value);
};
routerAdd("PUT", "/api/instance/{id}", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceUpdate(e);
}, $apis.requireAuth());
routerAdd("POST", "/api/instance", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceCreate(e);
}, $apis.requireAuth());
routerAdd("GET", "/api/instance/{id}/overview", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceOverview(e);
}, $apis.requireAuth());
routerAdd("GET", "/api/instance/{id}/metrics", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceMetrics(e);
}, $apis.requireAuth());
routerAdd("GET", "/api/instance/{id}/metrics/history", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceMetricsHistory(e);
}, $apis.requireAuth());
routerAdd("GET", "/api/instances/metrics", (e) => {
	return require(`${__hooks}/mothership`).HandleInstancesMetrics(e);
}, $apis.requireAuth());
routerAdd("GET", "/api/instance/{id}/monitoring", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceMonitoringGet(e);
}, $apis.requireAuth());
routerAdd("PUT", "/api/instance/{id}/monitoring", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceMonitoringUpdate(e);
}, $apis.requireAuth());
routerAdd("GET", "/api/instance/{id}/monitoring/history", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceMonitoringHistory(e);
}, $apis.requireAuth());
routerAdd("GET", "/api/instance/{id}/monitoring/incidents", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceMonitoringIncidents(e);
}, $apis.requireAuth());
routerAdd("POST", "/api/instance/{id}/monitoring/test", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceMonitoringTest(e);
}, $apis.requireAuth());
routerAdd("POST", "/api/instance/{id}/duplicate", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceDuplicate(e);
}, $apis.requireAuth());
routerAdd("POST", "/api/instance/{id}/transfers", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceTransferCreate(e);
}, $apis.requireAuth());
routerAdd("GET", "/api/instance-transfers", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceTransfersList(e);
}, $apis.requireAuth());
routerAdd("POST", "/api/instance-transfers/inspect", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceTransferInspect(e);
}, $apis.requireAuth());
routerAdd("POST", "/api/instance-transfers/import", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceTransferImport(e);
}, $apis.requireAuth());
routerAdd("POST", "/api/instance-transfers/{transferId}/cancel", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceTransferCancel(e);
}, $apis.requireAuth());
routerAdd("POST", "/api/instance-transfers/{transferId}/retry", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceTransferRetry(e);
}, $apis.requireAuth());
routerAdd("POST", "/api/instance-transfers/{transferId}/link", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceTransferRenewLink(e);
}, $apis.requireAuth());
routerAdd("GET", "/api/instance-transfers/{transferId}/metadata", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceTransferMetadata(e);
});
routerAdd("POST", "/api/instance-transfers/{transferId}/claim", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceTransferClaim(e);
});
routerAdd("GET", "/api/instance-transfers/{transferId}/archive", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceTransferArchive(e);
});
routerAdd("POST", "/api/instance-transfers/{transferId}/complete", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceTransferComplete(e);
});
routerAdd("POST", "/api/instance-transfers/{transferId}/progress", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceTransferProgress(e);
});
routerAdd("POST", "/api/instance/{id}/backups", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceBackupCreate(e);
}, $apis.requireAuth());
routerAdd("POST", "/api/instance/{id}/backups/import", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceBackupImport(e);
}, $apis.requireAuth(), $apis.bodyLimit(backupImportBodyLimitBytes()));
routerAdd("POST", "/api/instance/{id}/backups/import/chunked/start", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceBackupChunkedStart(e);
}, $apis.requireAuth());
routerAdd("POST", "/api/instance/{id}/backups/import/chunked/{uploadId}/chunk", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceBackupChunkedUpload(e);
}, $apis.requireAuth(), $apis.bodyLimit(backupImportChunkLimitBytes()));
routerAdd("POST", "/api/instance/{id}/backups/import/chunked/{uploadId}/complete", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceBackupChunkedComplete(e);
}, $apis.requireAuth());
routerAdd("DELETE", "/api/instance/{id}/backups/import/chunked/{uploadId}", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceBackupChunkedCancel(e);
}, $apis.requireAuth());
routerAdd("GET", "/api/instance/{id}/backups", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceBackupsList(e);
}, $apis.requireAuth());
routerAdd("GET", "/api/instance/{id}/backups/policy", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceBackupPolicyGet(e);
}, $apis.requireAuth());
routerAdd("PUT", "/api/instance/{id}/backups/policy", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceBackupPolicyUpdate(e);
}, $apis.requireAuth());
routerAdd("POST", "/api/instance/{id}/backups/policy/run", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceBackupPolicyRun(e);
}, $apis.requireAuth());
routerAdd("GET", "/api/instance/{id}/backups/litestream", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceLitestreamPolicyGet(e);
}, $apis.requireAuth());
routerAdd("PUT", "/api/instance/{id}/backups/litestream", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceLitestreamPolicyUpdate(e);
}, $apis.requireAuth());
routerAdd("GET", "/api/instance/{id}/backups/{backupId}/download", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceBackupDownload(e);
}, $apis.requireAuth());
routerAdd("DELETE", "/api/instance/{id}/backups/{backupId}", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceBackupDelete(e);
}, $apis.requireAuth());
routerAdd("POST", "/api/instance/{id}/backups/{backupId}/restore", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceBackupRestore(e);
}, $apis.requireAuth());
routerAdd("POST", "/api/instance/{id}/backups/{backupId}/restore-new", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceBackupRestoreNew(e);
}, $apis.requireAuth());
routerAdd("DELETE", "/api/instance/{id}", (e) => {
	return require(`${__hooks}/mothership`).HandleInstanceDelete(e);
}, $apis.requireAuth());
routerAdd("POST", "/api/instances/runtime/reset", (e) => {
	return require(`${__hooks}/mothership`).HandleInstancesRuntimeReset(e);
}, $apis.requireSuperuserAuth());
/** Default autoVacuum to true (PocketBase bool zero-default is false) */
onRecordCreate((e) => {
	require(`${__hooks}/mothership`).BeforeCreate_autoVacuum(e);
	e.next();
}, "instances");
/** Protect instances while a transfer is running and clean its private workspace after deletion. */
onRecordDelete((e) => {
	require(`${__hooks}/mothership`).BeforeDelete_instanceTransfer(e);
	e.next();
}, "instances");
onRecordAfterDeleteSuccess((e) => {
	require(`${__hooks}/mothership`).AfterDelete_instanceTransfer(e);
	e.next();
}, "instance_transfers");
/** Validate instance version */
onRecordUpdate((e) => {
	require(`${__hooks}/mothership`).BeforeUpdate_instanceTransferPower(e);
	require(`${__hooks}/mothership`).BeforeUpdate_version(e);
	e.next();
}, "instances");
/** Validate cname */
onRecordUpdate((e) => {
	require(`${__hooks}/mothership`).BeforeUpdate_cname(e);
	e.next();
}, "instances");
/** Create or resolve backup monitoring incidents after terminal backup updates. */
onRecordAfterUpdateSuccess((e) => {
	e.next();
	require(`${__hooks}/mothership`).HandleInstanceBackupMonitoringUpdate(e);
}, "instance_backups");
/** Notify discord on instance create */
onBootstrap((e) => {
	e.next();
});
/** Reset instance status to idle on start */
onBootstrap((e) => {
	e.next();
	return require(`${__hooks}/mothership`).HandleInstancesResetIdle(e);
});
/** Register automatic instance backup policies */
onBootstrap((e) => {
	e.next();
	return require(`${__hooks}/mothership`).HandleInstanceBackupPoliciesBootstrap(e);
});
cronAdd("instance-backup-policy-dispatcher", "* * * * *", () => {
	require(`${__hooks}/mothership`).HandleInstanceBackupPolicyCronDispatcher();
});
onBootstrap((e) => {
	e.next();
	require(`${__hooks}/mothership`).HandleInstanceTransferBootstrap();
});
cronAdd("instance-transfer-dispatcher", "* * * * *", () => {
	require(`${__hooks}/mothership`).HandleInstanceTransferDispatcher();
});
/** Persist metrics and evaluate opt-in monitoring with a shared Docker snapshot. */
cronAdd("instance-resource-metrics-sampler", "* * * * *", () => {
	require(`${__hooks}/mothership`).CollectInstanceMetricsAndMonitoring();
});
/** Keep the metrics collection bounded to seven days. */
cronAdd("instance-resource-metrics-retention", "17 * * * *", () => {
	require(`${__hooks}/mothership`).PurgeExpiredInstanceResourceMetrics();
});
/** Keep detailed uptime checks for exactly thirty days. */
cronAdd("instance-health-checks-retention", "29 * * * *", () => {
	require(`${__hooks}/mothership`).PurgeExpiredInstanceHealthChecks();
});
/** Reconcile optional Litestream replication */
onBootstrap((e) => {
	e.next();
	return require(`${__hooks}/mothership`).HandleInstanceLitestreamBootstrap(e);
});
routerUse((e) => require(`${__hooks}/mothership`).AuditInstanceRequest(e));
routerAdd("GET", "/api/instance/{id}/actions", (e) => require(`${__hooks}/mothership`).HandleInstanceActionLog(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/instance/{id}/backup-checks", (e) => require(`${__hooks}/mothership`).HandleInstanceBackupChecksGet(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/instance/{id}/backup-checks", (e) => require(`${__hooks}/mothership`).HandleInstanceBackupCheckCreate(e), $apis.requireAuth("users"));
routerAdd("PUT", "/api/instance/{id}/backup-checks/policy", (e) => require(`${__hooks}/mothership`).HandleInstanceBackupCheckPolicy(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/instance/{id}/service-accounts", (e) => require(`${__hooks}/mothership`).HandleInstanceServiceAccounts(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/instance/{id}/service-accounts", (e) => require(`${__hooks}/mothership`).HandleInstanceServiceAccounts(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/instance/{id}/operations/consume", (e) => require(`${__hooks}/mothership`).HandleInstanceOperationConsume(e));
onBootstrap((e) => {
	e.next();
	require(`${__hooks}/mothership`).BackupCheckBootstrap();
});
cronAdd("instance-backup-integrity-checks", "*/1 * * * *", () => require(`${__hooks}/mothership`).BackupCheckDispatcher());
routerAdd("GET", "/api/actions", (e) => require(`${__hooks}/mothership`).HandleAccountActionLog(e), $apis.requireAuth("users"));
onRecordCreateRequest((e) => require(`${__hooks}/mothership`).GuardInstanceHostnamesRequest(e), "instances");
onRecordUpdateRequest((e) => require(`${__hooks}/mothership`).GuardInstanceHostnamesRequest(e), "instances");
onRecordCreate((e) => require(`${__hooks}/mothership`).ValidateInstanceHostnames(e), "instances");
onRecordUpdate((e) => require(`${__hooks}/mothership`).ValidateInstanceHostnames(e), "instances");

//#endregion
//#region src/lib/handlers/lemon/hooks.ts
routerAdd("POST", "/api/ls", (e) => {
	return require(`${__hooks}/mothership`).HandleLemonSqueezySale(e);
});

//#endregion
//#region src/lib/handlers/mail/hooks.ts
routerAdd("POST", "/api/mail", (e) => {
	return require(`${__hooks}/mothership`).HandleMailSend(e);
}, $apis.requireSuperuserAuth());

//#endregion
//#region src/lib/handlers/meta/hooks.ts
onBootstrap((e) => {
	e.next();
	require(`${__hooks}/mothership`).HandleMetaUpdateAtBoot(e);
});

//#endregion
//#region src/lib/handlers/mirror/hooks.ts
routerAdd("GET", "/api/mirror", (e) => {
	return require(`${__hooks}/mothership`).HandleMirrorData(e);
}, $apis.gzip(), $apis.requireSuperuserAuth());
routerAdd("POST", "/api/mirror", (e) => {
	return require(`${__hooks}/mothership`).HandleMirrorSync(e);
}, $apis.gzip(), $apis.requireSuperuserAuth());

//#endregion
//#region src/lib/handlers/notify/hooks.ts
routerAdd(`GET`, `api/process_single_notification`, (e) => {
	return require(`${__hooks}/mothership`).HandleProcessSingleNotification(e);
});
onRecordAfterCreateSuccess((e) => {
	e.next();
	require(`${__hooks}/mothership`).HandleProcessNotification(e);
}, `notifications`);
onRecordUpdate((e) => {
	e.next();
	require(`${__hooks}/mothership`).HandleUserWelcomeMessage(e);
}, "users");

//#endregion
//#region src/lib/handlers/operatorAdmin/hooks.ts
routerAdd("GET", "/api/admin/fleet", (e) => require(`${__hooks}/mothership`).HandleOperatorFleet(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/admin/controls", (e) => require(`${__hooks}/mothership`).HandleOperatorControls(e), $apis.requireAuth("users"));
routerAdd("PUT", "/api/admin/controls/memory", (e) => require(`${__hooks}/mothership`).HandleOperatorMemory(e), $apis.requireAuth("users"));
routerAdd("PUT", "/api/admin/controls/maintenance", (e) => require(`${__hooks}/mothership`).HandleOperatorDeploymentMaintenance(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/admin/monitoring/resources", (e) => require(`${__hooks}/mothership`).HandleOperatorResourceMetrics(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/admin/_monitoring/config", (e) => require(`${__hooks}/mothership`).HandleOperatorMonitoringDeliveryConfig(e), $apis.requireAuth("_superusers"));
routerAdd("PATCH", "/api/admin/monitoring", (e) => require(`${__hooks}/mothership`).HandleOperatorMonitoringUpdate(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/admin/monitoring", (e) => require(`${__hooks}/mothership`).HandleOperatorMonitoring(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/admin/overview", (e) => {
	return require(`${__hooks}/mothership`).HandleOperatorAdminOverview(e);
}, $apis.requireAuth());
routerAdd("POST", "/api/admin/users", (e) => {
	return require(`${__hooks}/mothership`).HandleOperatorAdminCreateUser(e);
}, $apis.requireAuth());
routerAdd("PATCH", "/api/admin/users/{id}", (e) => {
	return require(`${__hooks}/mothership`).HandleOperatorAdminUpdateUser(e);
}, $apis.requireAuth());
routerAdd("PUT", "/api/admin/settings", (e) => {
	return require(`${__hooks}/mothership`).HandleOperatorAdminUpdateSettings(e);
}, $apis.requireAuth());
routerAdd("POST", "/api/admin/settings/backup-s3/test", (e) => {
	return require(`${__hooks}/mothership`).HandleOperatorAdminTestBackupS3(e);
}, $apis.requireAuth());
routerAdd("POST", "/api/admin/settings/smtp/test", (e) => {
	return require(`${__hooks}/mothership`).HandleOperatorAdminTestSMTP(e);
}, $apis.requireAuth());
routerAdd("GET", "/api/admin/disk-cleanup", (e) => {
	return require(`${__hooks}/mothership`).HandleOperatorAdminDiskCleanupPreview(e);
}, $apis.requireAuth());
routerAdd("POST", "/api/admin/disk-cleanup", (e) => {
	return require(`${__hooks}/mothership`).HandleOperatorAdminDiskCleanupRun(e);
}, $apis.requireAuth());
routerAdd("GET", "/api/workspace/overview", (e) => require(`${__hooks}/mothership`).HandleWorkspaceOverview(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/admin/request-limits", (e) => require(`${__hooks}/mothership`).HandleOperatorRequestLimits(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/admin/request-limits/web", (e) => require(`${__hooks}/mothership`).HandleOperatorWebRequestLimit(e), $apis.requireAuth("users"));
routerAdd("PUT", "/api/admin/request-limits/web", (e) => require(`${__hooks}/mothership`).HandleOperatorWebRequestLimit(e), $apis.requireAuth("users"));
routerAdd("PUT", "/api/admin/request-limits", (e) => require(`${__hooks}/mothership`).HandleOperatorRequestLimitsPut(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/admin/request-limits/databases/{id}", (e) => require(`${__hooks}/mothership`).HandleOperatorDatabaseRequestLimits(e), $apis.requireAuth("users"));
routerAdd("PUT", "/api/admin/request-limits/databases/{id}", (e) => require(`${__hooks}/mothership`).HandleOperatorDatabaseRequestLimits(e), $apis.requireAuth("users"));

//#endregion
//#region src/lib/handlers/outpost/hooks.ts
routerAdd("GET", "/api/unsubscribe", (e) => {
	return require(`${__hooks}/mothership`).HandleOutpostUnsubscribe(e);
});

//#endregion
//#region src/lib/handlers/signup/hooks.ts
routerAdd("GET", "/api/signup", (e) => {
	return require(`${__hooks}/mothership`).HandleSignupCheck(e);
});
routerAdd("POST", "/api/signup", (e) => {
	return require(`${__hooks}/mothership`).HandleSignupConfirm(e);
});

//#endregion
//#region src/lib/handlers/sns/hooks.ts
routerAdd("POST", "/api/sns", (e) => {
	return require(`${__hooks}/mothership`).HandleSesError(e);
});

//#endregion
//#region src/lib/handlers/sshKeys/hooks.ts
onRecordCreateRequest((e) => {
	require(`${__hooks}/mothership`).BeforeCreate_ssh_keys(e);
	e.next();
}, "ssh_keys");
onRecordUpdateRequest((e) => {
	require(`${__hooks}/mothership`).BeforeUpdate_ssh_keys(e);
	e.next();
}, "ssh_keys");

//#endregion
//#region src/lib/handlers/stats/hooks.ts
routerAdd("GET", "/stats.json", (e) => {
	return require(`${__hooks}/mothership`).HandleStatsRequest(e);
});
onBootstrap((e) => {
	e.next();
	require(`${__hooks}/mothership`).HandleStatsRefreshAtBoot(e);
});
cronAdd("public-stats", "0 * * * *", () => {
	require(`${__hooks}/mothership`).refreshPublicStats();
});

//#endregion
//#region src/lib/handlers/user/hooks.ts
routerAdd("POST", "/api/web-apps/_platform/delegate", (e) => require(`${__hooks}/mothership`).HandleDashboardServiceDelegate(e), $apis.requireAuth("_superusers"));
onBootstrap((e) => {
	e.next();
	require(`${__hooks}/mothership`).InitializeDashboardTotp();
});
routerUse((e) => require(`${__hooks}/mothership`).GuardDashboardTotp(e));
onRecordAuthRequest((e) => require(`${__hooks}/mothership`).HandleDashboardTotpAuth(e), "users");
routerAdd("GET", "/api/account/totp", (e) => require(`${__hooks}/mothership`).HandleDashboardTotpStatus(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/account/totp/setup", (e) => require(`${__hooks}/mothership`).HandleDashboardTotpSetup(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/account/totp/confirm", (e) => require(`${__hooks}/mothership`).HandleDashboardTotpConfirm(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/account/totp/disable", (e) => require(`${__hooks}/mothership`).HandleDashboardTotpDisable(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/account/totp/recovery", (e) => require(`${__hooks}/mothership`).HandleDashboardTotpRegenerate(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/userToken/{id}", (e) => {
	return require(`${__hooks}/mothership`).HandleUserTokenRequest(e);
}, $apis.requireSuperuserAuth());

//#endregion
//#region src/lib/handlers/versions/hooks.ts
/** Return a list of available PocketBase versions */
routerAdd("GET", "/api/versions", (e) => {
	return require(`${__hooks}/mothership`).HandleVersionsRequest(e);
});

//#endregion
//#region src/lib/handlers/webApps/hooks.ts
routerAdd("GET", "/api/web-apps", (e) => require(`${__hooks}/mothership`).HandleWebAppsList(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps", (e) => require(`${__hooks}/mothership`).HandleWebAppCreate(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-apps/settings", (e) => require(`${__hooks}/mothership`).HandleWebSettingsGet(e), $apis.requireAuth("users"));
routerAdd("PUT", "/api/web-apps/settings", (e) => require(`${__hooks}/mothership`).HandleWebSettingsPut(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-apps/repositories", (e) => require(`${__hooks}/mothership`).HandleWebRepositories(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-apps/repositories/{repositoryId}/branches", (e) => require(`${__hooks}/mothership`).HandleWebBranches(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/github/webhook", (e) => require(`${__hooks}/mothership`).HandleWebGithubWebhook(e), $apis.bodyLimit(1e7));
routerAdd("POST", "/api/web-apps/github/webhook/{connectionId}", (e) => require(`${__hooks}/mothership`).HandleWebGithubConnectionWebhook(e), $apis.bodyLimit(1e7));
routerAdd("GET", "/api/web-apps/github/connections", (e) => require(`${__hooks}/mothership`).HandleWebGithubConnectionsList(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/github/connections", (e) => require(`${__hooks}/mothership`).HandleWebGithubConnectionCreate(e), $apis.requireAuth("users"));
routerAdd("PATCH", "/api/web-apps/github/connections/{connectionId}", (e) => require(`${__hooks}/mothership`).HandleWebGithubConnectionUpdate(e), $apis.requireAuth("users"));
routerAdd("DELETE", "/api/web-apps/github/connections/{connectionId}", (e) => require(`${__hooks}/mothership`).HandleWebGithubConnectionDelete(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/_worker/claim", (e) => require(`${__hooks}/mothership`).HandleWebWorkerClaim(e));
routerAdd("POST", "/api/web-apps/_worker/project-limits", (e) => require(`${__hooks}/mothership`).HandleWebWorkerProjectLimits(e));
routerAdd("POST", "/api/web-apps/_worker/report", (e) => require(`${__hooks}/mothership`).HandleWebWorkerReport(e));
routerAdd("POST", "/api/web-apps/_worker/deletions/claim", (e) => require(`${__hooks}/mothership`).HandleWebDeletionClaim(e));
routerAdd("POST", "/api/web-apps/_worker/deletions/report", (e) => require(`${__hooks}/mothership`).HandleWebDeletionReport(e));
routerAdd("DELETE", "/api/web-apps/{id}", (e) => require(`${__hooks}/mothership`).HandleWebAppDelete(e), $apis.requireAuth("users"));
onRecordCreate((e) => require(`${__hooks}/mothership`).GuardWebDeletingRecord(e));
onRecordUpdate((e) => require(`${__hooks}/mothership`).GuardWebDeletingRecord(e));
routerAdd("GET", "/api/web-apps/{id}", (e) => require(`${__hooks}/mothership`).HandleWebAppGet(e), $apis.requireAuth("users"));
routerAdd("PATCH", "/api/web-apps/{id}", (e) => require(`${__hooks}/mothership`).HandleWebAppUpdate(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-apps/{id}/deployments", (e) => require(`${__hooks}/mothership`).HandleWebDeployments(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-apps/{id}/deployments/{deploymentId}/logs", (e) => require(`${__hooks}/mothership`).HandleWebDeploymentLogs(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/{id}/{action}", (e) => require(`${__hooks}/mothership`).HandleWebAppAction(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-apps/{id}/domains", (e) => require(`${__hooks}/mothership`).HandleWebDomainsList(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/{id}/domains", (e) => require(`${__hooks}/mothership`).HandleWebDomainCreate(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/{id}/domains/{domainId}/verify", (e) => require(`${__hooks}/mothership`).HandleWebDomainVerify(e), $apis.requireAuth("users"));
routerAdd("DELETE", "/api/web-apps/{id}/domains/{domainId}", (e) => require(`${__hooks}/mothership`).HandleWebDomainDelete(e), $apis.requireAuth("users"));
cronAdd("web-app-maintenance", "17 */6 * * *", () => require(`${__hooks}/mothership`).HandleWebMaintenance());
cronAdd("web-cloudflare-validation", "* * * * *", () => require(`${__hooks}/mothership`).HandleWebCloudflareMaintenance());
onBootstrap((e) => {
	e.next();
	require(`${__hooks}/mothership`).normalizeWebDeliveryConnections();
});
onRecordCreateRequest((e) => require(`${__hooks}/mothership`).GuardWebInstanceDomainCreate(e), "instances");
onRecordUpdateRequest((e) => require(`${__hooks}/mothership`).GuardWebInstanceDomainUpdate(e), "instances");
routerAdd("POST", "/api/web-apps/github/connect/start", (e) => require(`${__hooks}/mothership`).HandleWebGithubConnectStart(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/github/connect/manifest", (e) => require(`${__hooks}/mothership`).HandleWebGithubConnectManifest(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/github/connect/install/start", (e) => require(`${__hooks}/mothership`).HandleWebGithubConnectInstallStart(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/github/connect/install", (e) => require(`${__hooks}/mothership`).HandleWebGithubConnectInstall(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-apps/github/connect/status", (e) => require(`${__hooks}/mothership`).HandleWebGithubConnectStatus(e), $apis.requireAuth("users"));
cronAdd("web-github-connect-cleanup", "43 * * * *", () => require(`${__hooks}/mothership`).HandleWebGithubConnectMaintenance());
routerAdd("POST", "/api/web-apps/{id}/deployments/{deploymentId}/cancel", (e) => require(`${__hooks}/mothership`).HandleWebDeploymentCancel(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/{id}/deployments/{deploymentId}/retry", (e) => require(`${__hooks}/mothership`).HandleWebDeploymentRetry(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-apps/{id}/runtime", (e) => require(`${__hooks}/mothership`).HandleWebRuntime(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-apps/{id}/webhooks", (e) => require(`${__hooks}/mothership`).HandleWebWebhookDeliveries(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/github/connect/install/check", (e) => require(`${__hooks}/mothership`).HandleWebGithubConnectInstallCheck(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-apps/github/health", (e) => require(`${__hooks}/mothership`).HandleWebGithubHealth(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-apps/{id}/environment", (e) => require(`${__hooks}/mothership`).HandleWebEnvironmentGet(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/{id}/environment/reveal", (e) => require(`${__hooks}/mothership`).HandleWebEnvironmentReveal(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/{id}/environment/preview", (e) => require(`${__hooks}/mothership`).HandleWebEnvironmentPreview(e), $apis.requireAuth("users"));
routerAdd("PUT", "/api/web-apps/{id}/environment", (e) => require(`${__hooks}/mothership`).HandleWebEnvironmentPut(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/{id}/terminal/session", (e) => require(`${__hooks}/mothership`).HandleWebTerminalSession(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-apps/{id}/terminal/sessions", (e) => require(`${__hooks}/mothership`).HandleWebTerminalHistory(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/{id}/terminal/sessions/{sessionId}/close", (e) => require(`${__hooks}/mothership`).HandleWebTerminalRevoke(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/_terminal/claim", (e) => require(`${__hooks}/mothership`).HandleWebTerminalClaim(e));
routerAdd("POST", "/api/web-apps/_terminal/validate", (e) => require(`${__hooks}/mothership`).HandleWebTerminalValidate(e));
routerAdd("POST", "/api/web-apps/_terminal/close", (e) => require(`${__hooks}/mothership`).HandleWebTerminalClose(e));
cronAdd("web-terminal-cleanup", "*/5 * * * *", () => require(`${__hooks}/mothership`).HandleWebTerminalMaintenance());
routerAdd("POST", "/api/web-apps/{id}/apply-runtime", (e) => require(`${__hooks}/mothership`).HandleWebApplyRuntime(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-apps/{id}/health", (e) => require(`${__hooks}/mothership`).HandleWebHealthGet(e), $apis.requireAuth("users"));
routerAdd("PATCH", "/api/web-apps/{id}/health", (e) => require(`${__hooks}/mothership`).HandleWebHealthUpdate(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-apps/{id}/health/history", (e) => require(`${__hooks}/mothership`).HandleWebHealthHistory(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/_health/config", (e) => require(`${__hooks}/mothership`).HandleWebHealthConfig(e));
routerAdd("POST", "/api/web-apps/_health/report", (e) => require(`${__hooks}/mothership`).HandleWebHealthReport(e));
routerAdd("GET", "/api/web-apps/{id}/tasks", (e) => require(`${__hooks}/mothership`).HandleWebTasksList(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/{id}/tasks", (e) => require(`${__hooks}/mothership`).HandleWebTasksCreate(e), $apis.requireAuth("users"));
routerAdd("PATCH", "/api/web-apps/{id}/tasks/{taskId}", (e) => require(`${__hooks}/mothership`).HandleWebTaskUpdate(e), $apis.requireAuth("users"));
routerAdd("DELETE", "/api/web-apps/{id}/tasks/{taskId}", (e) => require(`${__hooks}/mothership`).HandleWebTaskDelete(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/{id}/tasks/{taskId}/run", (e) => require(`${__hooks}/mothership`).HandleWebTaskRun(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-apps/{id}/task-runs", (e) => require(`${__hooks}/mothership`).HandleWebTaskRuns(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-apps/{id}/checks", (e) => require(`${__hooks}/mothership`).HandleWebChecksGet(e), $apis.requireAuth("users"));
routerAdd("PUT", "/api/web-apps/{id}/checks", (e) => require(`${__hooks}/mothership`).HandleWebChecksPut(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/{id}/checks/run", (e) => require(`${__hooks}/mothership`).HandleWebChecksRun(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/_checks/claim", (e) => require(`${__hooks}/mothership`).HandleWebChecksClaim(e));
routerAdd("POST", "/api/web-apps/_checks/report", (e) => require(`${__hooks}/mothership`).HandleWebChecksReport(e));
routerAdd("POST", "/api/web-apps/_tasks/claim", (e) => require(`${__hooks}/mothership`).HandleWebTaskClaim(e));
routerAdd("POST", "/api/web-apps/_tasks/validate", (e) => require(`${__hooks}/mothership`).HandleWebTaskValidate(e));
routerAdd("POST", "/api/web-apps/_tasks/report", (e) => require(`${__hooks}/mothership`).HandleWebTaskReport(e));
cronAdd("web-health-cleanup", "17 * * * *", () => require(`${__hooks}/mothership`).HandleWebHealthMaintenance());
routerAdd("GET", "/api/web-apps/{id}/backups", (e) => require(`${__hooks}/mothership`).HandleWebBackupsList(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/{id}/backups", (e) => require(`${__hooks}/mothership`).HandleWebBackupCreate(e), $apis.requireAuth("users"));
routerAdd("PUT", "/api/web-apps/{id}/backups/settings", (e) => require(`${__hooks}/mothership`).HandleWebBackupSettings(e), $apis.requireAuth("users"));
routerAdd("DELETE", "/api/web-apps/{id}/backups/{backupId}", (e) => require(`${__hooks}/mothership`).HandleWebBackupDelete(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-apps/{id}/backups/{backupId}/download", (e) => require(`${__hooks}/mothership`).HandleWebBackupDownload(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/{id}/backups/{backupId}/restore/preview", (e) => require(`${__hooks}/mothership`).HandleWebBackupRestorePreview(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/{id}/backups/{backupId}/restore", (e) => require(`${__hooks}/mothership`).HandleWebBackupRestore(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/_worker/backups/claim", (e) => require(`${__hooks}/mothership`).HandleWebBackupClaim(e));
routerAdd("POST", "/api/web-apps/_worker/backups/report", (e) => require(`${__hooks}/mothership`).HandleWebBackupReport(e));
routerAdd("POST", "/api/web-apps/_worker/backups/pins", (e) => require(`${__hooks}/mothership`).HandleWebBackupPins(e));
routerAdd("POST", "/api/web-apps/{id}/backups/{backupId}/recover", (e) => require(`${__hooks}/mothership`).HandleWebBackupRecover(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-apps/{id}/runtime-logs", (e) => require(`${__hooks}/mothership`).HandleWebRuntimeLogs(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/_observability/config", (e) => require(`${__hooks}/mothership`).HandleWebRuntimeLogConfig(e));
routerAdd("GET", "/api/web-apps/{id}/notifications", (e) => require(`${__hooks}/mothership`).HandleWebNotificationsGet(e), $apis.requireAuth("users"));
routerAdd("PUT", "/api/web-apps/{id}/notifications", (e) => require(`${__hooks}/mothership`).HandleWebNotificationsPut(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-apps/{id}/notifications/history", (e) => require(`${__hooks}/mothership`).HandleWebNotificationHistory(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/{id}/notifications/test", (e) => require(`${__hooks}/mothership`).HandleWebNotificationTest(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/_notifications/claim", (e) => require(`${__hooks}/mothership`).HandleWebNotificationClaim(e));
routerAdd("POST", "/api/web-apps/_notifications/report", (e) => require(`${__hooks}/mothership`).HandleWebNotificationReport(e));
routerAdd("POST", "/api/web-apps/_notifications/email", (e) => require(`${__hooks}/mothership`).HandleWebNotificationEmail(e));
routerAdd("GET", "/api/web-platform-notifications", (e) => require(`${__hooks}/mothership`).HandleWebPlatformNotificationsGet(e), $apis.requireAuth("users"));
routerAdd("PUT", "/api/web-platform-notifications", (e) => require(`${__hooks}/mothership`).HandleWebPlatformNotificationsPut(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-platform-notifications/test", (e) => require(`${__hooks}/mothership`).HandleWebPlatformNotificationTest(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-platform-notifications/history", (e) => require(`${__hooks}/mothership`).HandleWebPlatformNotificationHistory(e), $apis.requireAuth("users"));
cronAdd("web-alert-watchdog", "* * * * *", () => require(`${__hooks}/mothership`).HandleWebAlertWatchdog());
routerAdd("POST", "/api/web-apps/_platform/recovery-events", (e) => require(`${__hooks}/mothership`).HandleWebPlatformRecoveryEvent(e));
routerAdd("GET", "/api/web-apps/{id}/environments", (e) => require(`${__hooks}/mothership`).HandleWebAppEnvironments(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/{id}/project", (e) => require(`${__hooks}/mothership`).HandleWebAppProjectCreate(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/{id}/project/attach", (e) => require(`${__hooks}/mothership`).HandleWebAppProjectAttach(e), $apis.requireAuth("users"));
routerAdd("DELETE", "/api/web-apps/{id}/project", (e) => require(`${__hooks}/mothership`).HandleWebAppProjectDetach(e), $apis.requireAuth("users"));
routerAdd("PUT", "/api/web-apps/{id}/project", (e) => require(`${__hooks}/mothership`).HandleWebAppProjectMove(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/{id}/environments/clone", (e) => require(`${__hooks}/mothership`).HandleWebEnvironmentClone(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/{id}/environments/promotion/preview", (e) => require(`${__hooks}/mothership`).HandleWebEnvironmentPromotionPreview(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/{id}/environments/promote", (e) => require(`${__hooks}/mothership`).HandleWebEnvironmentPromote(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-projects", (e) => require(`${__hooks}/mothership`).HandleWebProjectsList(e), $apis.requireAuth("users"));
routerAdd("PATCH", "/api/web-projects/{projectId}", (e) => require(`${__hooks}/mothership`).HandleWebProjectUpdate(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-projects/groups", (e) => require(`${__hooks}/mothership`).HandleWebProjectGroups(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-projects", (e) => require(`${__hooks}/mothership`).HandleWebProjectCreate(e), $apis.requireAuth("users"));
routerAdd("DELETE", "/api/web-projects/{projectId}", (e) => require(`${__hooks}/mothership`).HandleWebProjectDelete(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-projects/{projectId}/variables/{environment}", (e) => require(`${__hooks}/mothership`).HandleWebProjectVariablesGet(e), $apis.requireAuth("users"));
routerAdd("PUT", "/api/web-projects/{projectId}/variables/{environment}", (e) => require(`${__hooks}/mothership`).HandleWebProjectVariablesPut(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-projects/{projectId}/variables/{environment}/reveal", (e) => require(`${__hooks}/mothership`).HandleWebProjectVariablesReveal(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-projects/{projectId}/variables/{environment}/preview", (e) => require(`${__hooks}/mothership`).HandleWebProjectVariablesPreview(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-projects/{projectId}/previews/{number}/refresh", (e) => require(`${__hooks}/mothership`).HandleWebPreviewRefresh(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-projects/{projectId}/previews/{previewId}/close", (e) => require(`${__hooks}/mothership`).HandleWebPreviewClose(e), $apis.requireAuth("users"));
cronAdd("web-preview-cleanup", "*/5 * * * *", () => require(`${__hooks}/mothership`).HandleWebPreviewMaintenance());
routerAdd("GET", "/api/web-apps/{id}/runtime/profile", (e) => require(`${__hooks}/mothership`).HandleWebRuntimeGet(e), $apis.requireAuth("users"));
routerAdd("PATCH", "/api/web-apps/{id}/runtime/profile", (e) => require(`${__hooks}/mothership`).HandleWebRuntimeUpdate(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/{id}/runtime/profile/preview", (e) => require(`${__hooks}/mothership`).HandleWebRuntimePreview(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-apps/{id}/storage", (e) => require(`${__hooks}/mothership`).HandleWebStorageGet(e), $apis.requireAuth("users"));
routerAdd("PATCH", "/api/web-apps/{id}/storage", (e) => require(`${__hooks}/mothership`).HandleWebStorageUpdate(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/{id}/storage/{name}/{operation}", (e) => require(`${__hooks}/mothership`).HandleWebStorageAction(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/{id}/storage/{name}/restore/preview", (e) => require(`${__hooks}/mothership`).HandleWebStorageRestorePreview(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-apps/{id}/storage/history", (e) => require(`${__hooks}/mothership`).HandleWebStorageHistory(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/_worker/storage/claim", (e) => require(`${__hooks}/mothership`).HandleWebStorageClaim(e));
routerAdd("POST", "/api/web-apps/_worker/storage/report", (e) => require(`${__hooks}/mothership`).HandleWebStorageReport(e));
routerAdd("GET", "/api/web-apps/{id}/deploy-tokens", (e) => require(`${__hooks}/mothership`).HandleWebCiTokensList(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/{id}/deploy-tokens", (e) => require(`${__hooks}/mothership`).HandleWebCiTokensCreate(e), $apis.requireAuth("users"));
routerAdd("DELETE", "/api/web-apps/{id}/deploy-tokens/{tokenId}", (e) => require(`${__hooks}/mothership`).HandleWebCiTokenRevoke(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/ci/deploy", (e) => require(`${__hooks}/mothership`).HandleWebCiDeploy(e));
cronAdd("web-ci-cleanup", "23 * * * *", () => require(`${__hooks}/mothership`).HandleWebCiMaintenance());
cronAdd("web-storage-cleanup", "*/5 * * * *", () => require(`${__hooks}/mothership`).HandleWebStorageMaintenance());
routerAdd("GET", "/api/web-platform-recovery", (e) => require(`${__hooks}/mothership`).HandleWebPlatformRecoveryStatus(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-platform-recovery/runs", (e) => require(`${__hooks}/mothership`).HandleWebPlatformRecoveryRuns(e), $apis.requireAuth("users"));
routerAdd("PUT", "/api/web-platform-recovery/settings", (e) => require(`${__hooks}/mothership`).HandleWebPlatformRecoverySettings(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-platform-recovery/captures", (e) => require(`${__hooks}/mothership`).HandleWebPlatformRecoveryCapture(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-platform-recovery/captures/{backupId}/drill", (e) => require(`${__hooks}/mothership`).HandleWebPlatformRecoveryDrill(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-access/me", (e) => require(`${__hooks}/mothership`).HandleWebAccessMe(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-projects/{projectId}/members", (e) => require(`${__hooks}/mothership`).HandleWebProjectMembersList(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-projects/{projectId}/members", (e) => require(`${__hooks}/mothership`).HandleWebProjectMemberAdd(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-projects/{projectId}/members/audit", (e) => require(`${__hooks}/mothership`).HandleWebProjectMembersAudit(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-projects/{projectId}/members/leave", (e) => require(`${__hooks}/mothership`).HandleWebProjectMemberLeave(e), $apis.requireAuth("users"));
routerAdd("PATCH", "/api/web-projects/{projectId}/members/{memberId}", (e) => require(`${__hooks}/mothership`).HandleWebProjectMemberUpdate(e), $apis.requireAuth("users"));
routerAdd("DELETE", "/api/web-projects/{projectId}/members/{memberId}", (e) => require(`${__hooks}/mothership`).HandleWebProjectMemberRemove(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-access/users/{userId}/projects", (e) => require(`${__hooks}/mothership`).HandleWebUserProjectsGet(e), $apis.requireAuth("users"));
routerAdd("PUT", "/api/web-access/users/{userId}/projects", (e) => require(`${__hooks}/mothership`).HandleWebUserProjectsPut(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-git-sources/webhook/{webhookId}", (e) => {
	const m = require(`${__hooks}/mothership`);
	return m.HandleWebGitSourceWebhook(e, m.queueWebGitPush);
}, $apis.bodyLimit(1e7));
routerAdd("POST", "/api/web-git-sources", (e) => require(`${__hooks}/mothership`).HandleWebGitSourceCreate(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-git-sources/{sourceId}", (e) => require(`${__hooks}/mothership`).HandleWebGitSourceGet(e), $apis.requireAuth("users"));
routerAdd("DELETE", "/api/web-git-sources/{sourceId}", (e) => require(`${__hooks}/mothership`).HandleWebGitSourceDelete(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-git-sources/{sourceId}/branches", (e) => require(`${__hooks}/mothership`).HandleWebGitSourceBranches(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-git-sources/{sourceId}/{action}", (e) => require(`${__hooks}/mothership`).HandleWebGitSourceAction(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/instance/{id}/private-access", (e) => require(`${__hooks}/mothership`).HandleInstancePrivateAccess(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/instance/{id}/private-access", (e) => require(`${__hooks}/mothership`).HandleInstancePrivateAccessPut(e), $apis.requireAuth("users"));
routerAdd("DELETE", "/api/instance/{id}/private-access/{bindingId}", (e) => require(`${__hooks}/mothership`).HandleInstancePrivateAccessDelete(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-apps/{id}/private-databases", (e) => require(`${__hooks}/mothership`).HandleWebPrivateDatabases(e), $apis.requireAuth("users"));
routerAdd("DELETE", "/api/web-apps/{id}/private-databases/{bindingId}", (e) => require(`${__hooks}/mothership`).HandleWebPrivateDatabasesDelete(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-apps/_private-db/worker", (e) => require(`${__hooks}/mothership`).HandleWebPrivateDatabaseWorker(e));
routerAdd("GET", "/api/web-apps/_private-db/broker", (e) => require(`${__hooks}/mothership`).HandleWebPrivateDatabaseBroker(e));
routerAdd("POST", "/api/web-apps/_private-db/status", (e) => require(`${__hooks}/mothership`).HandleWebPrivateDatabaseStatus(e));
routerAdd("GET", "/api/web-apps/cloudflare/settings", (e) => require(`${__hooks}/mothership`).HandleWebCloudflareGet(e), $apis.requireAuth("users"));
routerAdd("PUT", "/api/web-apps/cloudflare/settings", (e) => require(`${__hooks}/mothership`).HandleWebCloudflarePut(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/{id}/domains/{domainId}/cloudflare/preview", (e) => require(`${__hooks}/mothership`).HandleWebCloudflarePreview(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-apps/{id}/domains/{domainId}/cloudflare/apply", (e) => require(`${__hooks}/mothership`).HandleWebCloudflareApply(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-project-recovery", (e) => require(`${__hooks}/mothership`).HandleProjectRecoveryGet(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-project-recovery/projects/{projectId}/preview", (e) => require(`${__hooks}/mothership`).HandleProjectRecoveryPreview(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-project-recovery/projects/{projectId}/runs", (e) => require(`${__hooks}/mothership`).HandleProjectRecoveryQueue(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-project-recovery/_worker/claim", (e) => require(`${__hooks}/mothership`).HandleProjectRecoveryClaim(e));
routerAdd("POST", "/api/web-project-recovery/_worker/report", (e) => require(`${__hooks}/mothership`).HandleProjectRecoveryReport(e));
routerAdd("GET", "/api/web-project-migrations", (e) => require(`${__hooks}/mothership`).HandleProjectMigrationsGet(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-project-migration/sources", (e) => require(`${__hooks}/mothership`).HandleProjectMigrationSources(e), $apis.requireAuth("users"));
routerAdd("GET", "/api/web-project-migration/sources/{projectId}/preview", (e) => require(`${__hooks}/mothership`).HandleProjectMigrationSourcePreview(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-project-migrations/{id}/approve", (e) => require(`${__hooks}/mothership`).HandleProjectMigrationApprove(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-project-migrations/{id}/release", (e) => require(`${__hooks}/mothership`).HandleProjectMigrationRelease(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-project-migrations/{id}/dns-approve", (e) => require(`${__hooks}/mothership`).HandleProjectMigrationDnsApprove(e), $apis.requireAuth("users"));
routerAdd("POST", "/api/web-project-migration/_operator/{action}", (e) => require(`${__hooks}/mothership`).HandleProjectMigrationOperator(e));
onRecordCreate((e) => require(`${__hooks}/mothership`).GuardProjectMigrationRecord(e));
onRecordUpdate((e) => require(`${__hooks}/mothership`).GuardProjectMigrationRecord(e));
onRecordDelete((e) => require(`${__hooks}/mothership`).GuardProjectMigrationDelete(e));
onRecordUpdateRequest((e) => require(`${__hooks}/mothership`).GuardProjectMigrationRequest(e));
onRecordDeleteRequest((e) => require(`${__hooks}/mothership`).GuardProjectMigrationRequest(e));

//#endregion