Object.defineProperty(exports, Symbol.toStringTag, { value: 'Module' });

//#region src/lib/util/appStoreJson.ts
/** JSON round-trip for $app.store() — Goja objects must not cross goroutines (see PB #7737). */
const parseAppStoreJson = (raw) => {
	if (raw == null || raw === "") return null;
	if (typeof raw !== "string") return null;
	return JSON.parse(raw);
};
const getAppStoreJson = (key) => {
	return parseAppStoreJson($app.store().get(key));
};
const setAppStoreJson = (key, value) => {
	$app.store().set(key, JSON.stringify(value));
};
const updateAppStoreJson = (key, updater) => {
	$app.store().setFunc(key, (raw) => {
		const next = updater(parseAppStoreJson(raw));
		return JSON.stringify(next);
	});
};

//#endregion
//#region src/lib/handlers/adminPlugins/viewStats.ts
const LIVE_VIEW_STATS_TOPIC = "mothership/live/view-stats";
const LIVE_VIEW_STATS_STORE_KEY = "phLiveViewStats";
const fieldInt = (record, name) => {
	const value = record.get(name);
	if (value == null || value === "") return 0;
	const n = Number(value);
	return Number.isFinite(n) ? n : 0;
};
const readStatsViewRecord = (record) => {
	return {
		totalUsers: fieldInt(record, "total_users"),
		totalLegacySubscribers: fieldInt(record, "total_legacy_subscribers"),
		totalFreeSubscribers: fieldInt(record, "total_free_subscribers"),
		totalProSubscribers: fieldInt(record, "total_pro_subscribers"),
		totalProMonthSubscribers: fieldInt(record, "total_pro_month_subscribers"),
		totalProYearSubscribers: fieldInt(record, "total_pro_year_subscribers"),
		totalFounderSubscribers: fieldInt(record, "total_founder_subscribers"),
		totalFlounderSubscribers: fieldInt(record, "total_flounder_subscribers"),
		newUsersLastHour: fieldInt(record, "new_users_last_hour"),
		newUsersLast24Hours: fieldInt(record, "new_users_last_24_hours"),
		newUsersLast7Days: fieldInt(record, "new_users_last_7_days"),
		newUsersLast30Days: fieldInt(record, "new_users_last_30_days"),
		totalInstances: fieldInt(record, "total_instances"),
		totalInstancesLastHour: fieldInt(record, "total_instances_last_hour"),
		totalInstancesLast24Hours: fieldInt(record, "total_instances_last_24_hours"),
		totalInstancesLast7Days: fieldInt(record, "total_instances_last_7_days"),
		totalInstancesLast30Days: fieldInt(record, "total_instances_last_30_days"),
		newInstancesLastHour: fieldInt(record, "new_instances_last_hour"),
		newInstancesLast24Hours: fieldInt(record, "new_instances_last_24_hours"),
		newInstancesLast7Days: fieldInt(record, "new_instances_last_7_days"),
		newInstancesLast30Days: fieldInt(record, "new_instances_last_30_days"),
		updatedAt: (/* @__PURE__ */ new Date()).toISOString()
	};
};
const getLiveViewStats = () => {
	return getAppStoreJson(LIVE_VIEW_STATS_STORE_KEY);
};
const refreshLiveViewStats = () => {
	const record = (() => {
		try {
			return $app.findFirstRecordByFilter("stats", "id != \"\"");
		} catch {
			return null;
		}
	})();
	if (!record) return null;
	const stats = readStatsViewRecord(record);
	setAppStoreJson(LIVE_VIEW_STATS_STORE_KEY, stats);
	return stats;
};
const broadcastLiveViewStats = () => {
	const stats = getLiveViewStats();
	if (!stats) return;
	const message = new SubscriptionMessage({
		name: LIVE_VIEW_STATS_TOPIC,
		data: JSON.stringify(stats)
	});
	const clients = $app.subscriptionsBroker().clients();
	for (const clientId in clients) if (clients[clientId].hasSubscription("mothership/live/view-stats")) clients[clientId].send(message);
};
const sendLiveViewStatsToClient = (client) => {
	const stats = getLiveViewStats();
	if (!stats) return;
	client.send(new SubscriptionMessage({
		name: LIVE_VIEW_STATS_TOPIC,
		data: JSON.stringify(stats)
	}));
};
const initLiveViewStatsAtBoot = () => {
	refreshLiveViewStats();
};
const handleLiveViewStatsCron = () => {
	if (!refreshLiveViewStats()) return;
	broadcastLiveViewStats();
};
const refreshAndBroadcastLiveViewStats = () => {
	if (!refreshLiveViewStats()) return null;
	broadcastLiveViewStats();
	return getLiveViewStats();
};

//#endregion
//#region src/lib/handlers/adminPlugins/platformStats.ts
const LIVE_PLATFORM_TOPIC = "mothership/live/platform";
const LIVE_PLATFORM_STORE_KEY = "phLivePlatformCounts";
const INSTANCE_STATUS_KEYS = [
	"running",
	"starting",
	"porting",
	"vacuuming",
	"idle",
	"failed"
];
const normalizeInstanceStatus = (raw) => {
	const status = (raw || "").trim();
	if (!status) return null;
	if (INSTANCE_STATUS_KEYS.includes(status)) return status;
	return null;
};
const emptyStatusCounts = () => {
	const counts = {};
	for (const key of INSTANCE_STATUS_KEYS) counts[key] = 0;
	return counts;
};
const safeCountRecords = (collection, ...exprs) => {
	try {
		return $app.countRecords(collection, ...exprs);
	} catch {
		return 0;
	}
};
const countInstanceStatus = (key) => {
	return safeCountRecords("instances", $dbx.exp(`status = {:status}`, { status: key }));
};
const countVerifiedUsers = () => {
	return safeCountRecords("verified_users");
};
const countUnverifiedUsers = () => {
	return safeCountRecords("unverified_users");
};
const getLivePlatformStats = () => {
	const stats = getAppStoreJson(LIVE_PLATFORM_STORE_KEY);
	if (!stats?.statusCounts) return null;
	return stats;
};
const recountLivePlatformStats = () => {
	const statusCounts = emptyStatusCounts();
	for (const key of INSTANCE_STATUS_KEYS) statusCounts[key] = countInstanceStatus(key);
	const stats = {
		statusCounts,
		totalUsers: safeCountRecords("users"),
		verifiedUsers: countVerifiedUsers(),
		unverifiedUsers: countUnverifiedUsers(),
		updatedAt: (/* @__PURE__ */ new Date()).toISOString()
	};
	setAppStoreJson(LIVE_PLATFORM_STORE_KEY, stats);
	return stats;
};
const emptyLivePlatformStats = () => ({
	statusCounts: emptyStatusCounts(),
	totalUsers: 0,
	verifiedUsers: 0,
	unverifiedUsers: 0,
	updatedAt: (/* @__PURE__ */ new Date()).toISOString()
});
const applyStatusDelta = (delta) => {
	updateAppStoreJson(LIVE_PLATFORM_STORE_KEY, (old) => {
		const stats = old || emptyLivePlatformStats();
		const statusCounts = { ...stats.statusCounts };
		for (const key of Object.keys(delta)) {
			const next = (statusCounts[key] || 0) + delta[key];
			statusCounts[key] = next < 0 ? 0 : next;
		}
		return {
			...stats,
			statusCounts,
			updatedAt: (/* @__PURE__ */ new Date()).toISOString()
		};
	});
};
const applyUserDelta = (delta) => {
	updateAppStoreJson(LIVE_PLATFORM_STORE_KEY, (old) => {
		const stats = old || emptyLivePlatformStats();
		const next = stats.totalUsers + delta;
		return {
			...stats,
			totalUsers: next < 0 ? 0 : next,
			updatedAt: (/* @__PURE__ */ new Date()).toISOString()
		};
	});
};
const applyVerifiedDelta = (verifiedDelta, unverifiedDelta) => {
	updateAppStoreJson(LIVE_PLATFORM_STORE_KEY, (old) => {
		const stats = old || emptyLivePlatformStats();
		const nextVerified = (stats.verifiedUsers || 0) + verifiedDelta;
		const nextUnverified = (stats.unverifiedUsers || 0) + unverifiedDelta;
		return {
			...stats,
			verifiedUsers: nextVerified < 0 ? 0 : nextVerified,
			unverifiedUsers: nextUnverified < 0 ? 0 : nextUnverified,
			updatedAt: (/* @__PURE__ */ new Date()).toISOString()
		};
	});
};
const broadcastLivePlatformStats = () => {
	const stats = getLivePlatformStats();
	if (!stats) return;
	const message = new SubscriptionMessage({
		name: LIVE_PLATFORM_TOPIC,
		data: JSON.stringify(stats)
	});
	const clients = $app.subscriptionsBroker().clients();
	for (const clientId in clients) if (clients[clientId].hasSubscription("mothership/live/platform")) clients[clientId].send(message);
};
const sendLivePlatformStatsToClient = (client) => {
	const stats = getLivePlatformStats();
	if (!stats) return;
	client.send(new SubscriptionMessage({
		name: LIVE_PLATFORM_TOPIC,
		data: JSON.stringify(stats)
	}));
};
const bumpStatus = (from, to) => {
	const delta = {};
	if (from) delta[from] = (delta[from] || 0) - 1;
	if (to) delta[to] = (delta[to] || 0) + 1;
	if (!Object.keys(delta).length) return;
	applyStatusDelta(delta);
	broadcastLivePlatformStats();
};
const initLivePlatformStatsAtBoot = () => {
	recountLivePlatformStats();
};
/** Full DB recount + SSE broadcast. Safety net for incremental drift (mirror bulk reset, missed hooks). */
const refreshAndBroadcastLivePlatformStats = () => {
	const stats = recountLivePlatformStats();
	broadcastLivePlatformStats();
	return stats;
};
const handleLivePlatformStatsCron = () => {
	refreshAndBroadcastLivePlatformStats();
};
const HandleLivePlatformRefresh = (e) => {
	const stats = refreshAndBroadcastLivePlatformStats();
	refreshAndBroadcastLiveViewStats();
	return e.json(200, stats);
};
const handleLivePlatformInstanceCreate = (e) => {
	const status = normalizeInstanceStatus(e.record.getString("status"));
	if (!status) return;
	bumpStatus(null, status);
};
const handleLivePlatformInstanceUpdate = (e) => {
	const next = normalizeInstanceStatus(e.record.getString("status"));
	const prev = normalizeInstanceStatus(e.record.original().getString("status"));
	if (next === prev) return;
	bumpStatus(prev, next);
};
const handleLivePlatformInstanceDelete = (e) => {
	const status = normalizeInstanceStatus(e.record.getString("status"));
	if (!status) return;
	bumpStatus(status, null);
};
const handleLivePlatformUserCreate = (e) => {
	applyUserDelta(1);
	const verified = e.record.getBool("verified");
	applyVerifiedDelta(verified ? 1 : 0, verified ? 0 : 1);
	broadcastLivePlatformStats();
};
const handleLivePlatformUserDelete = (e) => {
	applyUserDelta(-1);
	const verified = e.record.getBool("verified");
	applyVerifiedDelta(verified ? -1 : 0, verified ? 0 : -1);
	broadcastLivePlatformStats();
};
const handleLivePlatformUserUpdate = (e) => {
	const next = e.record.getBool("verified");
	if (next === e.record.original().getBool("verified")) return;
	applyVerifiedDelta(next ? 1 : -1, next ? -1 : 1);
	broadcastLivePlatformStats();
};

//#endregion
//#region src/lib/handlers/edge/api/HandleEdgeHeartbeat.ts
const HandleEdgeHeartbeat = (e) => {
	const { body } = e.requestInfo();
	const edgeId = body?.edge_id;
	if (!edgeId || typeof edgeId !== "string") throw new BadRequestError("edge_id is required");
	const stats = body?.stats ?? {};
	let record;
	try {
		record = $app.findFirstRecordByData("edges", "edge_id", edgeId);
	} catch {
		const collection = $app.findCollectionByNameOrId("edges");
		record = new Record(collection);
		record.set("edge_id", edgeId);
		record.set("label", typeof body?.label === "string" ? body.label : edgeId);
	}
	record.set("last_seen", (/* @__PURE__ */ new Date()).toISOString());
	record.set("status", "online");
	record.set("stats", stats);
	$app.save(record);
	return e.json(200, {
		ok: true,
		id: record.id
	});
};

//#endregion
//#region src/lib/handlers/edge/cron/markStaleEdges.ts
const STALE_MS = 3e4;
const OFFLINE_MS = 6e4;
const markStaleEdges = () => {
	const now = Date.now();
	const records = $app.findRecordsByFilter("edges", "1=1").filter((r) => !!r);
	for (const record of records) {
		const lastSeenRaw = record.get("last_seen");
		if (!lastSeenRaw) continue;
		const lastSeen = new Date(String(lastSeenRaw)).getTime();
		if (Number.isNaN(lastSeen)) continue;
		const age = now - lastSeen;
		let status = "online";
		if (age > OFFLINE_MS) status = "offline";
		else if (age > STALE_MS) status = "stale";
		if (record.get("status") !== status) {
			record.set("status", status);
			$app.save(record);
		}
	}
};

//#endregion
//#region src/lib/util/Logger.ts
const mkLog = (namespace) => (...s) => console.log(`[${namespace}]`, ...s.map((p) => {
	if (typeof p === "object") return JSON.stringify(p, null, 2);
	return p;
}));
const dbg = (...args) => console.log(args);
const interpolateString = (template, dict) => {
	return template.replace(/\{\$(\w+)\}/g, (match, key) => {
		dbg({
			match,
			key
		});
		const lowerKey = key.toLowerCase();
		return dict.hasOwnProperty(lowerKey) ? dict[lowerKey] || "" : match;
	});
};

//#endregion
//#region src/lib/handlers/operatorAdmin/operatorSettings.ts
const OPERATOR_SETTINGS_NAME = "operator_settings";
const DEFAULT_SERVER_TIMEZONE = "Indian/Reunion";
const DEFAULT_BACKUP_S3_PREFIX = "instances";
const DEFAULT_BACKUP_S3_REGION = "auto";
const envBoolean = (name, fallback) => {
	const raw = envString(name).trim().toLowerCase();
	if (!raw) return fallback;
	return [
		"1",
		"true",
		"yes",
		"on"
	].includes(raw);
};
const envNumber = (name, fallback) => {
	const value = Number(envString(name));
	if (!Number.isFinite(value) || value < 0) return fallback;
	return value;
};
const envString = (name, fallback = "") => {
	const osValue = typeof $os === "undefined" ? "" : $os.getenv(name);
	return `${process.env[name] || osValue || fallback}`;
};
const currentPocketBaseSettings = () => {
	try {
		if (typeof $app === "undefined") return null;
		return $app.settings();
	} catch {
		return null;
	}
};
const normalizeServerTimezone = (value, fallback = DEFAULT_SERVER_TIMEZONE) => {
	const raw = `${value || ""}`.trim();
	if (!raw) return fallback;
	if (["UTC", "Local"].includes(raw)) return raw;
	if (/^[A-Za-z_]+(?:\/[A-Za-z0-9._+-]+)+$/.test(raw)) return raw;
	return fallback;
};
const normalizeText = (value, fallback = "", max = 500) => {
	return `${(value === void 0 || value === null ? fallback : value) || ""}`.trim().slice(0, max);
};
const normalizeBackupS3Prefix = (value, fallback = DEFAULT_BACKUP_S3_PREFIX) => {
	return normalizeText(value, fallback, 500).replace(/^\/+|\/+$/g, "").replace(/\/{2,}/g, "/") || "instances";
};
const normalizeSMTPAuthMethod = (value) => {
	return `${value || ""}`.trim().toUpperCase() === "LOGIN" ? "LOGIN" : "PLAIN";
};
const normalizeSMTPPort = (value, fallback = 587) => {
	const numeric = Number(value);
	if (!Number.isFinite(numeric)) return fallback;
	return Math.max(1, Math.min(65535, Math.floor(numeric)));
};
const defaultBackupS3Settings = () => ({
	enabled: envBoolean("INSTANCE_BACKUP_S3_ENABLED", false),
	endpoint: normalizeText(envString("INSTANCE_BACKUP_S3_ENDPOINT"), "", 500),
	bucket: normalizeText(envString("INSTANCE_BACKUP_S3_BUCKET"), "", 255),
	prefix: normalizeBackupS3Prefix(envString("INSTANCE_BACKUP_S3_PREFIX"), DEFAULT_BACKUP_S3_PREFIX),
	region: normalizeText(envString("AWS_DEFAULT_REGION"), "auto", 64) || "auto",
	accessKeyId: normalizeText(envString("AWS_ACCESS_KEY_ID"), "", 255),
	secretAccessKey: normalizeText(envString("AWS_SECRET_ACCESS_KEY"), "", 1024)
});
const normalizeBackupS3Settings = (value) => {
	const defaults = defaultBackupS3Settings();
	return {
		enabled: !!value?.enabled,
		endpoint: normalizeText(value?.endpoint, defaults.endpoint, 500),
		bucket: normalizeText(value?.bucket, defaults.bucket, 255),
		prefix: normalizeBackupS3Prefix(value?.prefix, defaults.prefix),
		region: normalizeText(value?.region, defaults.region, 64) || "auto",
		accessKeyId: normalizeText(value?.accessKeyId, defaults.accessKeyId, 255),
		secretAccessKey: normalizeText(value?.secretAccessKey, defaults.secretAccessKey, 1024)
	};
};
const defaultSMTPSettings = () => {
	const pocketBaseSettings = currentPocketBaseSettings();
	const smtp = pocketBaseSettings?.smtp;
	const meta = pocketBaseSettings?.meta;
	const senderAddress = envString("SMTP_SENDER_ADDRESS", meta?.senderAddress || envString("PH_SUPPORT_EMAIL"));
	return {
		enabled: envBoolean("SMTP_ENABLED", smtp?.enabled ?? false),
		host: normalizeText(envString("SMTP_HOST", smtp?.host || ""), "", 255),
		port: normalizeSMTPPort(envString("SMTP_PORT"), smtp?.port || 587),
		username: normalizeText(envString("SMTP_USERNAME", smtp?.username || ""), "", 255),
		password: normalizeText(envString("SMTP_PASSWORD", smtp?.password || ""), "", 1024),
		authMethod: normalizeSMTPAuthMethod(envString("SMTP_AUTH_METHOD", smtp?.authMethod || "PLAIN")),
		tls: envBoolean("SMTP_TLS", smtp?.tls ?? false),
		localName: normalizeText(envString("SMTP_LOCAL_NAME", smtp?.localName || ""), "", 255),
		senderName: normalizeText(envString("SMTP_SENDER_NAME", meta?.senderName || "Gestion PocketBase"), "", 255),
		senderAddress: normalizeText(senderAddress, "", 255)
	};
};
const normalizeSMTPSettings = (value) => {
	const defaults = defaultSMTPSettings();
	return {
		enabled: !!value?.enabled,
		host: normalizeText(value?.host, defaults.host, 255),
		port: normalizeSMTPPort(value?.port, defaults.port),
		username: normalizeText(value?.username, defaults.username, 255),
		password: normalizeText(value?.password, defaults.password, 1024),
		authMethod: normalizeSMTPAuthMethod(value?.authMethod || defaults.authMethod),
		tls: !!(value?.tls ?? defaults.tls),
		localName: normalizeText(value?.localName, defaults.localName, 255),
		senderName: normalizeText(value?.senderName, defaults.senderName, 255),
		senderAddress: normalizeText(value?.senderAddress, defaults.senderAddress, 255)
	};
};
const serializeOperatorSettings = (settings) => {
	const { secretAccessKey, ...backupS3 } = settings.backupS3;
	const { password, ...smtp } = settings.smtp;
	return {
		...settings,
		backupS3: {
			...backupS3,
			hasSecretAccessKey: !!secretAccessKey
		},
		smtp: {
			...smtp,
			hasPassword: !!password
		}
	};
};
const defaultOperatorSettings = () => {
	const autoVerifyUsers = envBoolean("PH_AUTO_VERIFY_SIGNUPS", true);
	return {
		publicSignupEnabled: envBoolean("PH_PUBLIC_SIGNUP_ENABLED", false),
		autoVerifyUsers,
		defaultUserQuota: envNumber("PH_SIGNUP_SUBSCRIPTION_QUANTITY", autoVerifyUsers ? 250 : 0),
		defaultSubscription: "free",
		serverTimezone: normalizeServerTimezone(envString("PH_SERVER_TIMEZONE", DEFAULT_SERVER_TIMEZONE)),
		backupS3: defaultBackupS3Settings(),
		smtp: defaultSMTPSettings(),
		defaultInstancePower: true,
		defaultInstanceDevMode: false,
		defaultSyncAdmin: true,
		defaultAutoVacuum: true,
		supportEmail: envString("PH_SUPPORT_EMAIL"),
		maintenanceMessage: "",
		notes: ""
	};
};
const parseSettingsValue = (raw) => {
	if (!raw) return {};
	let parsed = raw;
	if (typeof raw === "string") try {
		parsed = JSON.parse(raw);
	} catch {
		return {};
	}
	if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
	return parsed;
};
const readOperatorSettings = (app = $app) => {
	const defaults = defaultOperatorSettings();
	try {
		const record = app.findFirstRecordByData("settings", "name", OPERATOR_SETTINGS_NAME);
		return normalizeOperatorSettings({
			...defaults,
			...parseSettingsValue(record.getString("value") || record.get("value"))
		});
	} catch {
		return defaults;
	}
};
const writeOperatorSettings = (settings, app = $app) => {
	const collection = app.findCollectionByNameOrId("settings");
	const normalized = normalizeOperatorSettings(settings);
	const record = (() => {
		try {
			return app.findFirstRecordByData("settings", "name", OPERATOR_SETTINGS_NAME);
		} catch {
			const newRecord = new Record(collection);
			newRecord.set("name", OPERATOR_SETTINGS_NAME);
			return newRecord;
		}
	})();
	record.set("value", JSON.stringify(normalized));
	app.save(record);
	applyOperatorMailSettings(normalized, app);
	return normalized;
};
const applyOperatorMailSettings = (settings, app = $app) => {
	const normalized = normalizeOperatorSettings(settings);
	const appSettings = app.settings();
	appSettings.smtp = {
		...appSettings.smtp,
		enabled: normalized.smtp.enabled,
		host: normalized.smtp.host,
		port: normalized.smtp.port,
		username: normalized.smtp.username,
		password: normalized.smtp.password,
		authMethod: normalized.smtp.authMethod,
		tls: normalized.smtp.tls,
		localName: normalized.smtp.localName
	};
	appSettings.meta = {
		...appSettings.meta,
		senderName: normalized.smtp.senderName || appSettings.meta.senderName,
		senderAddress: normalized.smtp.senderAddress || appSettings.meta.senderAddress
	};
	app.save(appSettings);
};
const normalizeOperatorSettings = (value) => {
	const defaults = defaultOperatorSettings();
	const defaultSubscription = `${value.defaultSubscription || defaults.defaultSubscription}`;
	return {
		publicSignupEnabled: !!value.publicSignupEnabled,
		autoVerifyUsers: !!value.autoVerifyUsers,
		defaultUserQuota: Math.max(0, Math.floor(Number(value.defaultUserQuota ?? defaults.defaultUserQuota) || 0)),
		defaultSubscription: [
			"free",
			"premium",
			"founder",
			"flounder",
			"legacy"
		].includes(defaultSubscription) ? defaultSubscription : defaults.defaultSubscription,
		serverTimezone: normalizeServerTimezone(value.serverTimezone, defaults.serverTimezone),
		backupS3: normalizeBackupS3Settings(value.backupS3 || defaults.backupS3),
		smtp: normalizeSMTPSettings(value.smtp || defaults.smtp),
		defaultInstancePower: value.defaultInstancePower ?? defaults.defaultInstancePower,
		defaultInstanceDevMode: value.defaultInstanceDevMode ?? defaults.defaultInstanceDevMode,
		defaultSyncAdmin: value.defaultSyncAdmin ?? defaults.defaultSyncAdmin,
		defaultAutoVacuum: value.defaultAutoVacuum ?? defaults.defaultAutoVacuum,
		supportEmail: `${value.supportEmail || ""}`.trim(),
		maintenanceMessage: `${value.maintenanceMessage || ""}`.trim(),
		notes: `${value.notes || ""}`.trim()
	};
};

//#endregion
//#region src/lib/handlers/instance/api/HandleInstanceBackups.ts
const BACKUP_FORMAT = "gestion-pocketbase-instance-backup-v1";
const BACKUP_DIRS = [
	"pb_data",
	"pb_public",
	"pb_migrations",
	"pb_hooks"
];
const REQUIRED_RESTORE_DIR = "pb_data";
const AUXILIARY_DB_FILES = [
	"auxiliary.db",
	"auxiliary.db-shm",
	"auxiliary.db-wal"
];
const SETTINGS_PARAM_ID = "settings";
const MAX_STOP_WAIT_SECONDS = 120;
const DIR_MODE = 493;
const PRIVATE_DIR_MODE = 448;
const PRIVATE_FILE_MODE = 384;
const DEFAULT_IMPORT_CHUNK_SIZE_BYTES = 32 * 1024 * 1024;
const MIN_IMPORT_CHUNK_SIZE_BYTES = 1024 * 1024;
const MAX_IMPORT_CHUNKS = 2e4;
const MAX_BACKUP_NAME_LENGTH = 120;
const DEFAULT_BACKUP_GZIP_LEVEL = 1;
const DEFAULT_BACKUP_NICE_LEVEL = 19;
const DEFAULT_BACKUP_IONICE_CLASS = 3;
const DEFAULT_BACKUP_POLICY_CRON = "0 2 * * *";
const DEFAULT_BACKUP_POLICY_LOCAL_RETENTION_COUNT = 7;
const DEFAULT_BACKUP_POLICY_LOCAL_RETENTION_DAYS = 14;
const DEFAULT_BACKUP_POLICY_REMOTE_RETENTION_COUNT = 30;
const DEFAULT_BACKUP_POLICY_REMOTE_RETENTION_DAYS = 90;
const LITESTREAM_SERVICE_NAME = "pockethost-litestream";
const DEFAULT_LITESTREAM_SYNC_INTERVAL = "10s";
const DEFAULT_LITESTREAM_MONITOR_INTERVAL = "10s";
const DEFAULT_LITESTREAM_CHECKPOINT_INTERVAL = "1m";
const DEFAULT_LITESTREAM_SNAPSHOT_INTERVAL = "1h";
const DEFAULT_LITESTREAM_SNAPSHOT_RETENTION = "72h";
const DEFAULT_LITESTREAM_VALIDATION_INTERVAL = "6h";
const DEFAULT_LITESTREAM_S3_REGION = "auto";
const BACKUP_POLICY_CRON_MACROS = [
	"@yearly",
	"@annually",
	"@monthly",
	"@weekly",
	"@daily",
	"@midnight",
	"@hourly",
	"@weekdays",
	"@weekends"
];
const dataRoot$3 = () => {
	const envRoot = $os.getenv("DATA_ROOT");
	if (envRoot) return envRoot;
	const appDataDir = `${$app.dataDir()}`;
	const inferred = appDataDir.replace(/\/mothership\/pb_data\/?$/, "");
	if (inferred !== appDataDir) return inferred;
	throw new Error("Impossible de trouver le dossier de donnees des instances.");
};
const backupRoot$1 = () => $os.getenv("INSTANCE_BACKUP_ROOT") || `${dataRoot$3()}/backups/instances`;
const importRoot$1 = () => $os.getenv("INSTANCE_IMPORT_ROOT") || `${dataRoot$3()}/imports`;
const chunkUploadRoot = () => `${importRoot$1()}/.chunked`;
const chunkSessionDir = (instanceId, uploadId) => `${chunkUploadRoot()}/${instanceId}/${uploadId}`;
const chunkPartsDir = (instanceId, uploadId) => `${chunkSessionDir(instanceId, uploadId)}/parts`;
const chunkMetaPath = (instanceId, uploadId) => `${chunkSessionDir(instanceId, uploadId)}/metadata.json`;
const assembledImportDir = (instanceId) => `${importRoot$1()}/assembled/${instanceId}`;
const chunkPartFilename = (index) => `${String(index).padStart(8, "0")}.part`;
const assertSafeInstanceId$4 = (id) => {
	if (!id.match(/^[a-z0-9]+$/)) throw new BadRequestError("Identifiant d'instance invalide.");
};
const assertSafeBackupId = (id) => {
	if (!id.match(/^[a-z0-9]+$/)) throw new BadRequestError("Identifiant de sauvegarde invalide.");
};
const assertSafeUploadId = (id) => {
	if (!id.match(/^[a-zA-Z0-9_-]+$/)) throw new BadRequestError("Identifiant d'upload invalide.");
};
const assertSafeBackupFilename = (filename) => {
	if (!filename.match(/^[a-zA-Z0-9._-]+\.(tar\.gz|tgz|zip)$/)) throw new BadRequestError("Nom de sauvegarde invalide.");
};
const instanceRoot$3 = (id) => `${dataRoot$3()}/instances/${id}`;
const backupDir = (instanceId) => `${backupRoot$1()}/${instanceId}`;
const backupPath = (instanceId, filename) => `${backupDir(instanceId)}/${filename}`;
const pathExists$2 = (path) => {
	try {
		$os.stat(path);
		return true;
	} catch {
		return false;
	}
};
const fileSize = (path) => {
	try {
		return Number($os.stat(path).size());
	} catch {
		return 0;
	}
};
const fileModifiedAt = (path) => {
	try {
		return `${$os.stat(path).modTime().format("2006-01-02T15:04:05Z07:00")}`;
	} catch {
		return "";
	}
};
const runCommand$1 = (name, ...args) => toString($os.cmd(name, ...args).combinedOutput()).trim();
const parseIntegerEnv = (name, fallback, min, max) => {
	const raw = `${$os.getenv(name) || ""}`.trim();
	if (!raw) return fallback;
	const value = Number(raw);
	if (!Number.isFinite(value)) return fallback;
	return Math.max(min, Math.min(max, Math.floor(value)));
};
const commandExists = (name) => {
	if (!name.match(/^[a-z0-9_-]+$/i)) return false;
	return runCommand$1("sh", "-c", `command -v ${name} >/dev/null 2>&1; echo $?`) === "0";
};
const backupGzipLevel = () => parseIntegerEnv("INSTANCE_BACKUP_GZIP_LEVEL", DEFAULT_BACKUP_GZIP_LEVEL, 1, 9);
const backupCpuLimitPercent = () => parseIntegerEnv("INSTANCE_BACKUP_CPU_LIMIT_PERCENT", 0, 0, 1e3);
const backupNiceLevel = () => parseIntegerEnv("INSTANCE_BACKUP_NICE_LEVEL", DEFAULT_BACKUP_NICE_LEVEL, -20, 19);
const backupIoniceClass = () => parseIntegerEnv("INSTANCE_BACKUP_IONICE_CLASS", DEFAULT_BACKUP_IONICE_CLASS, 0, 3);
const backupIonicePriority = () => parseIntegerEnv("INSTANCE_BACKUP_IONICE_PRIORITY", 7, 0, 7);
const restoreCpuLimitPercent = () => parseIntegerEnv("INSTANCE_RESTORE_CPU_LIMIT_PERCENT", backupCpuLimitPercent(), 0, 1e3);
const restoreNiceLevel = () => parseIntegerEnv("INSTANCE_RESTORE_NICE_LEVEL", backupNiceLevel(), -20, 19);
const restoreIoniceClass = () => parseIntegerEnv("INSTANCE_RESTORE_IONICE_CLASS", backupIoniceClass(), 0, 3);
const restoreIonicePriority = () => parseIntegerEnv("INSTANCE_RESTORE_IONICE_PRIORITY", backupIonicePriority(), 0, 7);
const backupResourceSettings = () => ({
	gzipLevel: backupGzipLevel(),
	cpuLimitPercent: backupCpuLimitPercent(),
	niceLevel: backupNiceLevel(),
	ioniceClass: backupIoniceClass(),
	ionicePriority: backupIonicePriority()
});
const restoreResourceSettings = () => ({
	cpuLimitPercent: restoreCpuLimitPercent(),
	niceLevel: restoreNiceLevel(),
	ioniceClass: restoreIoniceClass(),
	ionicePriority: restoreIonicePriority()
});
const withArchiveResourceLimits = (command, settings, cpuLimitEnvName) => {
	let limited = [...command];
	if (commandExists("nice")) limited = [
		"nice",
		"-n",
		`${settings.niceLevel}`,
		...limited
	];
	if (commandExists("ionice")) {
		const ioniceArgs = [
			"ionice",
			"-c",
			`${settings.ioniceClass}`
		];
		if (settings.ioniceClass === 2) ioniceArgs.push("-n", `${settings.ionicePriority}`);
		limited = [...ioniceArgs, ...limited];
	}
	if (settings.cpuLimitPercent > 0) {
		if (!commandExists("cpulimit")) throw new Error(`${cpuLimitEnvName} requiert le paquet systeme cpulimit.`);
		limited = [
			"cpulimit",
			"-q",
			"-m",
			"-f",
			"-l",
			`${settings.cpuLimitPercent}`,
			"--",
			...limited
		];
	}
	return limited;
};
const withBackupResourceLimits = (command) => withArchiveResourceLimits(command, backupResourceSettings(), "INSTANCE_BACKUP_CPU_LIMIT_PERCENT");
const withRestoreResourceLimits = (command) => withArchiveResourceLimits(command, restoreResourceSettings(), "INSTANCE_RESTORE_CPU_LIMIT_PERCENT");
const runBackupArchiveCommand = (tmpPath, root, stagingDir) => {
	const command = withBackupResourceLimits([
		"tar",
		"-I",
		`gzip -${backupResourceSettings().gzipLevel}`,
		"-cf",
		tmpPath,
		"-C",
		root,
		...BACKUP_DIRS,
		"-C",
		stagingDir,
		"manifest.json"
	]);
	return runCommand$1(command[0], ...command.slice(1));
};
const runArchiveCommand = (limited, command) => {
	const runnable = limited ? withRestoreResourceLimits(command) : command;
	return runCommand$1(runnable[0], ...runnable.slice(1));
};
const sleepOneSecond = () => {
	$os.cmd("sleep", "1").combinedOutput();
};
const errorMessage = (error) => {
	if (error instanceof Error) return error.message;
	return `${error}`;
};
const backupPolicyServerTimezone = () => readOperatorSettings().serverTimezone || "Indian/Reunion";
const resolveBackupPolicyCronTimezone = () => {
	const configuredTimezone = backupPolicyServerTimezone();
	const zone = new Timezone(configuredTimezone);
	const loadedName = zone.string();
	if (![
		"UTC",
		"Etc/UTC",
		"Local"
	].includes(configuredTimezone) && loadedName === "UTC") throw new Error(`Fuseau horaire invalide: ${configuredTimezone}`);
	return {
		configuredTimezone,
		zone
	};
};
const appliedBackupPolicyServerTimezone = () => {
	try {
		return resolveBackupPolicyCronTimezone().configuredTimezone;
	} catch {
		return DEFAULT_SERVER_TIMEZONE;
	}
};
const applyBackupPolicyCronTimezone = () => {
	const log = mkLog("cron:instance:backup-policy");
	try {
		const { configuredTimezone, zone } = resolveBackupPolicyCronTimezone();
		$app.cron().setTimezone(zone);
		return configuredTimezone;
	} catch (error) {
		const fallbackZone = new Timezone(DEFAULT_SERVER_TIMEZONE);
		$app.cron().setTimezone(fallbackZone);
		log(`${errorMessage(error)}; fallback ${DEFAULT_SERVER_TIMEZONE}`);
		return DEFAULT_SERVER_TIMEZONE;
	}
};
const backupPolicyCapabilities = () => ({
	s3Enabled: s3BackupsAvailable(),
	serverTimezone: appliedBackupPolicyServerTimezone()
});
const realpath = (path) => runCommand$1("realpath", path);
const parentDir = (path) => {
	const parts = path.replace(/\/+$/g, "").split("/");
	parts.pop();
	return parts.join("/") || "/";
};
const basename$1 = (path) => path.replace(/\/+$/g, "").split("/").pop() || "";
const sqliteLiteral = (value) => `'${value.replace(/'/g, "''")}'`;
const parsePositiveInteger = (value, field) => {
	const numeric = Number(value);
	if (!Number.isFinite(numeric) || numeric <= 0) throw new BadRequestError(`${field} invalide.`);
	return Math.floor(numeric);
};
const parseNonNegativeInteger = (value, field) => {
	const numeric = Number(value);
	if (!Number.isFinite(numeric) || numeric < 0) throw new BadRequestError(`${field} invalide.`);
	return Math.floor(numeric);
};
const normalizeInteger = (value, fallback, min, max) => {
	const numeric = Number(value);
	if (!Number.isFinite(numeric)) return fallback;
	return Math.max(min, Math.min(max, Math.floor(numeric)));
};
const normalizeBool = (value, fallback) => {
	if (typeof value === "boolean") return value;
	if (typeof value === "string") {
		const normalized = value.trim().toLowerCase();
		if ([
			"true",
			"1",
			"yes",
			"on"
		].includes(normalized)) return true;
		if ([
			"false",
			"0",
			"no",
			"off"
		].includes(normalized)) return false;
	}
	return fallback;
};
const normalizeBackupName = (value) => {
	const normalized = `${typeof value === "string" ? value : ""}`.replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim();
	if (Array.from(normalized).length > MAX_BACKUP_NAME_LENGTH) throw new BadRequestError(`Le nom de la sauvegarde est limite a ${MAX_BACKUP_NAME_LENGTH} caracteres.`);
	return normalized;
};
const isValidBackupPolicyCron = (cron) => {
	const expression = cron.trim();
	if (!expression) return false;
	if (BACKUP_POLICY_CRON_MACROS.includes(expression)) return true;
	const parts = expression.split(/\s+/);
	if (parts.length !== 5) return false;
	return parts.every((part) => /^[\d*,\-/?LW#]+$/i.test(part));
};
const normalizeBackupPolicyCron = (value) => {
	const cron = `${value || DEFAULT_BACKUP_POLICY_CRON}`.trim();
	if (!isValidBackupPolicyCron(cron)) throw new BadRequestError("Planification cron invalide.");
	return cron;
};
const shellLiteral = (value) => `'${value.replace(/'/g, "'\\''")}'`;
const cronExpressionForSchedule = (cron) => {
	const expression = cron.trim();
	switch (expression) {
		case "@yearly":
		case "@annually": return "0 0 1 1 *";
		case "@monthly": return "0 0 1 * *";
		case "@weekly": return "0 0 * * 0";
		case "@daily":
		case "@midnight": return "0 0 * * *";
		case "@hourly": return "0 * * * *";
		case "@weekdays": return "0 0 * * 1-5";
		case "@weekends": return "0 0 * * 0,6";
		default: return expression;
	}
};
const backupPolicyCronNowParts = () => {
	const lines = runCommand$1("sh", "-c", `TZ=${shellLiteral(appliedBackupPolicyServerTimezone())} date '+%M %H %d %m %w %Y' && date -u '+%Y-%m-%dT%H:%M'`).split(/\r?\n/).filter(Boolean);
	const local = (lines[0] || "").trim().split(/\s+/).map(Number);
	const utcMinuteKey = (lines[1] || (/* @__PURE__ */ new Date()).toISOString().slice(0, 16)).trim();
	return {
		minute: local[0] || 0,
		hour: local[1] || 0,
		dayOfMonth: local[2] || 1,
		month: local[3] || 1,
		dayOfWeek: local[4] || 0,
		year: local[5] || (/* @__PURE__ */ new Date()).getUTCFullYear(),
		utcMinuteKey
	};
};
const cronFieldIsWildcard = (field) => field === "*" || field === "?";
const matchesCronNumberField = (field, value, min, max) => {
	if (cronFieldIsWildcard(field)) return true;
	return field.split(",").some((segment) => {
		const [rangePart, stepPart] = segment.split("/");
		const step = stepPart ? Number(stepPart) : 1;
		if (!Number.isFinite(step) || step <= 0) return false;
		let start = min;
		let end = max;
		if (rangePart && rangePart !== "*" && rangePart !== "?") if (rangePart.includes("-")) {
			const [rangeStart, rangeEnd] = rangePart.split("-").map(Number);
			if (!Number.isFinite(rangeStart) || !Number.isFinite(rangeEnd)) return false;
			start = rangeStart;
			end = rangeEnd;
		} else {
			const exact = Number(rangePart);
			if (!Number.isFinite(exact)) return false;
			start = exact;
			end = exact;
		}
		if (value < start || value > end) return false;
		return (value - start) % step === 0;
	});
};
const matchesCronDayOfMonth = (field, now) => {
	if (field.toUpperCase() === "L") return now.dayOfMonth === new Date(now.year, now.month, 0).getDate();
	if (/[W#]/i.test(field)) return false;
	return matchesCronNumberField(field, now.dayOfMonth, 1, 31);
};
const matchesCronDayOfWeek = (field, now) => {
	if (/[LW#]/i.test(field)) return false;
	return matchesCronNumberField(field.split(",").map((part) => part === "7" ? "0" : part).join(","), now.dayOfWeek, 0, 6);
};
const backupPolicyCronDue = (cron, now) => {
	const parts = cronExpressionForSchedule(cron).split(/\s+/);
	if (parts.length !== 5) return false;
	const [minute, hour, dayOfMonth, month, dayOfWeek] = parts;
	if (!matchesCronNumberField(minute, now.minute, 0, 59)) return false;
	if (!matchesCronNumberField(hour, now.hour, 0, 23)) return false;
	if (!matchesCronNumberField(month, now.month, 1, 12)) return false;
	const domWildcard = cronFieldIsWildcard(dayOfMonth);
	const dowWildcard = cronFieldIsWildcard(dayOfWeek);
	const domMatches = matchesCronDayOfMonth(dayOfMonth, now);
	const dowMatches = matchesCronDayOfWeek(dayOfWeek, now);
	if (!domWildcard && !dowWildcard) return domMatches || dowMatches;
	return domMatches && dowMatches;
};
const lastPolicyRunMinuteKey = (policy) => policy.getString("lastRunAt").replace(" ", "T").slice(0, 16);
const slugForFilename = (value) => {
	return value.toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48).replace(/-+$/g, "") || "instance";
};
const timestampForFilename = () => (/* @__PURE__ */ new Date()).toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z");
const createBackupFilename = (instance, kind) => {
	const suffix = kind === "pre-restore" ? "pre-restore" : kind === "import" ? "import" : kind === "scheduled" ? "auto" : "manual";
	return `${timestampForFilename()}-${slugForFilename(instance.getString("subdomain"))}-${suffix}-${instance.id}.tar.gz`;
};
const extensionForImport = (filename) => {
	const lower = filename.toLowerCase();
	if (lower.endsWith(".tar.gz")) return "tar.gz";
	if (lower.endsWith(".tgz")) return "tgz";
	if (lower.endsWith(".zip")) return "zip";
	throw new BadRequestError("Archive non prise en charge. Utilisez .zip, .tgz ou .tar.gz.");
};
const archiveFormatForFilename = (filename) => {
	return extensionForImport(filename) === "zip" ? "zip" : "tar.gz";
};
const createImportBackupFilename = (instance, sourceFilename) => {
	const extension = extensionForImport(sourceFilename);
	return `${timestampForFilename()}-${slugForFilename(instance.getString("subdomain"))}-import-${instance.id}.${extension}`;
};
const normalizedOriginalArchiveName = (value) => {
	return basename$1(`${value || "archive.zip"}`.trim() || "archive.zip").replace(/[\r\n\t]/g, " ").slice(0, 240) || "archive.zip";
};
const isoFromEpochMillis = (value) => {
	const raw = typeof value === "string" ? Number(value) : typeof value === "number" ? value : 0;
	if (!Number.isFinite(raw) || raw <= 0) return "";
	try {
		return new Date(raw).toISOString();
	} catch {
		return "";
	}
};
const timestampFromBackupFilename = (filename) => {
	const match = filename.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z/);
	if (!match) return 0;
	const [, year, month, day, hour, minute, second] = match;
	const value = Date.parse(`${year}-${month}-${day}T${hour}:${minute}:${second}Z`);
	return Number.isFinite(value) ? value : 0;
};
const findInstance$2 = (id) => {
	assertSafeInstanceId$4(id);
	const instance = $app.findRecordById("instances", id);
	if (!instance) throw new BadRequestError(`Instance ${id} introuvable.`);
	return instance;
};
const requireAuthRecord$2 = (authRecord) => {
	if (!authRecord) throw new BadRequestError("Session utilisateur attendue.");
	return authRecord;
};
const assertInstanceAccess$2 = (instance, authRecord) => {
	if (instance.getString("uid") !== authRecord.id && !authRecord.getBool("superAdmin")) throw new BadRequestError("Non autorise.");
};
const assertServerImportAllowed = (authRecord, requestedPath) => {
	if (!authRecord.getBool("superAdmin")) throw new BadRequestError("L'import depuis un chemin serveur est reserve au superadmin.");
	if (!requestedPath.trim()) throw new BadRequestError("Chemin serveur manquant.");
	$os.mkdirAll(importRoot$1(), DIR_MODE);
	const root = realpath(importRoot$1());
	const source = realpath(requestedPath.trim());
	if (source !== root && !source.startsWith(`${root}/`)) throw new BadRequestError(`Archive hors du dossier autorise (${root}).`);
	return source;
};
const assertBackupImportAllowed = (authRecord) => {
	if (!authRecord.getBool("superAdmin")) throw new BadRequestError("L'import d'archive est reserve au superadmin.");
};
const serializeInstanceBackup = (backup) => ({
	id: backup.id,
	user: backup.getString("user"),
	instance: backup.getString("instance"),
	kind: backup.getString("kind"),
	status: backup.getString("status"),
	name: backup.getString("name"),
	filename: backup.getString("filename"),
	remoteKey: backup.getString("remoteKey"),
	sizeBytes: Number(backup.get("sizeBytes") || 0),
	compressedBytes: Number(backup.get("compressedBytes") || 0),
	checksum: backup.getString("checksum"),
	error: backup.getString("error"),
	remoteError: backup.getString("remoteError"),
	restoreState: backup.getString("restoreState"),
	restoreUpdatedAt: backup.getString("restoreUpdatedAt"),
	restoreError: backup.getString("restoreError"),
	manifest: backup.get("manifest"),
	created: backup.getString("created"),
	updated: backup.getString("updated")
});
const sortBackupsNewestFirst$1 = (backups) => {
	return backups.sort((a, b) => {
		const aTimestamp = timestampFromBackupFilename(a.getString("filename")) || Date.parse(a.getString("created") || "") || Date.parse(a.getString("updated") || "") || 0;
		const bTimestamp = timestampFromBackupFilename(b.getString("filename")) || Date.parse(b.getString("created") || "") || Date.parse(b.getString("updated") || "") || 0;
		if (aTimestamp !== bTimestamp) return bTimestamp - aTimestamp;
		const aValue = a.getString("filename") || a.id;
		return (b.getString("filename") || b.id).localeCompare(aValue);
	});
};
const findInstanceBackups$1 = (instanceId) => {
	return sortBackupsNewestFirst$1($app.findRecordsByFilter("instance_backups", "instance = {:instance}", "", 100, 0, { instance: instanceId }).filter((record) => !!record));
};
const normalizeBaseSubdomain$1 = (subdomain) => {
	const clean = subdomain.toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-").replace(/^-+|-+$/g, "");
	return (clean.match(/^[a-z]/) ? clean : `base-${clean}`).slice(0, 34).replace(/-+$/g, "") || "base";
};
const assertValidSubdomain = (subdomain) => {
	if (!subdomain.match(/^[a-z][a-z0-9-]{2,39}$/)) throw new BadRequestError("Nom de nouvelle instance invalide.");
};
const subdomainExists$1 = (subdomain) => {
	try {
		$app.findFirstRecordByData("instances", "subdomain", subdomain);
		return true;
	} catch {
		return false;
	}
};
const suggestRestoreSubdomain = (sourceSubdomain) => {
	const base = normalizeBaseSubdomain$1(sourceSubdomain);
	const fixed = `${base.slice(0, 31).replace(/-+$/g, "")}-restore`;
	if (fixed.match(/^[a-z][a-z0-9-]{2,39}$/) && !subdomainExists$1(fixed)) return fixed;
	for (let i = 0; i < 25; i++) {
		const suffix = $security.randomStringWithAlphabet(5 + Math.min(i, 4), "abcdefghijklmnopqrstuvwxyz0123456789");
		const candidate = `${base.slice(0, 39 - suffix.length).replace(/-+$/g, "")}-${suffix}`;
		if (candidate.match(/^[a-z][a-z0-9-]{2,39}$/) && !subdomainExists$1(candidate)) return candidate;
	}
	throw new BadRequestError("Impossible de generer un nom d'instance disponible.");
};
const getBackupRecord = (instance, backupId) => {
	assertSafeBackupId(backupId);
	const backup = $app.findRecordById("instance_backups", backupId);
	if (!backup || backup.getString("instance") !== instance.id) throw new BadRequestError("Sauvegarde introuvable.");
	return backup;
};
const pathValue$2 = (e, name) => {
	if (!e.request) throw new BadRequestError("Requete invalide.");
	return e.request.pathValue(name);
};
const assertNoRunningOperation = (instanceId) => {
	let running = null;
	try {
		running = $app.findFirstRecordByFilter("instance_backups", "instance = {:instance} && status = \"running\"", { instance: instanceId });
	} catch (error) {
		running = null;
	}
	if (running) throw new BadRequestError("Une operation de sauvegarde est deja en cours pour cette instance.");
	let restoring = [];
	try {
		restoring = $app.findRecordsByFilter("instance_backups", "restoreState = \"running\"", "", 500, 0);
	} catch {
		restoring = [];
	}
	if (restoring.some((backup) => {
		return (`${recordObject(recordObject(backup.get("manifest")).restoreOperation).targetInstanceId || ""}` || backup.getString("instance")) === instanceId;
	})) throw new BadRequestError("Une restauration est deja en cours pour cette instance.");
};
const setInstancePower = (instanceId, power) => {
	const record = findInstance$2(instanceId);
	record.set("power", power);
	$app.save(record);
	return record;
};
const waitUntilIdle = (instanceId) => {
	for (let i = 0; i < MAX_STOP_WAIT_SECONDS; i++) {
		const current = findInstance$2(instanceId);
		if (!current.getBool("power") && current.getString("status").toLowerCase() === "idle") return current;
		sleepOneSecond();
	}
	throw new BadRequestError("L'instance ne s'est pas arretee a temps.");
};
const stopForFilesystemOperation = (instance) => {
	const shouldRestart = instance.getBool("power");
	if (shouldRestart) setInstancePower(instance.id, false);
	waitUntilIdle(instance.id);
	return { shouldRestart };
};
const restartIfNeeded = (instanceId, managedPower) => {
	if (!managedPower.shouldRestart) return;
	try {
		setInstancePower(instanceId, true);
	} catch {}
};
const createBackupRecord = (instance, authRecord, kind, name = "") => {
	const collection = $app.findCollectionByNameOrId("instance_backups");
	const backup = new Record(collection);
	const now = (/* @__PURE__ */ new Date()).toISOString();
	backup.set("user", instance.getString("uid") || authRecord.id);
	backup.set("instance", instance.id);
	backup.set("kind", kind);
	backup.set("status", "running");
	backup.set("name", normalizeBackupName(name));
	backup.set("filename", "");
	backup.set("sizeBytes", 0);
	backup.set("compressedBytes", 0);
	backup.set("checksum", "");
	backup.set("error", "");
	backup.set("remoteError", "");
	backup.set("manifest", { operation: {
		phase: "queued",
		label: kind === "manual" ? "Sauvegarde demandee" : kind === "pre-restore" ? "Sauvegarde de securite demandee" : kind === "scheduled" ? "Sauvegarde automatique demandee" : "Import demande",
		percent: 2,
		startedAt: now,
		updatedAt: now
	} });
	$app.save(backup);
	return backup;
};
const recordObject = (value) => {
	if (typeof value === "string") try {
		const parsed = JSON.parse(value);
		return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
	} catch {
		return {};
	}
	if (!value || typeof value !== "object" || Array.isArray(value)) return {};
	return JSON.parse(JSON.stringify(value));
};
const latestBackupRecord = (backup) => {
	try {
		return $app.findRecordById("instance_backups", backup.id) || backup;
	} catch {
		return backup;
	}
};
const updateBackupOperation = (backup, phase, input = {}) => {
	const manifest = recordObject(backup.get("manifest"));
	const currentOperation = manifest.operation && typeof manifest.operation === "object" && !Array.isArray(manifest.operation) ? manifest.operation : {};
	const now = (/* @__PURE__ */ new Date()).toISOString();
	backup.set("manifest", {
		...manifest,
		operation: {
			...currentOperation,
			phase,
			label: input.label || currentOperation.label || phase,
			percent: typeof input.percent === "number" ? Math.max(0, Math.min(99, Math.round(input.percent))) : currentOperation.percent || 0,
			sourceSizeBytes: typeof input.sourceSizeBytes === "number" ? input.sourceSizeBytes : currentOperation.sourceSizeBytes || 0,
			compressedBytes: typeof input.compressedBytes === "number" ? input.compressedBytes : currentOperation.compressedBytes || 0,
			startedAt: currentOperation.startedAt || now,
			updatedAt: now
		}
	});
	$app.save(backup);
};
const updateRestoreOperation = (backup, phase, input = {}) => {
	const record = latestBackupRecord(backup);
	const manifest = recordObject(record.get("manifest"));
	const currentOperation = manifest.restoreOperation && typeof manifest.restoreOperation === "object" && !Array.isArray(manifest.restoreOperation) ? manifest.restoreOperation : {};
	const now = (/* @__PURE__ */ new Date()).toISOString();
	const isComplete = phase === "ready" || phase === "failed";
	const rawPercent = typeof input.percent === "number" ? input.percent : typeof currentOperation.percent === "number" ? currentOperation.percent : 0;
	record.set("manifest", {
		...manifest,
		restoreOperation: {
			...currentOperation,
			phase,
			label: input.label || currentOperation.label || phase,
			percent: Math.max(0, Math.min(isComplete ? 100 : 99, Math.round(rawPercent))),
			mode: input.mode || currentOperation.mode || "in-place",
			targetInstanceId: input.targetInstanceId || currentOperation.targetInstanceId || "",
			targetSubdomain: input.targetSubdomain || currentOperation.targetSubdomain || "",
			sourceSizeBytes: typeof input.sourceSizeBytes === "number" ? input.sourceSizeBytes : currentOperation.sourceSizeBytes || 0,
			compressedBytes: typeof input.compressedBytes === "number" ? input.compressedBytes : currentOperation.compressedBytes || 0,
			error: input.error || (phase === "failed" ? currentOperation.error || "" : ""),
			startedAt: currentOperation.startedAt || now,
			updatedAt: now
		}
	});
	record.set("restoreState", isComplete ? phase : "running");
	record.set("restoreUpdatedAt", now);
	record.set("restoreError", phase === "failed" ? input.error || currentOperation.error || "" : "");
	$app.save(record);
	backup.set("manifest", record.get("manifest"));
};
const sourceSizeBytes = (root) => {
	return runCommand$1("du", "-sb", ...BACKUP_DIRS.map((dir) => `${root}/${dir}`)).split("\n").map((line) => Number(line.trim().split(/\s+/)[0] || 0)).filter((value) => Number.isFinite(value)).reduce((sum, value) => sum + value, 0);
};
const sha256 = (path) => {
	return runCommand$1("sha256sum", path).split(/\s+/)[0] || "";
};
const s3Config = (settings = readOperatorSettings()) => {
	const config = settings.backupS3;
	if (!config.enabled) return null;
	if (!config.endpoint || !config.bucket || !config.accessKeyId || !config.secretAccessKey) throw new Error("Configuration R2/S3 incomplete: endpoint, bucket, access key et secret key requis.");
	return {
		endpoint: config.endpoint,
		bucket: config.bucket,
		prefix: config.prefix || "instances",
		region: config.region || "auto",
		accessKeyId: config.accessKeyId,
		secretAccessKey: config.secretAccessKey
	};
};
const runAwsS3Command = (config, ...args) => {
	if (!commandExists("aws")) throw new Error("AWS CLI n'est pas installe sur ce serveur.");
	return runCommand$1("env", `AWS_ACCESS_KEY_ID=${config.accessKeyId}`, `AWS_SECRET_ACCESS_KEY=${config.secretAccessKey}`, `AWS_DEFAULT_REGION=${config.region}`, "aws", ...args);
};
const remoteKeyFor = (instanceId, filename) => {
	const config = s3Config();
	if (!config) return "";
	return `${config.prefix}/${instanceId}/${filename}`.replace(/^\/+/, "");
};
const uploadBackupToS3 = (instanceId, filename, localPath) => {
	const config = s3Config();
	if (!config) return "";
	const remoteKey = remoteKeyFor(instanceId, filename);
	runAwsS3Command(config, "s3", "cp", localPath, `s3://${config.bucket}/${remoteKey}`, "--endpoint-url", config.endpoint, "--region", config.region);
	return remoteKey;
};
const downloadBackupFromS3 = (remoteKey, localPath) => {
	const config = s3Config();
	if (!config) throw new Error("La sauvegarde locale est absente et R2/S3 est desactive.");
	runAwsS3Command(config, "s3", "cp", `s3://${config.bucket}/${remoteKey}`, localPath, "--endpoint-url", config.endpoint, "--region", config.region);
};
const deleteBackupFromS3 = (remoteKey) => {
	const config = s3Config();
	if (!config || !remoteKey) return "";
	return runAwsS3Command(config, "s3", "rm", `s3://${config.bucket}/${remoteKey}`, "--endpoint-url", config.endpoint, "--region", config.region);
};
const ensureInstanceDirs = (root) => {
	$os.mkdirAll(root, DIR_MODE);
	for (const dir of BACKUP_DIRS) $os.mkdirAll(`${root}/${dir}`, DIR_MODE);
};
const createArchive = (instance, backup, kind) => {
	assertSafeInstanceId$4(instance.id);
	const root = instanceRoot$3(instance.id);
	const dir = backupDir(instance.id);
	const filename = createBackupFilename(instance, kind);
	const finalPath = backupPath(instance.id, filename);
	const tmpPath = `${finalPath}.tmp`;
	const stagingDir = `${dir}/.staging-${backup.id}`;
	const manifestPath = `${stagingDir}/manifest.json`;
	const resourceSettings = backupResourceSettings();
	assertSafeBackupFilename(filename);
	$os.mkdirAll(dir, DIR_MODE);
	$os.removeAll(stagingDir);
	$os.mkdirAll(stagingDir, PRIVATE_DIR_MODE);
	$os.removeAll(tmpPath);
	ensureInstanceDirs(root);
	updateBackupOperation(backup, "scanning", {
		label: "Analyse de la taille des fichiers",
		percent: 18
	});
	const sizeBytes = sourceSizeBytes(root);
	const manifest = {
		format: BACKUP_FORMAT,
		createdAt: (/* @__PURE__ */ new Date()).toISOString(),
		kind,
		instance: {
			id: instance.id,
			subdomain: instance.getString("subdomain"),
			version: instance.getString("version"),
			dev: instance.getBool("dev"),
			syncAdmin: instance.getBool("syncAdmin"),
			autoVacuum: instance.getBool("autoVacuum")
		},
		included: BACKUP_DIRS,
		excluded: ["logs"],
		sourceSizeBytes: sizeBytes,
		compression: {
			format: "tar.gz",
			gzipLevel: resourceSettings.gzipLevel,
			cpuLimitPercent: resourceSettings.cpuLimitPercent,
			niceLevel: resourceSettings.niceLevel,
			ioniceClass: resourceSettings.ioniceClass,
			ionicePriority: resourceSettings.ionicePriority
		}
	};
	try {
		$os.writeFile(manifestPath, JSON.stringify(manifest, null, 2), PRIVATE_FILE_MODE);
		updateBackupOperation(backup, "compressing", {
			label: resourceSettings.cpuLimitPercent > 0 ? `Compression limitee a ${resourceSettings.cpuLimitPercent} % CPU` : "Compression de l'archive en cours",
			percent: 36,
			sourceSizeBytes: sizeBytes
		});
		runBackupArchiveCommand(tmpPath, root, stagingDir);
		$os.rename(tmpPath, finalPath);
		const compressedBytes = fileSize(finalPath);
		updateBackupOperation(backup, "checksum", {
			label: "Calcul de l'empreinte SHA-256",
			percent: 86,
			sourceSizeBytes: sizeBytes,
			compressedBytes
		});
		const checksum = sha256(finalPath);
		updateBackupOperation(backup, "finalizing", {
			label: "Finalisation de la sauvegarde",
			percent: 92,
			sourceSizeBytes: sizeBytes,
			compressedBytes
		});
		return {
			filename,
			localPath: finalPath,
			sizeBytes,
			compressedBytes,
			checksum,
			manifest
		};
	} finally {
		try {
			$os.remove(tmpPath);
		} catch {}
		try {
			$os.removeAll(stagingDir);
		} catch {}
	}
};
const markBackupReady = (backup, details, storage = {}) => {
	const uploadRemote = storage.uploadRemote ?? true;
	backup.set("filename", details.filename);
	backup.set("localPath", details.localPath);
	backup.set("sizeBytes", details.sizeBytes);
	backup.set("compressedBytes", details.compressedBytes);
	backup.set("checksum", details.checksum);
	backup.set("manifest", details.manifest);
	backup.set("error", "");
	try {
		if (uploadRemote && s3Config()) {
			backup.set("manifest", {
				...details.manifest,
				operation: {
					phase: "remote",
					label: "Copie distante R2/S3 en cours",
					percent: 95,
					sourceSizeBytes: details.sizeBytes,
					compressedBytes: details.compressedBytes,
					startedAt: (/* @__PURE__ */ new Date()).toISOString(),
					updatedAt: (/* @__PURE__ */ new Date()).toISOString()
				}
			});
			updateBackupOperation(backup, "remote", {
				label: "Copie distante R2/S3 en cours",
				percent: 95,
				sourceSizeBytes: details.sizeBytes,
				compressedBytes: details.compressedBytes
			});
		}
		const remoteKey = uploadRemote && s3Config() ? uploadBackupToS3(backup.getString("instance"), details.filename, details.localPath) : "";
		backup.set("remoteKey", remoteKey);
		backup.set("remoteError", "");
	} catch (error) {
		backup.set("remoteError", errorMessage(error));
	}
	backup.set("status", "ready");
	backup.set("manifest", {
		...recordObject(details.manifest),
		storage: {
			localEnabled: storage.localEnabled ?? true,
			remoteEnabled: uploadRemote,
			policyId: storage.policyId || "",
			remoteUploaded: !!backup.getString("remoteKey")
		}
	});
	$app.save(backup);
};
const markBackupFailed = (backup, error) => {
	updateBackupOperation(backup, "failed", { label: "Sauvegarde en echec" });
	backup.set("status", "failed");
	backup.set("error", errorMessage(error));
	$app.save(backup);
};
const createBackupForInstance = (instance, authRecord, kind, managePower, skipRunningCheck = false, options = {}) => {
	if (!skipRunningCheck) assertNoRunningOperation(instance.id);
	const backup = createBackupRecord(instance, authRecord, kind, options.name);
	let power = { shouldRestart: false };
	try {
		if (managePower) {
			updateBackupOperation(backup, "stopping", {
				label: instance.getBool("power") ? "Arret de l'instance avant sauvegarde" : "Verification de l'instance",
				percent: 8
			});
			power = stopForFilesystemOperation(instance);
		} else {
			updateBackupOperation(backup, "waiting", {
				label: "Attente de l'arret de l'instance",
				percent: 8
			});
			waitUntilIdle(instance.id);
		}
		updateBackupOperation(backup, "snapshot", {
			label: "Instance arretee, preparation des fichiers",
			percent: 14
		});
		markBackupReady(backup, createArchive(findInstance$2(instance.id), backup, kind), options);
		return backup;
	} catch (error) {
		markBackupFailed(backup, error);
		throw error;
	} finally {
		if (managePower) restartIfNeeded(instance.id, power);
	}
};
const safeTarEntry = (entry) => {
	const normalized = entry.replace(/^\.\/+/, "");
	const parts = normalized.split("/");
	return !!normalized && !normalized.startsWith("/") && !normalized.startsWith("../") && !parts.includes("..");
};
const normalizeArchiveEntry = (entry) => entry.replace(/^\.\/+/, "");
const entryParts = (entry) => normalizeArchiveEntry(entry).split("/").filter(Boolean);
const archiveHasRequiredRestoreData = (entries) => {
	return entries.some((entry) => {
		const parts = entryParts(entry);
		return parts.includes(REQUIRED_RESTORE_DIR) || parts[parts.length - 1] === "data.db";
	});
};
const archiveIncludedDirs = (entries) => {
	const included = entries.map(entryParts).flat().filter((part, index, parts) => BACKUP_DIRS.includes(part) && parts.indexOf(part) === index);
	if (included.includes(REQUIRED_RESTORE_DIR)) return included;
	if (archiveHasRequiredRestoreData(entries)) return [REQUIRED_RESTORE_DIR, ...included];
	return included;
};
const zipListedSizeBytes = (archivePath) => {
	const output = runCommand$1("unzip", "-l", archivePath);
	let total = 0;
	for (const line of output.split("\n")) {
		const summary = line.match(/^\s*(\d+)\s+\d+\s+files?\s*$/i);
		if (summary) return Number(summary[1]);
		const entry = line.match(/^\s*(\d+)\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s+/);
		if (!entry) continue;
		const bytes = Number(entry[1]);
		if (Number.isFinite(bytes)) total += bytes;
	}
	return total;
};
const tarListedSizeBytes = (archivePath) => {
	const output = runCommand$1("tar", "--numeric-owner", "-tvzf", archivePath);
	let total = 0;
	for (const line of output.split("\n")) {
		const parts = line.trim().split(/\s+/);
		if (!(parts[0] || "").startsWith("-")) continue;
		const sizeToken = parts.slice(1).find((part) => part.match(/^\d+$/));
		if (!sizeToken) continue;
		const bytes = Number(sizeToken);
		if (Number.isFinite(bytes)) total += bytes;
	}
	return total;
};
const archiveSourceSizeBytes = (archivePath, filename) => {
	try {
		const sourceBytes = archiveFormatForFilename(filename) === "zip" ? zipListedSizeBytes(archivePath) : tarListedSizeBytes(archivePath);
		return Number.isFinite(sourceBytes) && sourceBytes > 0 ? sourceBytes : 0;
	} catch {
		return 0;
	}
};
const listArchiveEntries = (archivePath, filename, limited = false) => {
	return (archiveFormatForFilename(filename) === "zip" ? runArchiveCommand(limited, [
		"unzip",
		"-Z1",
		archivePath
	]) : runArchiveCommand(limited, [
		"tar",
		"-tzf",
		archivePath
	])).split("\n").map((entry) => entry.trim()).filter(Boolean);
};
const validateArchiveListing = (archivePath, filename, limited = false) => {
	const entries = listArchiveEntries(archivePath, filename, limited);
	if (!entries.length) throw new BadRequestError("Archive vide.");
	for (const entry of entries) if (!safeTarEntry(entry)) throw new BadRequestError("Archive invalide.");
	if (!archiveHasRequiredRestoreData(entries)) throw new BadRequestError(`Archive invalide: dossier ${REQUIRED_RESTORE_DIR} ou fichier data.db manquant.`);
	return entries;
};
const ensureLocalArchive = (instance, backup) => {
	const filename = backup.getString("filename");
	assertSafeBackupFilename(filename);
	const localPath = backupPath(instance.id, filename);
	if (pathExists$2(localPath)) return localPath;
	const remoteKey = backup.getString("remoteKey");
	if (!remoteKey) throw new BadRequestError("Archive locale introuvable.");
	$os.mkdirAll(backupDir(instance.id), DIR_MODE);
	downloadBackupFromS3(remoteKey, localPath);
	backup.set("localPath", localPath);
	backup.set("remoteError", "");
	$app.save(backup);
	return localPath;
};
const readManifest = (extractDir, backup) => {
	const manifestPath = `${extractDir}/manifest.json`;
	if (!pathExists$2(manifestPath)) return {
		format: BACKUP_FORMAT,
		imported: true,
		originalFilename: backup?.getString("filename") || "",
		createdAt: (/* @__PURE__ */ new Date()).toISOString(),
		included: BACKUP_DIRS.filter((dir) => pathExists$2(`${extractDir}/${dir}`)),
		sourceSizeBytes: 0
	};
	const raw = toString($os.readFile(manifestPath));
	const manifest = JSON.parse(raw);
	if (!manifest || manifest.format !== BACKUP_FORMAT) throw new BadRequestError("Format de sauvegarde non pris en charge.");
	return manifest;
};
const extractArchive = (archivePath, filename, extractDir) => {
	if (archiveFormatForFilename(filename) === "zip") {
		runArchiveCommand(true, [
			"unzip",
			"-q",
			archivePath,
			"-d",
			extractDir
		]);
		return;
	}
	runArchiveCommand(true, [
		"tar",
		"-xzf",
		archivePath,
		"-C",
		extractDir
	]);
};
const moveDirectoryContents = (sourceDir, targetDir) => {
	$os.mkdirAll(targetDir, DIR_MODE);
	runCommand$1("find", sourceDir, "-mindepth", "1", "-maxdepth", "1", "-exec", "mv", "{}", targetDir, ";");
};
const findLoosePbDataRoot = (extractDir) => {
	const found = runCommand$1("find", extractDir, "-type", "f", "-name", "data.db", "-print", "-quit");
	if (!found) return "";
	return parentDir(found.split("\n")[0]);
};
const findArchiveContentRoot = (extractDir, normalizedDir) => {
	if (pathExists$2(`${extractDir}/${REQUIRED_RESTORE_DIR}`)) return extractDir;
	const found = runCommand$1("find", extractDir, "-type", "d", "-name", REQUIRED_RESTORE_DIR, "-print", "-quit");
	if (found) return parentDir(found.split("\n")[0]);
	const loosePbDataRoot = findLoosePbDataRoot(extractDir);
	if (!loosePbDataRoot) throw new BadRequestError(`Archive invalide: dossier ${REQUIRED_RESTORE_DIR} ou fichier data.db manquant.`);
	$os.removeAll(normalizedDir);
	moveDirectoryContents(loosePbDataRoot, `${normalizedDir}/${REQUIRED_RESTORE_DIR}`);
	return normalizedDir;
};
const ensureRestorableDirs = (sourceRoot) => {
	if (!pathExists$2(`${sourceRoot}/${REQUIRED_RESTORE_DIR}`)) throw new BadRequestError(`Archive incomplete: ${REQUIRED_RESTORE_DIR} manquant.`);
	for (const dir of BACKUP_DIRS) $os.mkdirAll(`${sourceRoot}/${dir}`, DIR_MODE);
};
const isExternalImportedBackup = (backup, manifest) => {
	return backup.getString("kind") === "import" || manifest.imported === true;
};
const preserveTargetAuxiliaryDbForExternalImport = (instance, sourceRoot, backup, manifest) => {
	if (!isExternalImportedBackup(backup, manifest)) return;
	const targetPbData = `${instanceRoot$3(instance.id)}/${REQUIRED_RESTORE_DIR}`;
	const restoredPbData = `${sourceRoot}/${REQUIRED_RESTORE_DIR}`;
	$os.mkdirAll(restoredPbData, DIR_MODE);
	for (const filename of AUXILIARY_DB_FILES) {
		const restoredPath = `${restoredPbData}/${filename}`;
		try {
			$os.remove(restoredPath);
		} catch {}
		const currentPath = `${targetPbData}/${filename}`;
		if (pathExists$2(currentPath)) runCommand$1("cp", "-p", currentPath, restoredPath);
	}
};
const preserveTargetSettingsForExternalImport = (instance, sourceRoot, backup, manifest) => {
	if (!isExternalImportedBackup(backup, manifest)) return;
	const targetDataDb = `${instanceRoot$3(instance.id)}/${REQUIRED_RESTORE_DIR}/data.db`;
	const restoredDataDb = `${sourceRoot}/${REQUIRED_RESTORE_DIR}/data.db`;
	if (!pathExists$2(restoredDataDb)) return;
	const settingsId = sqliteLiteral(SETTINGS_PARAM_ID);
	const deleteExternalSettings = `DELETE FROM _params WHERE id = ${settingsId};`;
	if (!pathExists$2(targetDataDb)) {
		runCommand$1("sqlite3", restoredDataDb, deleteExternalSettings);
		return;
	}
	runCommand$1("sqlite3", restoredDataDb, [
		`ATTACH DATABASE ${sqliteLiteral(targetDataDb)} AS target_runtime;`,
		deleteExternalSettings,
		`INSERT INTO _params (id, value, created, updated) SELECT id, value, created, updated FROM target_runtime._params WHERE id = ${settingsId};`,
		"DETACH DATABASE target_runtime;"
	].join(" "));
};
const restoreExtractedDirs = (instance, extractDir) => {
	const root = instanceRoot$3(instance.id);
	const rollbackDir = `${root}/.restore-rollback-${Date.now()}-${instance.id}`;
	const failedInstallDir = `${root}/.restore-failed-${Date.now()}-${instance.id}`;
	const movedCurrentDirs = [];
	const installedDirs = [];
	const log = mkLog("instance:backup:restore:filesystem");
	$os.mkdirAll(root, DIR_MODE);
	$os.mkdirAll(rollbackDir, PRIVATE_DIR_MODE);
	try {
		for (const dir of BACKUP_DIRS) if (!pathExists$2(`${extractDir}/${dir}`)) throw new BadRequestError(`Archive incomplete: ${dir} manquant.`);
		for (const dir of BACKUP_DIRS) {
			const current = `${root}/${dir}`;
			if (pathExists$2(current)) {
				$os.rename(current, `${rollbackDir}/${dir}`);
				movedCurrentDirs.push(dir);
			}
		}
		for (const dir of BACKUP_DIRS) {
			$os.rename(`${extractDir}/${dir}`, `${root}/${dir}`);
			installedDirs.push(dir);
		}
	} catch (error) {
		const rollbackErrors = [];
		if (installedDirs.length > 0) {
			try {
				$os.mkdirAll(failedInstallDir, PRIVATE_DIR_MODE);
			} catch (rollbackError) {
				rollbackErrors.push(`creation du dossier d'echec: ${errorMessage(rollbackError)}`);
			}
			for (const dir of [...installedDirs].reverse()) {
				const current = `${root}/${dir}`;
				if (!pathExists$2(current)) continue;
				try {
					$os.rename(current, `${failedInstallDir}/${dir}`);
				} catch (rollbackError) {
					rollbackErrors.push(`mise a l'ecart de ${dir}: ${errorMessage(rollbackError)}`);
				}
			}
		}
		for (const dir of movedCurrentDirs) {
			const previous = `${rollbackDir}/${dir}`;
			if (!pathExists$2(previous)) continue;
			const current = `${root}/${dir}`;
			if (pathExists$2(current)) {
				rollbackErrors.push(`restauration de ${dir}: le dossier cible existe encore`);
				continue;
			}
			try {
				$os.rename(previous, current);
			} catch (rollbackError) {
				rollbackErrors.push(`restauration de ${dir}: ${errorMessage(rollbackError)}`);
			}
		}
		if (rollbackErrors.length > 0) throw new Error(`La restauration a echoue (${errorMessage(error)}). Le rollback automatique est incomplet: ${rollbackErrors.join("; ")}. Les fichiers de secours sont conserves dans ${rollbackDir}.`);
		try {
			$os.removeAll(failedInstallDir);
		} catch (cleanupError) {
			log(`cleanup deferred for failed install ${failedInstallDir}: ${errorMessage(cleanupError)}`);
		}
		try {
			$os.removeAll(rollbackDir);
		} catch (cleanupError) {
			log(`cleanup deferred for restored rollback ${rollbackDir}: ${errorMessage(cleanupError)}`);
		}
		throw error;
	}
	try {
		$os.removeAll(rollbackDir);
	} catch (cleanupError) {
		log(`restore committed; cleanup deferred for ${rollbackDir}: ${errorMessage(cleanupError)}`);
	}
	try {
		if (pathExists$2(failedInstallDir)) $os.removeAll(failedInstallDir);
	} catch (cleanupError) {
		log(`cleanup deferred for ${failedInstallDir}: ${errorMessage(cleanupError)}`);
	}
};
const restoreArchive = (instance, backup, archiveInstance = instance, options = { mode: "in-place" }) => {
	const filename = backup.getString("filename");
	const target = {
		mode: options.mode,
		targetInstanceId: options.targetInstanceId || instance.id,
		targetSubdomain: options.targetSubdomain || instance.getString("subdomain")
	};
	const compressedBytes = Number(backup.get("compressedBytes") || 0);
	const sourceBytes = Number(backup.get("sizeBytes") || 0);
	updateRestoreOperation(backup, "preparing", {
		...target,
		label: "Préparation de l'archive",
		percent: 12,
		sourceSizeBytes: sourceBytes,
		compressedBytes
	});
	let archivePath = "";
	try {
		archivePath = ensureLocalArchive(archiveInstance, backup);
		updateRestoreOperation(backup, "validating", {
			...target,
			label: "Validation de l'archive",
			percent: 18,
			sourceSizeBytes: sourceBytes,
			compressedBytes: compressedBytes || fileSize(archivePath)
		});
		validateArchiveListing(archivePath, filename, true);
	} catch (error) {
		updateRestoreOperation(backup, "failed", {
			...target,
			label: "Restauration échouée",
			percent: 100,
			sourceSizeBytes: sourceBytes,
			compressedBytes: compressedBytes || (archivePath ? fileSize(archivePath) : 0),
			error: errorMessage(error)
		});
		throw error;
	}
	const extractDir = `${instanceRoot$3(instance.id)}/.restore-extract-${backup.id}`;
	const normalizedDir = `${instanceRoot$3(instance.id)}/.restore-normalized-${backup.id}`;
	$os.mkdirAll(instanceRoot$3(instance.id), DIR_MODE);
	$os.removeAll(extractDir);
	$os.removeAll(normalizedDir);
	$os.mkdirAll(extractDir, PRIVATE_DIR_MODE);
	try {
		const resourceSettings = restoreResourceSettings();
		updateRestoreOperation(backup, "extracting", {
			...target,
			label: resourceSettings.cpuLimitPercent > 0 ? `Extraction limitée à ${resourceSettings.cpuLimitPercent} % CPU` : "Extraction de l'archive",
			percent: 32,
			sourceSizeBytes: sourceBytes,
			compressedBytes: compressedBytes || fileSize(archivePath)
		});
		extractArchive(archivePath, filename, extractDir);
		updateRestoreOperation(backup, "normalizing", {
			...target,
			label: "Préparation des dossiers restaurés",
			percent: 68,
			sourceSizeBytes: sourceBytes,
			compressedBytes: compressedBytes || fileSize(archivePath)
		});
		const contentRoot = findArchiveContentRoot(extractDir, normalizedDir);
		ensureRestorableDirs(contentRoot);
		const manifest = readManifest(contentRoot, backup);
		preserveTargetAuxiliaryDbForExternalImport(instance, contentRoot, backup, manifest);
		preserveTargetSettingsForExternalImport(instance, contentRoot, backup, manifest);
		updateRestoreOperation(backup, "replacing", {
			...target,
			label: "Remplacement des fichiers de l'instance",
			percent: 78,
			sourceSizeBytes: Number(manifest.sourceSizeBytes || sourceBytes || 0),
			compressedBytes: compressedBytes || fileSize(archivePath)
		});
		restoreExtractedDirs(instance, contentRoot);
		updateRestoreOperation(backup, "finalizing", {
			...target,
			label: "Finalisation de la restauration",
			percent: 92,
			sourceSizeBytes: Number(manifest.sourceSizeBytes || sourceBytes || 0),
			compressedBytes: compressedBytes || fileSize(archivePath)
		});
		if (manifest.instance?.version) {
			const current = findInstance$2(instance.id);
			current.set("version", manifest.instance.version);
			$app.save(current);
		}
		if (options.markReady !== false) updateRestoreOperation(backup, "ready", {
			...target,
			label: "Restauration terminée",
			percent: 100,
			sourceSizeBytes: Number(manifest.sourceSizeBytes || sourceBytes || 0),
			compressedBytes: compressedBytes || fileSize(archivePath)
		});
	} catch (error) {
		updateRestoreOperation(backup, "failed", {
			...target,
			label: "Restauration échouée",
			percent: 100,
			sourceSizeBytes: sourceBytes,
			compressedBytes: compressedBytes || (archivePath ? fileSize(archivePath) : 0),
			error: errorMessage(error)
		});
		throw error;
	} finally {
		try {
			$os.removeAll(extractDir);
		} catch {}
		try {
			$os.removeAll(normalizedDir);
		} catch {}
	}
};
const createRestoredInstanceFromBackup = (source, authRecord, backup, e) => {
	const { subdomain } = readRestoreNewInput(e, source);
	updateRestoreOperation(backup, "creating", {
		mode: "new-instance",
		targetSubdomain: subdomain,
		label: "Création de la nouvelle instance",
		percent: 8,
		sourceSizeBytes: Number(backup.get("sizeBytes") || 0),
		compressedBytes: Number(backup.get("compressedBytes") || 0)
	});
	const collection = $app.findCollectionByNameOrId("instances");
	const target = new Record(collection);
	target.set("uid", authRecord.id);
	target.set("subdomain", subdomain);
	target.set("status", "idle");
	target.set("power", false);
	target.set("version", source.getString("version"));
	target.set("dev", false);
	target.set("syncAdmin", source.getBool("syncAdmin"));
	target.set("autoVacuum", source.getBool("autoVacuum"));
	target.set("secrets", source.get("secrets"));
	target.set("webhooks", source.get("webhooks"));
	try {
		$app.save(target);
		target.set("dev", false);
		target.set("autoVacuum", source.getBool("autoVacuum"));
		$app.save(target);
		updateRestoreOperation(backup, "created", {
			mode: "new-instance",
			targetInstanceId: target.id,
			targetSubdomain: subdomain,
			label: "Nouvelle instance créée",
			percent: 14,
			sourceSizeBytes: Number(backup.get("sizeBytes") || 0),
			compressedBytes: Number(backup.get("compressedBytes") || 0)
		});
		restoreArchive(target, backup, source, {
			mode: "new-instance",
			targetInstanceId: target.id,
			targetSubdomain: subdomain
		});
		return target;
	} catch (error) {
		updateRestoreOperation(backup, "failed", {
			mode: "new-instance",
			targetInstanceId: target.id || "",
			targetSubdomain: subdomain,
			label: "Restauration échouée",
			percent: 100,
			sourceSizeBytes: Number(backup.get("sizeBytes") || 0),
			compressedBytes: Number(backup.get("compressedBytes") || 0),
			error: errorMessage(error)
		});
		try {
			if (target.id) $app.delete(target);
		} catch {}
		try {
			if (target.id) $os.removeAll(instanceRoot$3(target.id));
		} catch {}
		throw new ApiError(500, "Impossible de restaurer vers une nouvelle instance.", { error });
	}
};
const importBackupFromServerPath = (instance, authRecord, serverPath, options = {}) => {
	const source = assertServerImportAllowed(authRecord, serverPath);
	const originalFilename = normalizedOriginalArchiveName(options.originalFilename || basename$1(source));
	const sourceModifiedAt = options.sourceModifiedAt || fileModifiedAt(source);
	const filename = createImportBackupFilename(instance, originalFilename);
	const dir = backupDir(instance.id);
	const finalPath = backupPath(instance.id, filename);
	const tmpPath = `${finalPath}.tmp`;
	assertSafeBackupFilename(filename);
	$os.mkdirAll(dir, DIR_MODE);
	$os.removeAll(tmpPath);
	try {
		runCommand$1("cp", source, tmpPath);
		validateArchiveListing(tmpPath, filename);
		$os.rename(tmpPath, finalPath);
	} catch (error) {
		try {
			$os.remove(tmpPath);
		} catch {}
		throw error;
	}
	return {
		filename,
		localPath: finalPath,
		originalFilename,
		sourceModifiedAt
	};
};
const importBackupFromUpload = (instance, uploaded, sourceModifiedAt = "") => {
	const originalFilename = normalizedOriginalArchiveName(uploaded.originalName || uploaded.name || "archive.zip");
	const filename = createImportBackupFilename(instance, originalFilename);
	const dir = backupDir(instance.id);
	const finalPath = backupPath(instance.id, filename);
	const tmpName = `${filename}.tmp`;
	const tmpPath = `${dir}/${tmpName}`;
	assertSafeBackupFilename(filename);
	$os.mkdirAll(dir, DIR_MODE);
	$os.removeAll(tmpPath);
	const fs = $filesystem.local(dir);
	try {
		fs.uploadFile(uploaded, tmpName);
		validateArchiveListing(tmpPath, filename);
		$os.rename(tmpPath, finalPath);
	} catch (error) {
		try {
			$os.remove(tmpPath);
		} catch {}
		throw error;
	} finally {
		fs.close();
	}
	return {
		filename,
		localPath: finalPath,
		originalFilename,
		sourceModifiedAt
	};
};
const readArchiveLastModifiedInput = (e) => {
	try {
		return isoFromEpochMillis(e.request.formValue("archiveLastModified"));
	} catch {
		return "";
	}
};
const readServerPathInput = (e) => {
	try {
		const value = e.request.formValue("serverPath");
		if (value) return value;
	} catch {}
	try {
		let data = new DynamicModel({ serverPath: "" });
		e.bindBody(data);
		data = JSON.parse(JSON.stringify(data));
		return data.serverPath || "";
	} catch {}
	return "";
};
const readRestoreNewInput = (e, source) => {
	let data = new DynamicModel({ subdomain: "" });
	try {
		e.bindBody(data);
		data = JSON.parse(JSON.stringify(data));
	} catch {
		data = { subdomain: "" };
	}
	const subdomain = (data.subdomain || "").trim().toLowerCase() || suggestRestoreSubdomain(source.getString("subdomain"));
	assertValidSubdomain(subdomain);
	if (subdomainExists$1(subdomain)) throw new BadRequestError("Ce nom de nouvelle instance est deja utilise.");
	return { subdomain };
};
const readRestoreInput = (e, source) => {
	let data = new DynamicModel({ targetInstanceId: "" });
	try {
		e.bindBody(data);
		data = JSON.parse(JSON.stringify(data));
	} catch {
		data = { targetInstanceId: "" };
	}
	const targetInstanceId = `${data.targetInstanceId || source.id}`.trim();
	assertSafeInstanceId$4(targetInstanceId);
	return { targetInstanceId };
};
const backupManifestObject = (backup) => {
	return recordObject(backup.get("manifest"));
};
const runningBackupPolicyIds = /* @__PURE__ */ new Set();
const s3BackupsAvailable = () => {
	try {
		return !!s3Config() && commandExists("aws");
	} catch {
		return false;
	}
};
const TestBackupS3Config = (settings = readOperatorSettings()) => {
	const config = s3Config(settings);
	if (!config) throw new BadRequestError("S3/R2 est desactive.");
	runAwsS3Command(config, "s3api", "head-bucket", "--bucket", config.bucket, "--endpoint-url", config.endpoint, "--region", config.region);
	return {
		ok: true,
		bucket: config.bucket,
		endpoint: config.endpoint,
		prefix: config.prefix
	};
};
const litestreamRoot = () => $os.getenv("LITESTREAM_ROOT") || `${dataRoot$3()}/litestream`;
const litestreamConfigPath = () => `${litestreamRoot()}/litestream.yml`;
const litestreamDbPath = (instanceId) => `${instanceRoot$3(instanceId)}/pb_data/data.db`;
const durationSeconds = (value) => {
	const match = `${value || ""}`.trim().match(/^(\d+)(s|m|h)$/i);
	if (!match) return 0;
	const amount = Number(match[1]);
	const unit = match[2].toLowerCase();
	if (!Number.isFinite(amount) || amount <= 0) return 0;
	if (unit === "h") return amount * 60 * 60;
	if (unit === "m") return amount * 60;
	return amount;
};
const normalizeLitestreamDuration = (value, fallback, field, minSeconds, maxSeconds) => {
	const raw = `${value || fallback}`.trim().toLowerCase();
	const seconds = durationSeconds(raw);
	if (!seconds || seconds < minSeconds || seconds > maxSeconds) throw new BadRequestError(`${field} invalide. Utilisez une duree comme 10s, 5m ou 1h.`);
	return raw;
};
const shortestDuration = (values, fallback) => {
	const normalized = values.filter((value) => durationSeconds(value) > 0);
	if (!normalized.length) return fallback;
	return normalized.sort((a, b) => durationSeconds(a) - durationSeconds(b))[0] || fallback;
};
const longestDuration = (values, fallback) => {
	const normalized = values.filter((value) => durationSeconds(value) > 0);
	if (!normalized.length) return fallback;
	return normalized.sort((a, b) => durationSeconds(b) - durationSeconds(a))[0] || fallback;
};
const yamlValue = (value) => JSON.stringify(value);
const normalizeLitestreamText = (value, fallback, max) => {
	return `${(value === void 0 || value === null ? fallback : value) || ""}`.trim().slice(0, max);
};
const normalizeLitestreamPrefix = (value) => {
	return normalizeLitestreamText(value, "", 500).replace(/^\/+|\/+$/g, "").replace(/\/{2,}/g, "/");
};
const litestreamDefaultS3Values = () => {
	try {
		const config = s3Config();
		if (config) return {
			endpoint: config.endpoint,
			bucket: config.bucket,
			prefix: config.prefix,
			region: config.region
		};
	} catch {}
	return {
		endpoint: "",
		bucket: "",
		prefix: "",
		region: DEFAULT_LITESTREAM_S3_REGION
	};
};
const litestreamCapabilities = () => ({
	s3PerInstance: true,
	litestreamInstalled: commandExists("litestream"),
	pm2Installed: commandExists("pm2"),
	serviceName: LITESTREAM_SERVICE_NAME,
	configPath: litestreamConfigPath()
});
const litestreamReplicaPathFor = (instanceId, prefix = "") => {
	return [
		normalizeLitestreamPrefix(prefix),
		"litestream",
		"instances",
		instanceId,
		"data.db"
	].filter(Boolean).join("/");
};
const litestreamS3ConfigFor = (policy, instanceId) => {
	const prefix = normalizeLitestreamPrefix(policy.getString("s3Prefix"));
	const bucket = normalizeLitestreamText(policy.getString("s3Bucket"), "", 255);
	const replicaPath = litestreamReplicaPathFor(instanceId, prefix);
	return {
		endpoint: normalizeLitestreamText(policy.getString("s3Endpoint"), "", 500),
		bucket,
		prefix,
		region: normalizeLitestreamText(policy.getString("s3Region"), DEFAULT_LITESTREAM_S3_REGION, 64) || DEFAULT_LITESTREAM_S3_REGION,
		accessKeyId: normalizeLitestreamText(policy.getString("s3AccessKeyId"), "", 255),
		secretAccessKey: normalizeLitestreamText(policy.getString("s3SecretAccessKey"), "", 1024),
		forcePathStyle: policy.getBool("s3ForcePathStyle"),
		replicaPath,
		replicaUrl: bucket && replicaPath ? `s3://${bucket}/${replicaPath}` : ""
	};
};
const litestreamS3MissingFields = (config) => {
	const missing = [];
	if (!config.endpoint) missing.push("endpoint");
	if (!config.bucket) missing.push("bucket");
	if (!config.accessKeyId) missing.push("access key");
	if (!config.secretAccessKey) missing.push("secret key");
	return missing;
};
const litestreamPolicyHasS3Config = (policy) => {
	return litestreamS3MissingFields(litestreamS3ConfigFor(policy, policy.getString("instance"))).length === 0;
};
const litestreamPolicyCollection = () => $app.findCollectionByNameOrId("instance_litestream_replicas");
const litestreamPm2Status = () => {
	if (!commandExists("pm2")) return "";
	try {
		const output = runCommand$1("pm2", "jlist");
		const processes = JSON.parse(output || "[]");
		if (!Array.isArray(processes)) return "";
		const process = processes.find((entry) => entry && entry.name === LITESTREAM_SERVICE_NAME);
		if (!process || !process.pm2_env) return "";
		return `${process.pm2_env.status || ""}`;
	} catch {
		return "";
	}
};
const updateLitestreamPolicyState = (policy, status, input = {}) => {
	policy.set("status", status);
	policy.set("lastCheckedAt", (/* @__PURE__ */ new Date()).toISOString());
	if (typeof input.lastError === "string") policy.set("lastError", input.lastError);
	if (typeof input.lastStartedAt === "string") policy.set("lastStartedAt", input.lastStartedAt);
	if (typeof input.lastStoppedAt === "string") policy.set("lastStoppedAt", input.lastStoppedAt);
	$app.save(policy);
};
const runtimeLitestreamStatus = (policy) => {
	if (!policy.getBool("enabled")) return "disabled";
	const capabilities = litestreamCapabilities();
	if (!litestreamPolicyHasS3Config(policy) || !capabilities.litestreamInstalled || !capabilities.pm2Installed) return "unavailable";
	const status = litestreamPm2Status();
	if (status === "online") return "running";
	if (status) return "failed";
	return policy.getString("status") === "running" ? "failed" : "configured";
};
const refreshLitestreamPolicyState = (policy) => {
	const status = runtimeLitestreamStatus(policy);
	if (policy.getString("status") !== status || policy.getBool("enabled")) updateLitestreamPolicyState(policy, status);
	return policy;
};
const serializeLitestreamPolicy = (policy) => ({
	id: policy.id,
	user: policy.getString("user"),
	instance: policy.getString("instance"),
	enabled: policy.getBool("enabled"),
	status: policy.getString("status") || "disabled",
	replicaPath: policy.getString("replicaPath"),
	s3Endpoint: policy.getString("s3Endpoint"),
	s3Bucket: policy.getString("s3Bucket"),
	s3Prefix: policy.getString("s3Prefix"),
	s3Region: policy.getString("s3Region") || DEFAULT_LITESTREAM_S3_REGION,
	s3AccessKeyId: policy.getString("s3AccessKeyId"),
	hasS3SecretAccessKey: !!policy.getString("s3SecretAccessKey"),
	s3ForcePathStyle: policy.getBool("s3ForcePathStyle"),
	syncInterval: policy.getString("syncInterval") || DEFAULT_LITESTREAM_SYNC_INTERVAL,
	monitorInterval: policy.getString("monitorInterval") || DEFAULT_LITESTREAM_MONITOR_INTERVAL,
	checkpointInterval: policy.getString("checkpointInterval") || DEFAULT_LITESTREAM_CHECKPOINT_INTERVAL,
	snapshotInterval: policy.getString("snapshotInterval") || DEFAULT_LITESTREAM_SNAPSHOT_INTERVAL,
	snapshotRetention: policy.getString("snapshotRetention") || DEFAULT_LITESTREAM_SNAPSHOT_RETENTION,
	validationInterval: policy.getString("validationInterval") || DEFAULT_LITESTREAM_VALIDATION_INTERVAL,
	lastStartedAt: policy.getString("lastStartedAt"),
	lastStoppedAt: policy.getString("lastStoppedAt"),
	lastCheckedAt: policy.getString("lastCheckedAt"),
	lastError: policy.getString("lastError"),
	created: policy.getString("created"),
	updated: policy.getString("updated")
});
const findLitestreamPolicyForInstance = (instanceId) => {
	try {
		return $app.findFirstRecordByFilter("instance_litestream_replicas", "instance = {:instance}", { instance: instanceId });
	} catch {
		return null;
	}
};
const createDefaultLitestreamPolicy = (instance) => {
	const defaultS3 = litestreamDefaultS3Values();
	const policy = new Record(litestreamPolicyCollection());
	policy.set("user", instance.getString("uid"));
	policy.set("instance", instance.id);
	policy.set("enabled", false);
	policy.set("status", "disabled");
	policy.set("replicaPath", litestreamReplicaPathFor(instance.id, defaultS3.prefix));
	policy.set("s3Endpoint", defaultS3.endpoint);
	policy.set("s3Bucket", defaultS3.bucket);
	policy.set("s3Prefix", defaultS3.prefix);
	policy.set("s3Region", defaultS3.region);
	policy.set("s3AccessKeyId", "");
	policy.set("s3SecretAccessKey", "");
	policy.set("s3ForcePathStyle", false);
	policy.set("syncInterval", DEFAULT_LITESTREAM_SYNC_INTERVAL);
	policy.set("monitorInterval", DEFAULT_LITESTREAM_MONITOR_INTERVAL);
	policy.set("checkpointInterval", DEFAULT_LITESTREAM_CHECKPOINT_INTERVAL);
	policy.set("snapshotInterval", DEFAULT_LITESTREAM_SNAPSHOT_INTERVAL);
	policy.set("snapshotRetention", DEFAULT_LITESTREAM_SNAPSHOT_RETENTION);
	policy.set("validationInterval", DEFAULT_LITESTREAM_VALIDATION_INTERVAL);
	policy.set("lastStartedAt", "");
	policy.set("lastStoppedAt", "");
	policy.set("lastCheckedAt", "");
	policy.set("lastError", "");
	$app.save(policy);
	return policy;
};
const getOrCreateLitestreamPolicy = (instance) => {
	return findLitestreamPolicyForInstance(instance.id) || createDefaultLitestreamPolicy(instance);
};
const readLitestreamPolicyInput = (e) => {
	let data = new DynamicModel({
		enabled: false,
		s3Endpoint: "",
		s3Bucket: "",
		s3Prefix: "",
		s3Region: DEFAULT_LITESTREAM_S3_REGION,
		s3AccessKeyId: "",
		s3SecretAccessKey: "",
		s3ForcePathStyle: false,
		syncInterval: DEFAULT_LITESTREAM_SYNC_INTERVAL,
		monitorInterval: DEFAULT_LITESTREAM_MONITOR_INTERVAL,
		checkpointInterval: DEFAULT_LITESTREAM_CHECKPOINT_INTERVAL,
		snapshotInterval: DEFAULT_LITESTREAM_SNAPSHOT_INTERVAL,
		snapshotRetention: DEFAULT_LITESTREAM_SNAPSHOT_RETENTION,
		validationInterval: DEFAULT_LITESTREAM_VALIDATION_INTERVAL
	});
	try {
		e.bindBody(data);
		data = JSON.parse(JSON.stringify(data));
	} catch {
		data = {};
	}
	return data;
};
const applyLitestreamPolicyInput = (policy, instance, input) => {
	const enabled = normalizeBool(input.enabled, policy.getBool("enabled"));
	const s3Endpoint = normalizeLitestreamText(input.s3Endpoint, policy.getString("s3Endpoint"), 500);
	const s3Bucket = normalizeLitestreamText(input.s3Bucket, policy.getString("s3Bucket"), 255);
	const s3Prefix = normalizeLitestreamPrefix(input.s3Prefix ?? policy.getString("s3Prefix"));
	const s3Region = normalizeLitestreamText(input.s3Region, policy.getString("s3Region") || DEFAULT_LITESTREAM_S3_REGION, 64) || DEFAULT_LITESTREAM_S3_REGION;
	const s3AccessKeyId = normalizeLitestreamText(input.s3AccessKeyId, policy.getString("s3AccessKeyId"), 255);
	const s3SecretAccessKey = normalizeLitestreamText(input.s3SecretAccessKey, "", 1024) || policy.getString("s3SecretAccessKey");
	const s3ForcePathStyle = normalizeBool(input.s3ForcePathStyle, policy.getBool("s3ForcePathStyle"));
	const syncInterval = normalizeLitestreamDuration(input.syncInterval || policy.getString("syncInterval") || DEFAULT_LITESTREAM_SYNC_INTERVAL, DEFAULT_LITESTREAM_SYNC_INTERVAL, "Intervalle de synchronisation", 5, 1440 * 60);
	const monitorInterval = normalizeLitestreamDuration(input.monitorInterval || policy.getString("monitorInterval") || DEFAULT_LITESTREAM_MONITOR_INTERVAL, DEFAULT_LITESTREAM_MONITOR_INTERVAL, "Intervalle de surveillance", 5, 1440 * 60);
	const checkpointInterval = normalizeLitestreamDuration(input.checkpointInterval || policy.getString("checkpointInterval") || DEFAULT_LITESTREAM_CHECKPOINT_INTERVAL, DEFAULT_LITESTREAM_CHECKPOINT_INTERVAL, "Intervalle checkpoint", 10, 1440 * 60);
	const snapshotInterval = normalizeLitestreamDuration(input.snapshotInterval || policy.getString("snapshotInterval") || DEFAULT_LITESTREAM_SNAPSHOT_INTERVAL, DEFAULT_LITESTREAM_SNAPSHOT_INTERVAL, "Intervalle snapshot", 60, 720 * 60 * 60);
	const snapshotRetention = normalizeLitestreamDuration(input.snapshotRetention || policy.getString("snapshotRetention") || DEFAULT_LITESTREAM_SNAPSHOT_RETENTION, DEFAULT_LITESTREAM_SNAPSHOT_RETENTION, "Retention snapshot", 3600, 365 * 24 * 60 * 60);
	const validationInterval = normalizeLitestreamDuration(input.validationInterval || policy.getString("validationInterval") || DEFAULT_LITESTREAM_VALIDATION_INTERVAL, DEFAULT_LITESTREAM_VALIDATION_INTERVAL, "Intervalle de validation", 60, 720 * 60 * 60);
	if (enabled) {
		const capabilities = litestreamCapabilities();
		const missingS3 = litestreamS3MissingFields({
			endpoint: s3Endpoint,
			bucket: s3Bucket,
			prefix: s3Prefix,
			region: s3Region,
			accessKeyId: s3AccessKeyId,
			secretAccessKey: s3SecretAccessKey,
			forcePathStyle: s3ForcePathStyle,
			replicaPath: litestreamReplicaPathFor(instance.id, s3Prefix),
			replicaUrl: s3Bucket ? `s3://${s3Bucket}/${litestreamReplicaPathFor(instance.id, s3Prefix)}` : ""
		});
		if (missingS3.length) throw new BadRequestError(`Parametres R2/S3 incomplets pour cette instance : ${missingS3.join(", ")}.`);
		if (!capabilities.litestreamInstalled) throw new BadRequestError("Litestream n'est pas installe sur ce serveur.");
		if (!capabilities.pm2Installed) throw new BadRequestError("PM2 n'est pas installe sur ce serveur.");
		if (!pathExists$2(litestreamDbPath(instance.id))) throw new BadRequestError("data.db est introuvable. Demarrez l'instance une fois avant d'activer Litestream.");
	}
	policy.set("user", instance.getString("uid"));
	policy.set("instance", instance.id);
	policy.set("enabled", enabled);
	policy.set("status", enabled ? "configured" : "disabled");
	policy.set("replicaPath", litestreamReplicaPathFor(instance.id, s3Prefix));
	policy.set("s3Endpoint", s3Endpoint);
	policy.set("s3Bucket", s3Bucket);
	policy.set("s3Prefix", s3Prefix);
	policy.set("s3Region", s3Region);
	policy.set("s3AccessKeyId", s3AccessKeyId);
	policy.set("s3SecretAccessKey", s3SecretAccessKey);
	policy.set("s3ForcePathStyle", s3ForcePathStyle);
	policy.set("syncInterval", syncInterval);
	policy.set("monitorInterval", monitorInterval);
	policy.set("checkpointInterval", checkpointInterval);
	policy.set("snapshotInterval", snapshotInterval);
	policy.set("snapshotRetention", snapshotRetention);
	policy.set("validationInterval", validationInterval);
	policy.set("lastError", "");
	if (!enabled) policy.set("lastStoppedAt", (/* @__PURE__ */ new Date()).toISOString());
};
const enabledLitestreamPolicies = () => {
	try {
		return $app.findRecordsByFilter("instance_litestream_replicas", "enabled = true", "", 500, 0).filter((record) => !!record);
	} catch {
		return [];
	}
};
const buildLitestreamConfig = (policies) => {
	const validPolicies = [];
	const dbLines = [];
	for (const policy of policies) try {
		const instance = findInstance$2(policy.getString("instance"));
		const dbPath = litestreamDbPath(instance.id);
		if (!pathExists$2(dbPath)) {
			updateLitestreamPolicyState(policy, "failed", { lastError: "data.db introuvable pour cette instance." });
			continue;
		}
		const s3 = litestreamS3ConfigFor(policy, instance.id);
		const missingS3 = litestreamS3MissingFields(s3);
		if (missingS3.length) {
			updateLitestreamPolicyState(policy, "failed", { lastError: `Parametres R2/S3 incomplets : ${missingS3.join(", ")}.` });
			continue;
		}
		policy.set("replicaPath", s3.replicaPath);
		$app.save(policy);
		validPolicies.push(policy);
		dbLines.push(`  - path: ${yamlValue(dbPath)}`);
		dbLines.push(`    monitor-interval: ${policy.getString("monitorInterval") || DEFAULT_LITESTREAM_MONITOR_INTERVAL}`);
		dbLines.push(`    checkpoint-interval: ${policy.getString("checkpointInterval") || DEFAULT_LITESTREAM_CHECKPOINT_INTERVAL}`);
		dbLines.push("    busy-timeout: 30s");
		dbLines.push("    replica:");
		dbLines.push(`      url: ${yamlValue(s3.replicaUrl)}`);
		dbLines.push(`      endpoint: ${yamlValue(s3.endpoint)}`);
		dbLines.push(`      region: ${yamlValue(s3.region)}`);
		dbLines.push(`      access-key-id: ${yamlValue(s3.accessKeyId)}`);
		dbLines.push(`      secret-access-key: ${yamlValue(s3.secretAccessKey)}`);
		dbLines.push(`      force-path-style: ${s3.forcePathStyle ? "true" : "false"}`);
		dbLines.push(`      sync-interval: ${policy.getString("syncInterval") || DEFAULT_LITESTREAM_SYNC_INTERVAL}`);
	} catch (error) {
		updateLitestreamPolicyState(policy, "failed", { lastError: errorMessage(error) });
	}
	if (!validPolicies.length) return {
		config: "",
		policies: validPolicies
	};
	const snapshotInterval = shortestDuration(validPolicies.map((policy) => policy.getString("snapshotInterval")), DEFAULT_LITESTREAM_SNAPSHOT_INTERVAL);
	const snapshotRetention = longestDuration(validPolicies.map((policy) => policy.getString("snapshotRetention")), DEFAULT_LITESTREAM_SNAPSHOT_RETENTION);
	const validationInterval = shortestDuration(validPolicies.map((policy) => policy.getString("validationInterval")), DEFAULT_LITESTREAM_VALIDATION_INTERVAL);
	return {
		config: [
			"logging:",
			"  level: info",
			"  type: text",
			"  stderr: true",
			"snapshot:",
			`  interval: ${snapshotInterval}`,
			`  retention: ${snapshotRetention}`,
			"validation:",
			`  interval: ${validationInterval}`,
			"dbs:",
			...dbLines,
			""
		].join("\n"),
		policies: validPolicies
	};
};
const stopLitestreamService = () => {
	if (!commandExists("pm2")) return;
	try {
		runCommand$1("sh", "-c", `pm2 delete ${LITESTREAM_SERVICE_NAME} >/dev/null 2>&1 || true; pm2 save >/dev/null 2>&1 || true`);
	} catch {}
};
const startOrRestartLitestreamService = (configPath) => {
	runCommand$1("sh", "-c", `set -e
if pm2 jlist | grep -q '"name":"${LITESTREAM_SERVICE_NAME}"'; then
  pm2 delete ${LITESTREAM_SERVICE_NAME} >/dev/null 2>&1 || true
fi
litestream_bin=$(command -v litestream)
pm2 start "$litestream_bin" --name ${LITESTREAM_SERVICE_NAME} --time -- replicate -no-expand-env -config "$1"
pm2 save >/dev/null 2>&1 || true`, "sh", configPath);
};
const reconcileLitestreamService = () => {
	const policies = enabledLitestreamPolicies();
	if (!policies.length) {
		stopLitestreamService();
		try {
			$os.remove(litestreamConfigPath());
		} catch {}
		return [];
	}
	const capabilities = litestreamCapabilities();
	if (!capabilities.litestreamInstalled || !capabilities.pm2Installed) {
		const missing = !capabilities.litestreamInstalled ? "Litestream non installe." : "PM2 non installe.";
		for (const policy of policies) updateLitestreamPolicyState(policy, "unavailable", { lastError: missing });
		stopLitestreamService();
		throw new Error(missing);
	}
	$os.mkdirAll(litestreamRoot(), PRIVATE_DIR_MODE);
	const { config, policies: validPolicies } = buildLitestreamConfig(policies);
	if (!validPolicies.length) {
		stopLitestreamService();
		throw new Error("Aucune base data.db valide pour Litestream.");
	}
	$os.writeFile(litestreamConfigPath(), config, PRIVATE_FILE_MODE);
	startOrRestartLitestreamService(litestreamConfigPath());
	const now = (/* @__PURE__ */ new Date()).toISOString();
	const pm2Status = litestreamPm2Status();
	const status = pm2Status === "online" ? "running" : pm2Status ? "failed" : "configured";
	for (const policy of validPolicies) updateLitestreamPolicyState(policy, status, {
		lastStartedAt: now,
		lastError: status === "failed" ? `PM2 status: ${pm2Status}` : ""
	});
	return validPolicies;
};
const activeBehaviorFor = (value) => {
	return value === "skip-active" ? "skip-active" : "stop-restart";
};
const backupPolicyCollection = () => $app.findCollectionByNameOrId("instance_backup_policies");
const serializeBackupPolicy = (policy) => ({
	id: policy.id,
	user: policy.getString("user"),
	instance: policy.getString("instance"),
	enabled: policy.getBool("enabled"),
	cron: policy.getString("cron"),
	localEnabled: policy.getBool("localEnabled"),
	remoteEnabled: policy.getBool("remoteEnabled"),
	localRetentionCount: Number(policy.get("localRetentionCount") || 0),
	localRetentionDays: Number(policy.get("localRetentionDays") || 0),
	remoteRetentionCount: Number(policy.get("remoteRetentionCount") || 0),
	remoteRetentionDays: Number(policy.get("remoteRetentionDays") || 0),
	activeBehavior: activeBehaviorFor(policy.getString("activeBehavior")),
	lastStatus: policy.getString("lastStatus") || "never",
	lastRunAt: policy.getString("lastRunAt"),
	lastSuccessAt: policy.getString("lastSuccessAt"),
	lastBackup: policy.getString("lastBackup"),
	lastError: policy.getString("lastError"),
	lastDurationSeconds: Number(policy.get("lastDurationSeconds") || 0),
	created: policy.getString("created"),
	updated: policy.getString("updated")
});
const findBackupPolicyForInstance = (instanceId) => {
	try {
		return $app.findFirstRecordByFilter("instance_backup_policies", "instance = {:instance}", { instance: instanceId });
	} catch {
		return null;
	}
};
const createDefaultBackupPolicy = (instance) => {
	const policy = new Record(backupPolicyCollection());
	policy.set("user", instance.getString("uid"));
	policy.set("instance", instance.id);
	policy.set("enabled", false);
	policy.set("cron", DEFAULT_BACKUP_POLICY_CRON);
	policy.set("localEnabled", true);
	policy.set("remoteEnabled", false);
	policy.set("localRetentionCount", DEFAULT_BACKUP_POLICY_LOCAL_RETENTION_COUNT);
	policy.set("localRetentionDays", DEFAULT_BACKUP_POLICY_LOCAL_RETENTION_DAYS);
	policy.set("remoteRetentionCount", DEFAULT_BACKUP_POLICY_REMOTE_RETENTION_COUNT);
	policy.set("remoteRetentionDays", DEFAULT_BACKUP_POLICY_REMOTE_RETENTION_DAYS);
	policy.set("activeBehavior", "stop-restart");
	policy.set("lastStatus", "never");
	policy.set("lastRunAt", "");
	policy.set("lastSuccessAt", "");
	policy.set("lastBackup", "");
	policy.set("lastError", "");
	policy.set("lastDurationSeconds", 0);
	$app.save(policy);
	return policy;
};
const clearObsoleteBackupPolicyError = (policy) => {
	if (!policy.getString("lastError").includes("invalid sort field \"created\"")) return policy;
	policy.set("lastError", "");
	if (policy.getString("lastStatus") === "failed") policy.set("lastStatus", policy.getString("lastSuccessAt") ? "ready" : "never");
	$app.save(policy);
	return policy;
};
const getOrCreateBackupPolicy = (instance) => {
	return clearObsoleteBackupPolicyError(findBackupPolicyForInstance(instance.id) || createDefaultBackupPolicy(instance));
};
const readBackupPolicyInput = (e) => {
	let data = new DynamicModel({
		enabled: false,
		cron: "",
		localEnabled: true,
		remoteEnabled: false,
		localRetentionCount: DEFAULT_BACKUP_POLICY_LOCAL_RETENTION_COUNT,
		localRetentionDays: DEFAULT_BACKUP_POLICY_LOCAL_RETENTION_DAYS,
		remoteRetentionCount: DEFAULT_BACKUP_POLICY_REMOTE_RETENTION_COUNT,
		remoteRetentionDays: DEFAULT_BACKUP_POLICY_REMOTE_RETENTION_DAYS,
		activeBehavior: "stop-restart"
	});
	try {
		e.bindBody(data);
		data = JSON.parse(JSON.stringify(data));
	} catch {
		data = {};
	}
	return data;
};
const applyBackupPolicyInput = (policy, instance, input) => {
	const enabled = normalizeBool(input.enabled, policy.getBool("enabled"));
	const localEnabled = normalizeBool(input.localEnabled, policy.get("localEnabled") === null ? true : policy.getBool("localEnabled"));
	const remoteEnabled = normalizeBool(input.remoteEnabled, policy.getBool("remoteEnabled"));
	const cron = normalizeBackupPolicyCron(input.cron || policy.getString("cron") || DEFAULT_BACKUP_POLICY_CRON);
	const activeBehavior = input.activeBehavior === "skip-active" ? "skip-active" : "stop-restart";
	if (!localEnabled && !remoteEnabled) throw new BadRequestError("Activez au moins une destination de sauvegarde.");
	if (remoteEnabled && !s3BackupsAvailable()) throw new BadRequestError("S3/R2 n'est pas configure sur ce serveur.");
	policy.set("user", instance.getString("uid"));
	policy.set("instance", instance.id);
	policy.set("enabled", enabled);
	policy.set("cron", cron);
	policy.set("localEnabled", localEnabled);
	policy.set("remoteEnabled", remoteEnabled);
	policy.set("localRetentionCount", normalizeInteger(input.localRetentionCount, DEFAULT_BACKUP_POLICY_LOCAL_RETENTION_COUNT, 0, 3650));
	policy.set("localRetentionDays", normalizeInteger(input.localRetentionDays, DEFAULT_BACKUP_POLICY_LOCAL_RETENTION_DAYS, 0, 3650));
	policy.set("remoteRetentionCount", normalizeInteger(input.remoteRetentionCount, DEFAULT_BACKUP_POLICY_REMOTE_RETENTION_COUNT, 0, 3650));
	policy.set("remoteRetentionDays", normalizeInteger(input.remoteRetentionDays, DEFAULT_BACKUP_POLICY_REMOTE_RETENTION_DAYS, 0, 3650));
	policy.set("activeBehavior", activeBehavior);
};
const registerBackupPolicyCron = (policy) => {
	applyBackupPolicyCronTimezone();
	if (!policy.getBool("enabled")) return;
	if (!isValidBackupPolicyCron(policy.getString("cron"))) return;
};
const registerAllBackupPolicyCrons = () => {
	applyBackupPolicyCronTimezone();
};
const enabledBackupPolicies = () => {
	let policies = [];
	try {
		policies = $app.findRecordsByFilter("instance_backup_policies", "enabled = true", "", 500, 0);
	} catch {
		policies = [];
	}
	return policies.filter((policy) => !!policy);
};
const runBackupPolicyCronDispatcher = () => {
	const log = mkLog("cron:instance:backup-policy");
	applyBackupPolicyCronTimezone();
	const now = backupPolicyCronNowParts();
	const policies = enabledBackupPolicies();
	for (const policy of policies) try {
		const cron = policy.getString("cron");
		if (!isValidBackupPolicyCron(cron)) continue;
		if (!backupPolicyCronDue(cron, now)) continue;
		if (lastPolicyRunMinuteKey(policy) === now.utcMinuteKey) continue;
		log(`running policy ${policy.id} (${cron})`);
		runScheduledBackupPolicy(policy.id, "cron");
	} catch (error) {
		log(`policy ${policy.id} failed: ${errorMessage(error)}`);
	}
};
const setPolicyRunState = (policy, status, input = {}) => {
	policy.set("lastStatus", status);
	if (typeof input.lastRunAt === "string") policy.set("lastRunAt", input.lastRunAt);
	if (typeof input.lastSuccessAt === "string") policy.set("lastSuccessAt", input.lastSuccessAt);
	if (typeof input.lastBackup === "string") policy.set("lastBackup", input.lastBackup);
	if (typeof input.lastError === "string") policy.set("lastError", input.lastError);
	if (typeof input.lastDurationSeconds === "number") policy.set("lastDurationSeconds", input.lastDurationSeconds);
	$app.save(policy);
};
const scheduledBackupsForInstance = (instanceId) => {
	return sortBackupsNewestFirst$1($app.findRecordsByFilter("instance_backups", "instance = {:instance} && kind = \"scheduled\" && status = \"ready\"", "", 500, 0, { instance: instanceId }).filter((record) => !!record));
};
const backupTimestampMs = (backup) => {
	const fromFilename = timestampFromBackupFilename(backup.getString("filename"));
	if (fromFilename) return fromFilename;
	const raw = backup.getString("created") || backup.getString("updated");
	const timestamp = Date.parse(raw);
	return Number.isFinite(timestamp) ? timestamp : 0;
};
const shouldRetainBackupCopy = (backup, index, count, days) => {
	if (!(count <= 0 || index < count)) return false;
	if (days <= 0) return true;
	const timestamp = backupTimestampMs(backup);
	if (!timestamp) return true;
	return Date.now() - timestamp <= days * 24 * 60 * 60 * 1e3;
};
const removeLocalBackupFile = (backup) => {
	const filename = backup.getString("filename");
	if (!filename) return;
	assertSafeBackupFilename(filename);
	try {
		$os.remove(backupPath(backup.getString("instance"), filename));
	} catch {}
};
const localBackupExists = (backup) => {
	const filename = backup.getString("filename");
	if (!filename) return false;
	try {
		assertSafeBackupFilename(filename);
		return pathExists$2(backupPath(backup.getString("instance"), filename));
	} catch {
		return false;
	}
};
const applyScheduledBackupRetention = (instance, policy) => {
	const localEnabled = policy.getBool("localEnabled");
	const remoteEnabled = policy.getBool("remoteEnabled");
	const localRetentionCount = Number(policy.get("localRetentionCount") || 0);
	const localRetentionDays = Number(policy.get("localRetentionDays") || 0);
	const remoteRetentionCount = Number(policy.get("remoteRetentionCount") || 0);
	const remoteRetentionDays = Number(policy.get("remoteRetentionDays") || 0);
	scheduledBackupsForInstance(instance.id).forEach((backup, index) => {
		const keepLocal = localEnabled && shouldRetainBackupCopy(backup, index, localRetentionCount, localRetentionDays);
		const keepRemote = remoteEnabled && !!backup.getString("remoteKey") && shouldRetainBackupCopy(backup, index, remoteRetentionCount, remoteRetentionDays);
		if (!keepLocal && localBackupExists(backup)) removeLocalBackupFile(backup);
		if (!keepRemote && backup.getString("remoteKey")) {
			try {
				deleteBackupFromS3(backup.getString("remoteKey"));
			} catch {}
			backup.set("remoteKey", "");
		}
		const hasLocal = localBackupExists(backup);
		const hasRemote = !!backup.getString("remoteKey");
		if (!hasLocal && !hasRemote) {
			try {
				$app.delete(backup);
			} catch {}
			return;
		}
		$app.save(backup);
	});
};
const hasRunningBackupOperation = (instanceId) => {
	try {
		return !!$app.findFirstRecordByFilter("instance_backups", "instance = {:instance} && status = \"running\"", { instance: instanceId });
	} catch {
		return false;
	}
};
const runScheduledBackupPolicy = (policyId, trigger) => {
	if (runningBackupPolicyIds.has(policyId)) return null;
	runningBackupPolicyIds.add(policyId);
	const startedAt = /* @__PURE__ */ new Date();
	const startedAtIso = startedAt.toISOString();
	try {
		const policy = $app.findRecordById("instance_backup_policies", policyId);
		if (!policy || !policy.getBool("enabled")) return null;
		const instance = findInstance$2(policy.getString("instance"));
		const user = $app.findRecordById("users", policy.getString("user") || instance.getString("uid"));
		if (!user) throw new Error("Utilisateur proprietaire introuvable.");
		setPolicyRunState(policy, "running", {
			lastRunAt: startedAtIso,
			lastError: "",
			lastDurationSeconds: 0
		});
		if (hasRunningBackupOperation(instance.id)) {
			setPolicyRunState(policy, "skipped", {
				lastError: "Operation de sauvegarde deja en cours.",
				lastDurationSeconds: Math.max(0, Math.round((Date.now() - startedAt.getTime()) / 1e3))
			});
			return null;
		}
		if (activeBehaviorFor(policy.getString("activeBehavior")) === "skip-active" && instance.getBool("power")) {
			setPolicyRunState(policy, "skipped", {
				lastError: "Instance active: sauvegarde ignoree selon la politique.",
				lastDurationSeconds: Math.max(0, Math.round((Date.now() - startedAt.getTime()) / 1e3))
			});
			return null;
		}
		if (policy.getBool("remoteEnabled") && !s3BackupsAvailable()) throw new Error("S3/R2 n'est plus configure sur ce serveur.");
		const backup = createBackupForInstance(instance, user, "scheduled", true, false, {
			uploadRemote: policy.getBool("remoteEnabled"),
			localEnabled: policy.getBool("localEnabled"),
			policyId: policy.id
		});
		const manifest = backupManifestObject(backup);
		backup.set("manifest", {
			...manifest,
			scheduledPolicy: {
				id: policy.id,
				trigger,
				localEnabled: policy.getBool("localEnabled"),
				remoteEnabled: policy.getBool("remoteEnabled"),
				cron: policy.getString("cron")
			}
		});
		$app.save(backup);
		applyScheduledBackupRetention(instance, policy);
		setPolicyRunState(policy, "ready", {
			lastSuccessAt: (/* @__PURE__ */ new Date()).toISOString(),
			lastBackup: backup.id,
			lastError: "",
			lastDurationSeconds: Math.max(0, Math.round((Date.now() - startedAt.getTime()) / 1e3))
		});
		return backup;
	} catch (error) {
		try {
			const policy = $app.findRecordById("instance_backup_policies", policyId);
			if (policy) setPolicyRunState(policy, "failed", {
				lastError: errorMessage(error),
				lastDurationSeconds: Math.max(0, Math.round((Date.now() - startedAt.getTime()) / 1e3))
			});
		} catch {}
		throw error;
	} finally {
		runningBackupPolicyIds.delete(policyId);
	}
};
const refreshImportedBackupSizeMetadata = (backup) => {
	if (backup.getString("kind") !== "import") return backup;
	if (backup.getString("status") !== "ready") return backup;
	let manifest = backupManifestObject(backup);
	if (manifest.sourceSizeComputedAt) return backup;
	const filename = backup.getString("filename");
	if (!filename) return backup;
	try {
		assertSafeBackupFilename(filename);
		const localPath = backupPath(backup.getString("instance"), filename);
		if (!pathExists$2(localPath)) return backup;
		const compressedBytes = fileSize(localPath);
		const sourceBytes = archiveSourceSizeBytes(localPath, filename) || compressedBytes;
		const record = latestBackupRecord(backup);
		manifest = backupManifestObject(record);
		if (manifest.sourceSizeComputedAt) return record;
		record.set("sizeBytes", sourceBytes);
		record.set("compressedBytes", compressedBytes);
		record.set("manifest", {
			...manifest,
			sourceSizeBytes: sourceBytes,
			compressedSizeBytes: compressedBytes,
			sourceSizeComputedAt: (/* @__PURE__ */ new Date()).toISOString()
		});
		$app.save(record);
		return record;
	} catch {}
	return backup;
};
const createImportedBackupFromImporter = (instance, authRecord, importer) => {
	assertBackupImportAllowed(authRecord);
	assertNoRunningOperation(instance.id);
	const backup = createBackupRecord(instance, authRecord, "import");
	try {
		const imported = importer();
		if (!imported) throw new BadRequestError("Archive manquante. Envoyez un fichier archive ou renseignez un chemin serveur.");
		const compressedBytes = fileSize(imported.localPath);
		const entries = listArchiveEntries(imported.localPath, imported.filename);
		const sourceBytes = archiveSourceSizeBytes(imported.localPath, imported.filename) || compressedBytes;
		markBackupReady(backup, {
			filename: imported.filename,
			localPath: imported.localPath,
			sizeBytes: sourceBytes,
			compressedBytes,
			checksum: sha256(imported.localPath),
			manifest: {
				format: BACKUP_FORMAT,
				imported: true,
				originalFilename: imported.originalFilename,
				sourceModifiedAt: imported.sourceModifiedAt,
				importedAt: (/* @__PURE__ */ new Date()).toISOString(),
				createdAt: (/* @__PURE__ */ new Date()).toISOString(),
				included: archiveIncludedDirs(entries),
				sourceSizeBytes: sourceBytes,
				compressedSizeBytes: compressedBytes,
				sourceSizeComputedAt: (/* @__PURE__ */ new Date()).toISOString()
			}
		});
		return backup;
	} catch (error) {
		markBackupFailed(backup, error);
		throw error;
	}
};
const createImportedBackup = (instance, authRecord, e) => {
	return createImportedBackupFromImporter(instance, authRecord, () => {
		const serverPath = readServerPathInput(e);
		const uploaded = !serverPath ? e.findUploadedFiles("archive").filter((file) => !!file)[0] : null;
		return serverPath ? importBackupFromServerPath(instance, authRecord, serverPath) : uploaded ? importBackupFromUpload(instance, uploaded, readArchiveLastModifiedInput(e)) : null;
	});
};
const createImportedBackupFromServerArchive = (instance, authRecord, serverPath, options = {}) => {
	return createImportedBackupFromImporter(instance, authRecord, () => importBackupFromServerPath(instance, authRecord, serverPath, options));
};
const readChunkStartInput = (e) => {
	let data = new DynamicModel({
		filename: "",
		size: 0,
		chunkSize: 0,
		lastModified: 0
	});
	e.bindBody(data);
	return JSON.parse(JSON.stringify(data));
};
const normalizeChunkSize = (requested) => {
	if (!Number.isFinite(requested) || requested <= 0) return DEFAULT_IMPORT_CHUNK_SIZE_BYTES;
	return Math.max(MIN_IMPORT_CHUNK_SIZE_BYTES, Math.min(Math.floor(requested), DEFAULT_IMPORT_CHUNK_SIZE_BYTES));
};
const startChunkSession = (instance, authRecord, e) => {
	assertBackupImportAllowed(authRecord);
	assertNoRunningOperation(instance.id);
	const input = readChunkStartInput(e);
	const filename = `${input.filename || ""}`.trim() || "archive.zip";
	extensionForImport(filename);
	const size = parsePositiveInteger(input.size, "Taille du fichier");
	const chunkSize = normalizeChunkSize(Number(input.chunkSize || DEFAULT_IMPORT_CHUNK_SIZE_BYTES));
	const totalChunks = Math.ceil(size / chunkSize);
	if (totalChunks <= 0 || totalChunks > MAX_IMPORT_CHUNKS) throw new BadRequestError("Nombre de morceaux invalide.");
	const uploadId = $security.randomStringWithAlphabet(24, "abcdefghijklmnopqrstuvwxyz0123456789");
	const session = {
		instanceId: instance.id,
		userId: authRecord.id,
		filename,
		sourceModifiedAt: isoFromEpochMillis(input.lastModified),
		size,
		chunkSize,
		totalChunks,
		createdAt: (/* @__PURE__ */ new Date()).toISOString()
	};
	const sessionDir = chunkSessionDir(instance.id, uploadId);
	$os.mkdirAll(chunkPartsDir(instance.id, uploadId), PRIVATE_DIR_MODE);
	$os.writeFile(chunkMetaPath(instance.id, uploadId), JSON.stringify(session, null, 2), PRIVATE_FILE_MODE);
	return {
		uploadId,
		session,
		sessionDir
	};
};
const readChunkSession = (instanceId, uploadId) => {
	assertSafeInstanceId$4(instanceId);
	assertSafeUploadId(uploadId);
	try {
		const raw = toString($os.readFile(chunkMetaPath(instanceId, uploadId)));
		const session = JSON.parse(raw);
		if (session.instanceId !== instanceId) throw new Error("Instance mismatch");
		extensionForImport(session.filename);
		parsePositiveInteger(session.size, "Taille du fichier");
		parsePositiveInteger(session.chunkSize, "Taille de morceau");
		parsePositiveInteger(session.totalChunks, "Nombre de morceaux");
		session.sourceModifiedAt = `${session.sourceModifiedAt || ""}`;
		return session;
	} catch {
		throw new BadRequestError("Session d'upload introuvable ou invalide.");
	}
};
const assertChunkSessionOwner = (session, authRecord) => {
	if (session.userId !== authRecord.id) throw new BadRequestError("Session d'upload non autorisee.");
};
const expectedChunkBytes = (session, index) => {
	if (index === session.totalChunks - 1) return session.size - session.chunkSize * (session.totalChunks - 1);
	return session.chunkSize;
};
const uploadedChunkCount = (instanceId, uploadId, totalChunks) => {
	let count = 0;
	for (let index = 0; index < totalChunks; index++) if (pathExists$2(`${chunkPartsDir(instanceId, uploadId)}/${chunkPartFilename(index)}`)) count++;
	return count;
};
const storeChunk = (instance, authRecord, uploadId, e) => {
	const session = readChunkSession(instance.id, uploadId);
	assertChunkSessionOwner(session, authRecord);
	assertBackupImportAllowed(authRecord);
	const index = parseNonNegativeInteger(e.request.formValue("index"), "Index de morceau");
	if (index >= session.totalChunks) throw new BadRequestError("Index de morceau hors limite.");
	const uploaded = e.findUploadedFiles("chunk").filter((file) => !!file)[0];
	if (!uploaded) throw new BadRequestError("Morceau manquant.");
	const partsDir = chunkPartsDir(instance.id, uploadId);
	const partFilename = chunkPartFilename(index);
	const partPath = `${partsDir}/${partFilename}`;
	$os.mkdirAll(partsDir, PRIVATE_DIR_MODE);
	try {
		$os.remove(partPath);
	} catch {}
	const fs = $filesystem.local(partsDir);
	try {
		fs.uploadFile(uploaded, partFilename);
	} finally {
		fs.close();
	}
	const uploadedBytes = fileSize(partPath);
	if (uploadedBytes !== expectedChunkBytes(session, index)) {
		try {
			$os.remove(partPath);
		} catch {}
		throw new BadRequestError("Taille de morceau invalide.");
	}
	return {
		index,
		uploadedBytes,
		uploadedChunks: uploadedChunkCount(instance.id, uploadId, session.totalChunks),
		totalChunks: session.totalChunks
	};
};
const assertAllChunksPresent = (instanceId, uploadId, session) => {
	let totalBytes = 0;
	for (let index = 0; index < session.totalChunks; index++) {
		const partPath = `${chunkPartsDir(instanceId, uploadId)}/${chunkPartFilename(index)}`;
		if (!pathExists$2(partPath)) throw new BadRequestError(`Morceau ${index + 1}/${session.totalChunks} manquant.`);
		const partBytes = fileSize(partPath);
		if (partBytes !== expectedChunkBytes(session, index)) throw new BadRequestError(`Morceau ${index + 1}/${session.totalChunks} invalide.`);
		totalBytes += partBytes;
	}
	if (totalBytes !== session.size) throw new BadRequestError("Taille totale assemblee invalide.");
};
const assembleChunkSessionArchive = (instance, uploadId, session) => {
	assertAllChunksPresent(instance.id, uploadId, session);
	const filename = createImportBackupFilename(instance, session.filename);
	const dir = assembledImportDir(instance.id);
	const finalPath = `${dir}/${uploadId}-${filename}`;
	const tmpPath = `${finalPath}.tmp`;
	assertSafeBackupFilename(filename);
	$os.mkdirAll(dir, PRIVATE_DIR_MODE);
	$os.removeAll(tmpPath);
	$os.removeAll(finalPath);
	try {
		runCommand$1("sh", "-c", "set -e; : > \"$3\"; i=0; while [ \"$i\" -lt \"$2\" ]; do part=$(printf \"%s/%08d.part\" \"$1\" \"$i\"); cat \"$part\" >> \"$3\"; i=$((i + 1)); done", "sh", chunkPartsDir(instance.id, uploadId), `${session.totalChunks}`, tmpPath);
		if (fileSize(tmpPath) !== session.size) throw new BadRequestError("Archive assemblee invalide.");
		$os.rename(tmpPath, finalPath);
		return finalPath;
	} catch (error) {
		try {
			$os.remove(tmpPath);
		} catch {}
		throw error;
	}
};
const completeChunkSession = (instance, authRecord, uploadId) => {
	const session = readChunkSession(instance.id, uploadId);
	assertChunkSessionOwner(session, authRecord);
	assertBackupImportAllowed(authRecord);
	let assembledPath = "";
	try {
		assembledPath = assembleChunkSessionArchive(instance, uploadId, session);
		return createImportedBackupFromServerArchive(instance, authRecord, assembledPath, {
			originalFilename: session.filename,
			sourceModifiedAt: session.sourceModifiedAt
		});
	} finally {
		if (assembledPath) try {
			$os.remove(assembledPath);
		} catch {}
		try {
			$os.removeAll(chunkSessionDir(instance.id, uploadId));
		} catch {}
	}
};
const cancelChunkSession = (instance, authRecord, uploadId) => {
	assertChunkSessionOwner(readChunkSession(instance.id, uploadId), authRecord);
	assertBackupImportAllowed(authRecord);
	$os.removeAll(chunkSessionDir(instance.id, uploadId));
};
const readBackupCreateName = (e) => {
	let data = new DynamicModel({ name: "" });
	try {
		e.bindBody(data);
		data = JSON.parse(JSON.stringify(data));
	} catch {
		data = {};
	}
	return normalizeBackupName(data.name);
};
const HandleInstanceBackupCreate = (e) => {
	const log = mkLog("POST:instance:backup");
	const authRecord = requireAuthRecord$2(e.auth);
	const instance = findInstance$2(pathValue$2(e, "id"));
	assertInstanceAccess$2(instance, authRecord);
	const backup = createBackupForInstance(instance, authRecord, "manual", true, false, { name: readBackupCreateName(e) });
	log(`created ${backup.id} for ${instance.id}`);
	return e.json(200, { backup: serializeInstanceBackup(backup) });
};
const HandleInstanceBackupImport = (e) => {
	const log = mkLog("POST:instance:backup:import");
	const authRecord = requireAuthRecord$2(e.auth);
	const instance = findInstance$2(pathValue$2(e, "id"));
	assertInstanceAccess$2(instance, authRecord);
	const backup = createImportedBackup(instance, authRecord, e);
	log(`imported ${backup.id} for ${instance.id}`);
	return e.json(200, { backup: serializeInstanceBackup(backup) });
};
const HandleInstanceBackupChunkedStart = (e) => {
	const authRecord = requireAuthRecord$2(e.auth);
	const instance = findInstance$2(pathValue$2(e, "id"));
	assertInstanceAccess$2(instance, authRecord);
	const { uploadId, session } = startChunkSession(instance, authRecord, e);
	return e.json(200, {
		uploadId,
		chunkSize: session.chunkSize,
		totalChunks: session.totalChunks
	});
};
const HandleInstanceBackupChunkedUpload = (e) => {
	const authRecord = requireAuthRecord$2(e.auth);
	const instance = findInstance$2(pathValue$2(e, "id"));
	assertInstanceAccess$2(instance, authRecord);
	const result = storeChunk(instance, authRecord, pathValue$2(e, "uploadId"), e);
	return e.json(200, result);
};
const HandleInstanceBackupChunkedComplete = (e) => {
	const log = mkLog("POST:instance:backup:import:chunked:complete");
	const authRecord = requireAuthRecord$2(e.auth);
	const instance = findInstance$2(pathValue$2(e, "id"));
	assertInstanceAccess$2(instance, authRecord);
	const backup = completeChunkSession(instance, authRecord, pathValue$2(e, "uploadId"));
	log(`imported ${backup.id} for ${instance.id} from chunked upload`);
	return e.json(200, { backup: serializeInstanceBackup(backup) });
};
const HandleInstanceBackupChunkedCancel = (e) => {
	const authRecord = requireAuthRecord$2(e.auth);
	const instance = findInstance$2(pathValue$2(e, "id"));
	assertInstanceAccess$2(instance, authRecord);
	cancelChunkSession(instance, authRecord, pathValue$2(e, "uploadId"));
	return e.json(200, { status: "ok" });
};
const HandleInstanceBackupsList = (e) => {
	const authRecord = requireAuthRecord$2(e.auth);
	const instance = findInstance$2(pathValue$2(e, "id"));
	assertInstanceAccess$2(instance, authRecord);
	const backups = findInstanceBackups$1(instance.id).map(refreshImportedBackupSizeMetadata).map(serializeInstanceBackup);
	const activeRestoreIds = backups.filter((backup) => backup.restoreState === "running").map((backup) => backup.id);
	return e.json(200, {
		backups,
		activeRestoreIds
	});
};
const HandleInstanceBackupPolicyGet = (e) => {
	const authRecord = requireAuthRecord$2(e.auth);
	const instance = findInstance$2(pathValue$2(e, "id"));
	assertInstanceAccess$2(instance, authRecord);
	const policy = getOrCreateBackupPolicy(instance);
	return e.json(200, {
		policy: serializeBackupPolicy(policy),
		capabilities: backupPolicyCapabilities()
	});
};
const HandleInstanceBackupPolicyUpdate = (e) => {
	const authRecord = requireAuthRecord$2(e.auth);
	const instance = findInstance$2(pathValue$2(e, "id"));
	assertInstanceAccess$2(instance, authRecord);
	const policy = getOrCreateBackupPolicy(instance);
	applyBackupPolicyInput(policy, instance, readBackupPolicyInput(e));
	$app.save(policy);
	registerBackupPolicyCron(policy);
	return e.json(200, {
		policy: serializeBackupPolicy(policy),
		capabilities: backupPolicyCapabilities()
	});
};
const ReconcileBackupPolicyCrons = () => {
	registerAllBackupPolicyCrons();
};
const HandleInstanceBackupPolicyCronDispatcher = () => {
	runBackupPolicyCronDispatcher();
};
const HandleInstanceBackupPolicyRun = (e) => {
	const log = mkLog("POST:instance:backup-policy:run");
	const authRecord = requireAuthRecord$2(e.auth);
	const instance = findInstance$2(pathValue$2(e, "id"));
	assertInstanceAccess$2(instance, authRecord);
	const policy = getOrCreateBackupPolicy(instance);
	if (!policy.getBool("enabled")) throw new BadRequestError("La sauvegarde automatique n'est pas activee.");
	const backup = runScheduledBackupPolicy(policy.id, "manual");
	log(`manual scheduled run ${policy.id} for ${instance.id}`);
	return e.json(200, {
		policy: serializeBackupPolicy($app.findRecordById("instance_backup_policies", policy.id)),
		backup: backup ? serializeInstanceBackup(backup) : null
	});
};
const HandleInstanceLitestreamPolicyGet = (e) => {
	const authRecord = requireAuthRecord$2(e.auth);
	const instance = findInstance$2(pathValue$2(e, "id"));
	assertInstanceAccess$2(instance, authRecord);
	const policy = refreshLitestreamPolicyState(getOrCreateLitestreamPolicy(instance));
	return e.json(200, {
		policy: serializeLitestreamPolicy(policy),
		capabilities: litestreamCapabilities()
	});
};
const HandleInstanceLitestreamPolicyUpdate = (e) => {
	const authRecord = requireAuthRecord$2(e.auth);
	const instance = findInstance$2(pathValue$2(e, "id"));
	assertInstanceAccess$2(instance, authRecord);
	const policy = getOrCreateLitestreamPolicy(instance);
	applyLitestreamPolicyInput(policy, instance, readLitestreamPolicyInput(e));
	$app.save(policy);
	try {
		reconcileLitestreamService();
	} catch (error) {
		const saved = $app.findRecordById("instance_litestream_replicas", policy.id);
		if (saved) updateLitestreamPolicyState(saved, saved.getBool("enabled") ? "failed" : "disabled", { lastError: errorMessage(error) });
		throw new BadRequestError(errorMessage(error));
	}
	const refreshed = refreshLitestreamPolicyState($app.findRecordById("instance_litestream_replicas", policy.id));
	return e.json(200, {
		policy: serializeLitestreamPolicy(refreshed),
		capabilities: litestreamCapabilities()
	});
};
const HandleInstanceBackupPoliciesBootstrap = () => {
	registerAllBackupPolicyCrons();
};
const HandleInstanceLitestreamBootstrap = () => {
	try {
		reconcileLitestreamService();
	} catch (error) {
		mkLog("bootstrap:litestream")(errorMessage(error));
	}
};
const HandleInstanceBackupDownload = (e) => {
	const authRecord = requireAuthRecord$2(e.auth);
	const instance = findInstance$2(pathValue$2(e, "id"));
	assertInstanceAccess$2(instance, authRecord);
	const backup = getBackupRecord(instance, pathValue$2(e, "backupId"));
	if (backup.getString("status") !== "ready") throw new BadRequestError("Cette sauvegarde n'est pas prete.");
	const localPath = ensureLocalArchive(instance, backup);
	const filename = backup.getString("filename");
	e.response.header().set("Content-Disposition", `attachment; filename="${filename}"`);
	e.response.header().set("Content-Length", `${fileSize(localPath)}`);
	return e.fileFS($os.dirFS(backupDir(instance.id)), filename);
};
const HandleInstanceBackupDelete = (e) => {
	const authRecord = requireAuthRecord$2(e.auth);
	const instance = findInstance$2(pathValue$2(e, "id"));
	assertInstanceAccess$2(instance, authRecord);
	const backup = getBackupRecord(instance, pathValue$2(e, "backupId"));
	const filename = backup.getString("filename");
	if (filename) {
		assertSafeBackupFilename(filename);
		try {
			$os.remove(backupPath(instance.id, filename));
		} catch {}
	}
	try {
		deleteBackupFromS3(backup.getString("remoteKey"));
	} catch {}
	$app.delete(backup);
	return e.json(200, { status: "ok" });
};
const HandleInstanceBackupRestore = (e) => {
	const log = mkLog("POST:instance:backup:restore");
	const authRecord = requireAuthRecord$2(e.auth);
	const source = findInstance$2(pathValue$2(e, "id"));
	assertInstanceAccess$2(source, authRecord);
	const backup = getBackupRecord(source, pathValue$2(e, "backupId"));
	if (backup.getString("status") !== "ready") throw new BadRequestError("Cette sauvegarde n'est pas prete.");
	if (backup.getString("restoreState") === "running") throw new BadRequestError("Cette sauvegarde est deja en cours de restauration.");
	const { targetInstanceId } = readRestoreInput(e, source);
	const target = targetInstanceId === source.id ? source : findInstance$2(targetInstanceId);
	assertInstanceAccess$2(target, authRecord);
	assertNoRunningOperation(source.id);
	if (target.id !== source.id) assertNoRunningOperation(target.id);
	const restoreTarget = {
		mode: target.id === source.id ? "in-place" : "existing-instance",
		targetInstanceId: target.id,
		targetSubdomain: target.getString("subdomain")
	};
	let power = { shouldRestart: false };
	try {
		updateRestoreOperation(backup, "stopping", {
			...restoreTarget,
			label: target.getBool("power") ? "Arrêt de l'instance cible avant restauration" : "Vérification de l'instance cible",
			percent: 4,
			sourceSizeBytes: Number(backup.get("sizeBytes") || 0),
			compressedBytes: Number(backup.get("compressedBytes") || 0)
		});
		power = stopForFilesystemOperation(target);
		updateRestoreOperation(backup, "safety-backup", {
			...restoreTarget,
			label: "Sauvegarde de sécurité de l'instance cible",
			percent: 8,
			sourceSizeBytes: Number(backup.get("sizeBytes") || 0),
			compressedBytes: Number(backup.get("compressedBytes") || 0)
		});
		createBackupForInstance(findInstance$2(target.id), authRecord, "pre-restore", false, true);
		restoreArchive(findInstance$2(target.id), backup, source, {
			...restoreTarget,
			markReady: !power.shouldRestart
		});
		if (power.shouldRestart) {
			updateRestoreOperation(backup, "restarting", {
				...restoreTarget,
				label: "Redémarrage de l'instance cible",
				percent: 96,
				sourceSizeBytes: Number(backup.get("sizeBytes") || 0),
				compressedBytes: Number(backup.get("compressedBytes") || 0)
			});
			restartIfNeeded(target.id, power);
			updateRestoreOperation(backup, "ready", {
				...restoreTarget,
				label: "Restauration terminée",
				percent: 100,
				sourceSizeBytes: Number(backup.get("sizeBytes") || 0),
				compressedBytes: Number(backup.get("compressedBytes") || 0)
			});
		}
		log(`restored ${backup.id} from ${source.id} into ${target.id}`);
	} catch (error) {
		updateRestoreOperation(backup, "failed", {
			...restoreTarget,
			label: "Restauration échouée",
			percent: 100,
			sourceSizeBytes: Number(backup.get("sizeBytes") || 0),
			compressedBytes: Number(backup.get("compressedBytes") || 0),
			error: errorMessage(error)
		});
		throw error;
	}
	return e.json(200, {
		status: "ok",
		targetInstanceId: target.id
	});
};
const HandleInstanceBackupRestoreNew = (e) => {
	const log = mkLog("POST:instance:backup:restore:new");
	const authRecord = requireAuthRecord$2(e.auth);
	const instance = findInstance$2(pathValue$2(e, "id"));
	assertInstanceAccess$2(instance, authRecord);
	const backup = getBackupRecord(instance, pathValue$2(e, "backupId"));
	if (backup.getString("status") !== "ready") throw new BadRequestError("Cette sauvegarde n'est pas prete.");
	const target = createRestoredInstanceFromBackup(instance, authRecord, backup, e);
	log(`restored ${backup.id} from ${instance.id} into new instance ${target.id}`);
	return e.json(200, { instance: target });
};

//#endregion
//#region src/lib/util/versions.ts
const POCKETBASE_VERSIONS_SETTING = "pocketbase_versions";
const parsePocketbaseVersionsValue = (raw) => {
	if (!raw) return null;
	if (typeof raw === "string") try {
		return JSON.parse(raw);
	} catch {
		return null;
	}
	return raw;
};
const readPocketbaseVersions = () => {
	try {
		const value = parsePocketbaseVersionsValue($app.findFirstRecordByData("settings", "name", POCKETBASE_VERSIONS_SETTING).getString("value"));
		if (!value?.versions?.length) return [];
		return value.versions;
	} catch {
		return [];
	}
};
/** Minor wildcard versions (e.g. `0.22.*`) from mothership settings */
const listVersions = () => readPocketbaseVersions().map((entry) => entry.range);

//#endregion
//#region src/lib/handlers/instance/api/HandleInstanceCreate.ts
const HandleInstanceCreate = (e) => {
	const log = mkLog(`POST:instance`);
	const authRecord = e.auth;
	log(`authRecord`, JSON.stringify(authRecord));
	if (!authRecord) throw new Error(`Session utilisateur attendue`);
	log(`TOP OF POST`);
	let data = new DynamicModel({
		subdomain: "",
		version: listVersions()[0]
	});
	log(`before bind`);
	e.bindBody(data);
	log(`after bind`);
	data = JSON.parse(JSON.stringify(data));
	const { subdomain, version } = data;
	const settings = readOperatorSettings();
	log(`vars`, JSON.stringify({ subdomain }));
	if (!subdomain) throw new BadRequestError(`Le sous-domaine est obligatoire pour créer une instance.`);
	const collection = $app.findCollectionByNameOrId("instances");
	const record = new Record(collection);
	record.set("uid", authRecord.id);
	record.set("subdomain", subdomain);
	record.set("power", settings.defaultInstancePower);
	record.set("status", "idle");
	record.set("version", version);
	record.set("dev", settings.defaultInstanceDevMode);
	record.set("syncAdmin", settings.defaultSyncAdmin);
	record.set("autoVacuum", settings.defaultAutoVacuum);
	$app.save(record);
	return e.json(200, { instance: record });
};

//#endregion
//#region src/lib/handlers/operatorAdmin/diskCleanup.ts
const SAFE_INSTANCE_ID = /^[a-z0-9]+$/;
const dataRoot$2 = () => {
	const envRoot = $os.getenv("DATA_ROOT");
	if (envRoot) return envRoot;
	const appDataDir = `${$app.dataDir()}`;
	const inferred = appDataDir.replace(/\/mothership\/pb_data\/?$/, "");
	if (inferred !== appDataDir) return inferred;
	throw new Error("Impossible de trouver le dossier de donnees des instances.");
};
const instanceRoot$2 = (id) => `${dataRoot$2()}/instances/${id}`;
const backupRoot = () => $os.getenv("INSTANCE_BACKUP_ROOT") || `${dataRoot$2()}/backups/instances`;
const importRoot = () => $os.getenv("INSTANCE_IMPORT_ROOT") || `${dataRoot$2()}/imports`;
const pathExists$1 = (path) => {
	try {
		$os.stat(path);
		return true;
	} catch {
		return false;
	}
};
const runCommand = (name, ...args) => toString($os.cmd(name, ...args).combinedOutput()).trim();
const basename = (path) => path.replace(/\/+$/g, "").split("/").pop() || "";
const assertSafeInstanceId$3 = (id) => {
	if (!SAFE_INSTANCE_ID.test(id)) throw new BadRequestError("Identifiant d'instance invalide.");
};
const directorySizeBytes = (path) => {
	if (!pathExists$1(path)) return 0;
	try {
		const output = runCommand("du", "-sb", path);
		const value = Number(output.split(/\s+/)[0] || 0);
		return Number.isFinite(value) ? Math.max(0, value) : 0;
	} catch {
		return 0;
	}
};
const listChildDirs = (root) => {
	if (!pathExists$1(root)) return [];
	try {
		return runCommand("find", root, "-mindepth", "1", "-maxdepth", "1", "-type", "d", "-print").split("\n").map((line) => line.trim()).filter(Boolean);
	} catch {
		return [];
	}
};
const dockerContainerNames = () => {
	try {
		return new Set(runCommand("docker", "ps", "-a", "--format", "{{.Names}}").split("\n").map((line) => line.trim()).filter(Boolean));
	} catch {
		return /* @__PURE__ */ new Set();
	}
};
const listInstanceRecordIds = () => {
	const ids = /* @__PURE__ */ new Set();
	let offset = 0;
	const pageSize = 500;
	for (;;) {
		const records = $app.findRecordsByFilter("instances", "id != \"\"", "", pageSize, offset);
		for (const record of records) ids.add(record.id);
		if (records.length < pageSize) break;
		offset += pageSize;
	}
	return ids;
};
const removeDirectory = (path) => {
	if (!pathExists$1(path)) return false;
	$os.removeAll(path);
	return true;
};
const scanRoot = (root, kind, recordIds, containers, remove, requirePbData = false) => {
	const entries = [];
	for (const path of listChildDirs(root)) {
		const id = basename(path);
		if (!SAFE_INSTANCE_ID.test(id)) continue;
		if (requirePbData && !pathExists$1(`${path}/pb_data`)) continue;
		const hasRecord = recordIds.has(id);
		const hasContainer = containers.has(id);
		if (hasRecord || hasContainer) continue;
		const entry = {
			id,
			kind,
			path,
			sizeBytes: directorySizeBytes(path),
			hasRecord,
			hasContainer,
			removed: false,
			error: ""
		};
		if (remove) try {
			removeDirectory(path);
			entry.removed = true;
		} catch (error) {
			entry.error = error instanceof Error ? error.message : `${error}`;
		}
		entries.push(entry);
	}
	return entries;
};
const scanOrphanInstanceStorage = (remove = false) => {
	const recordIds = listInstanceRecordIds();
	const containers = dockerContainerNames();
	const entries = [
		...scanRoot(`${dataRoot$2()}/instances`, "instance-data", recordIds, containers, remove, true),
		...scanRoot(backupRoot(), "backup-data", recordIds, containers, remove),
		...scanRoot(`${importRoot()}/assembled`, "import-data", recordIds, containers, remove),
		...scanRoot(`${importRoot()}/.chunked`, "import-data", recordIds, containers, remove)
	];
	return {
		scannedAt: (/* @__PURE__ */ new Date()).toISOString(),
		dataRoot: dataRoot$2(),
		orphanCount: entries.length,
		totalBytes: entries.reduce((total, entry) => total + entry.sizeBytes, 0),
		removedCount: entries.filter((entry) => entry.removed).length,
		freedBytes: entries.filter((entry) => entry.removed).reduce((total, entry) => total + entry.sizeBytes, 0),
		entries
	};
};
const removeInstanceLocalStorage = (id) => {
	assertSafeInstanceId$3(id);
	const targets = [
		instanceRoot$2(id),
		`${backupRoot()}/${id}`,
		`${importRoot()}/assembled/${id}`,
		`${importRoot()}/.chunked/${id}`
	];
	let removedCount = 0;
	let freedBytes = 0;
	const errors = [];
	for (const target of targets) try {
		const bytes = directorySizeBytes(target);
		if (removeDirectory(target)) {
			removedCount++;
			freedBytes += bytes;
		}
	} catch (error) {
		errors.push(error instanceof Error ? error.message : `${error}`);
	}
	return {
		removedCount,
		freedBytes,
		errors
	};
};

//#endregion
//#region src/lib/handlers/instance/api/HandleInstanceDelete.ts
const HandleInstanceDelete = (e) => {
	const log = mkLog(`DELETE:instance`);
	log(`TOP OF DELETE`);
	let data = new DynamicModel({ id: "" });
	e.bindBody(data);
	log(`After bind`);
	data = JSON.parse(JSON.stringify(data));
	const id = e.request.pathValue("id");
	log(`vars`, JSON.stringify({ id }));
	const authRecord = e.auth;
	log(`authRecord`, JSON.stringify(authRecord));
	if (!authRecord) throw new BadRequestError(`Expected authRecord here`);
	const record = $app.findRecordById("instances", id);
	if (!record) throw new BadRequestError(`Instance ${id} introuvable.`);
	if (record.get("uid") !== authRecord.id) throw new BadRequestError(`Not authorized`);
	if (record.getString("status").toLowerCase() !== "idle") throw new BadRequestError(`L'instance doit d'abord être arrêtée.`);
	$app.delete(record);
	const cleanup = removeInstanceLocalStorage(id);
	return e.json(200, {
		status: "ok",
		cleanup
	});
};

//#endregion
//#region src/lib/handlers/instance/api/HandleInstanceDuplicate.ts
const COPY_DIRS = [
	"pb_data",
	"pb_migrations",
	"pb_public",
	"pb_hooks"
];
const dataRoot$1 = () => {
	const envRoot = $os.getenv("DATA_ROOT");
	if (envRoot) return envRoot;
	const appDataDir = `${$app.dataDir()}`;
	const inferred = appDataDir.replace(/\/mothership\/pb_data\/?$/, "");
	if (inferred !== appDataDir) return inferred;
	throw new Error("Impossible de trouver le dossier de donnees des instances.");
};
const assertSafeInstanceId$2 = (id) => {
	if (!id.match(/^[a-z0-9]+$/)) throw new BadRequestError("Identifiant d'instance invalide.");
};
const instanceRoot$1 = (id) => `${dataRoot$1()}/instances/${id}`;
const pathExists = (path) => {
	try {
		$os.stat(path);
		return true;
	} catch {
		return false;
	}
};
const copyDirectory = (source, target) => {
	$os.mkdirAll(target, 493);
	$os.cmd("cp", "-a", `${source}/.`, target).combinedOutput();
};
const copyInstanceFiles = (sourceId, targetId) => {
	assertSafeInstanceId$2(sourceId);
	assertSafeInstanceId$2(targetId);
	const sourceRoot = instanceRoot$1(sourceId);
	const targetRoot = instanceRoot$1(targetId);
	$os.mkdirAll(targetRoot, 493);
	for (const dir of COPY_DIRS) {
		const source = `${sourceRoot}/${dir}`;
		const target = `${targetRoot}/${dir}`;
		$os.removeAll(target);
		if (pathExists(source)) copyDirectory(source, target);
		else $os.mkdirAll(target, 493);
	}
};
const normalizeBaseSubdomain = (subdomain) => {
	const clean = subdomain.toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-").replace(/^-+|-+$/g, "");
	return (clean.match(/^[a-z]/) ? clean : `base-${clean}`).slice(0, 34).replace(/-+$/g, "") || "base";
};
const subdomainExists = (subdomain) => {
	try {
		$app.findFirstRecordByData("instances", "subdomain", subdomain);
		return true;
	} catch {
		return false;
	}
};
const suggestDuplicateSubdomain = (sourceSubdomain) => {
	const base = normalizeBaseSubdomain(sourceSubdomain);
	const fixed = `${base.slice(0, 34).replace(/-+$/g, "")}-copy`;
	if (fixed.match(/^[a-z][a-z0-9-]{2,39}$/) && !subdomainExists(fixed)) return fixed;
	for (let i = 0; i < 25; i++) {
		const suffix = $security.randomStringWithAlphabet(5 + Math.min(i, 4), "abcdefghijklmnopqrstuvwxyz0123456789");
		const candidate = `${base.slice(0, 39 - suffix.length).replace(/-+$/g, "")}-${suffix}`;
		if (candidate.match(/^[a-z][a-z0-9-]{2,39}$/) && !subdomainExists(candidate)) return candidate;
	}
	throw new BadRequestError("Impossible de generer un nom d'instance disponible.");
};
const HandleInstanceDuplicate = (e) => {
	const log = mkLog(`POST:instance:duplicate`);
	const authRecord = e.auth;
	if (!authRecord) throw new BadRequestError(`Session utilisateur attendue`);
	const sourceId = e.request.pathValue("id");
	assertSafeInstanceId$2(sourceId);
	const source = $app.findRecordById("instances", sourceId);
	if (!source) throw new BadRequestError(`Instance ${sourceId} introuvable.`);
	if (source.get("uid") !== authRecord.id && !authRecord.getBool("superAdmin")) throw new BadRequestError(`Non autorise`);
	if (source.getBool("power") || source.getString("status").toLowerCase() !== "idle") throw new BadRequestError("Eteignez l'instance avant de dupliquer sa base.");
	const collection = $app.findCollectionByNameOrId("instances");
	const target = new Record(collection);
	const targetSubdomain = suggestDuplicateSubdomain(source.getString("subdomain"));
	target.set("uid", authRecord.id);
	target.set("subdomain", targetSubdomain);
	target.set("status", "idle");
	target.set("power", false);
	target.set("version", source.getString("version"));
	target.set("dev", source.getBool("dev"));
	target.set("syncAdmin", source.getBool("syncAdmin"));
	target.set("autoVacuum", source.getBool("autoVacuum"));
	target.set("secrets", source.get("secrets"));
	target.set("webhooks", source.get("webhooks"));
	try {
		$app.save(target);
		target.set("dev", source.getBool("dev"));
		target.set("autoVacuum", source.getBool("autoVacuum"));
		$app.save(target);
		copyInstanceFiles(source.id, target.id);
		log(`duplicated ${source.id} to ${target.id}`);
	} catch (error) {
		try {
			if (target.id) $app.delete(target);
		} catch {}
		try {
			if (target.id) $os.removeAll(instanceRoot$1(target.id));
		} catch {}
		throw new ApiError(500, `Impossible de dupliquer la base.`, { error });
	}
	return e.json(200, { instance: target });
};

//#endregion
//#region src/lib/util/mailRecipient.ts
/** Reason a user must not receive platform email, or null if OK to send. */
const mailRecipientSkipReason = (user) => {
	if (!user.getBool("verified")) return "unverified";
	if (user.getBool("unsubscribe")) return "unsubscribed";
	return null;
};
/** Permanent bounce or complaint: stop all future platform email. */
const suppressUserEmail = (user) => {
	user.setVerified(false);
	user.set("unsubscribe", true);
};

//#endregion
//#region src/lib/handlers/instance/api/HandleInstanceOverview.ts
const INSTANCE_RESOURCE_METRICS_COLLECTION = "instance_resource_metrics";
const INSTANCE_METRICS_RETENTION_MS = 10080 * 60 * 1e3;
const INSTANCE_METRICS_QUERY_LIMIT = 12e3;
const historyRanges = {
	"30m": {
		durationMs: 1800 * 1e3,
		bucketMs: 60 * 1e3
	},
	"6h": {
		durationMs: 360 * 60 * 1e3,
		bucketMs: 60 * 1e3
	},
	"24h": {
		durationMs: 1440 * 60 * 1e3,
		bucketMs: 120 * 1e3
	},
	"7d": {
		durationMs: INSTANCE_METRICS_RETENTION_MS,
		bucketMs: 600 * 1e3
	}
};
const formatPocketBaseDate$1 = (timestamp) => new Date(timestamp).toISOString().replace("T", " ");
const assertSafeInstanceId$1 = (id) => {
	if (!id.match(/^[a-z0-9]+$/)) throw new BadRequestError("Identifiant d'instance invalide.");
};
const dataRoot = () => {
	const envRoot = $os.getenv("DATA_ROOT");
	if (envRoot) return envRoot;
	const appDataDir = `${$app.dataDir()}`;
	const inferred = appDataDir.replace(/\/mothership\/pb_data\/?$/, "");
	if (inferred !== appDataDir) return inferred;
	throw new Error("Impossible de trouver le dossier de donnees des instances.");
};
const instanceRoot = (id) => `${dataRoot()}/instances/${id}`;
const requireAuthRecord$1 = (authRecord) => {
	if (!authRecord) throw new BadRequestError("Session utilisateur attendue.");
	return authRecord;
};
const findInstance$1 = (id) => {
	assertSafeInstanceId$1(id);
	const instance = $app.findRecordById("instances", id);
	if (!instance) throw new BadRequestError(`Instance ${id} introuvable.`);
	return instance;
};
const assertInstanceAccess$1 = (instance, authRecord) => {
	if (instance.getString("uid") !== authRecord.id && !authRecord.getBool("superAdmin")) throw new BadRequestError("Non autorise.");
};
const pathValue$1 = (e, name) => {
	if (!e.request) throw new BadRequestError("Requete invalide.");
	return e.request.pathValue(name);
};
const getDirectorySizeBytes = (path) => {
	try {
		const output = toString($os.cmd("du", "-sb", path).combinedOutput()).trim();
		const value = Number(output.split(/\s+/)[0] || 0);
		return Number.isFinite(value) ? value : null;
	} catch {
		return null;
	}
};
const parseDockerPercent = (value) => {
	const normalized = `${value || ""}`.trim().replace("%", "").replace(",", ".");
	const number = Number(normalized);
	return Number.isFinite(number) ? Math.max(0, number) : null;
};
const parsePositiveNumber = (value) => {
	const number = Number(value);
	return Number.isFinite(number) && number > 0 ? number : null;
};
const countCpuSet = (value) => {
	const cpuIds = /* @__PURE__ */ new Set();
	for (const part of `${value || ""}`.split(",")) {
		const normalized = part.trim();
		if (!normalized) continue;
		const range = normalized.match(/^(\d+)-(\d+)$/);
		if (range) {
			const start = Number(range[1]);
			const end = Number(range[2]);
			if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || end < start || end - start > 4096) continue;
			for (let cpu = start; cpu <= end; cpu += 1) cpuIds.add(cpu);
			continue;
		}
		const cpu = Number(normalized);
		if (Number.isSafeInteger(cpu) && cpu >= 0) cpuIds.add(cpu);
	}
	return cpuIds.size > 0 ? cpuIds.size : null;
};
const readDockerHostCpuCores = () => {
	try {
		const dockerCpuCores = parsePositiveNumber(toString($os.cmd("docker", "info", "--format", "{{.NCPU}}").combinedOutput()).trim());
		if (dockerCpuCores !== null) return dockerCpuCores;
	} catch {}
	try {
		return parsePositiveNumber(toString($os.cmd("nproc").combinedOutput()).trim());
	} catch {
		return null;
	}
};
const dockerByteUnits = {
	b: 1,
	kb: 1e3,
	mb: 1e3 ** 2,
	gb: 1e3 ** 3,
	tb: 1e3 ** 4,
	kib: 1024,
	mib: 1024 ** 2,
	gib: 1024 ** 3,
	tib: 1024 ** 4
};
const parseDockerBytes = (value) => {
	const match = `${value || ""}`.trim().replace(",", ".").match(/^([0-9.]+)\s*([a-zA-Z]+)$/);
	if (!match) return null;
	const number = Number(match[1]);
	const factor = dockerByteUnits[match[2].toLowerCase()];
	if (!Number.isFinite(number) || !factor) return null;
	return Math.round(number * factor);
};
const parseDockerBytePair = (value) => {
	const parts = `${value || ""}`.split("/").map((part) => part.trim());
	return [parseDockerBytes(parts[0]), parseDockerBytes(parts[1])];
};
const readDockerStatsByName = (containerNames = []) => {
	const rows = /* @__PURE__ */ new Map();
	try {
		const output = toString($os.cmd("docker", "stats", "--no-stream", "--format", "{{json .}}", ...containerNames).combinedOutput()).trim();
		if (!output) return rows;
		for (const line of output.split("\n")) {
			const trimmed = line.trim();
			if (!trimmed) continue;
			try {
				const row = JSON.parse(trimmed);
				const name = `${row.Name || ""}`;
				if (name) rows.set(name, row);
			} catch {}
		}
	} catch {
		return rows;
	}
	return rows;
};
const readDockerCpuConfigByName = (containerNames) => {
	const rows = /* @__PURE__ */ new Map();
	if (containerNames.length === 0) return rows;
	try {
		const output = toString($os.cmd("docker", "inspect", "--format", "{\"name\":{{json .Name}},\"nanoCpus\":{{json .HostConfig.NanoCpus}},\"cpuQuota\":{{json .HostConfig.CpuQuota}},\"cpuPeriod\":{{json .HostConfig.CpuPeriod}},\"cpusetCpus\":{{json .HostConfig.CpusetCpus}}}", ...containerNames).combinedOutput()).trim();
		if (!output) return rows;
		for (const line of output.split("\n")) try {
			const row = JSON.parse(line.trim());
			const name = `${row.name || ""}`.replace(/^\//, "");
			if (name) rows.set(name, row);
		} catch {}
	} catch {
		return rows;
	}
	return rows;
};
const readDockerMetricsSnapshot = (containerNames = []) => {
	const statsByName = readDockerStatsByName(containerNames);
	return {
		statsByName,
		cpuConfigByName: readDockerCpuConfigByName(Array.from(statsByName.keys())),
		hostCpuCores: readDockerHostCpuCores()
	};
};
const resolveAvailableCpuCores = (config, hostCpuCores) => {
	const limits = [];
	if (hostCpuCores !== null) limits.push(hostCpuCores);
	const nanoCpuLimit = parsePositiveNumber(config?.nanoCpus);
	if (nanoCpuLimit !== null) limits.push(nanoCpuLimit / 1e9);
	const cpuQuota = parsePositiveNumber(config?.cpuQuota);
	const cpuPeriod = parsePositiveNumber(config?.cpuPeriod);
	if (cpuQuota !== null) limits.push(cpuQuota / (cpuPeriod ?? 1e5));
	const cpuSetLimit = countCpuSet(config?.cpusetCpus);
	if (cpuSetLimit !== null) limits.push(cpuSetLimit);
	return limits.length > 0 ? Math.min(...limits) : null;
};
const serializeInstanceRuntimeMetrics = (instance, snapshot) => {
	const row = snapshot.statsByName.get(instance.id);
	const [memoryBytes, memoryLimitBytes] = parseDockerBytePair(row?.MemUsage);
	const [blockReadBytes, blockWriteBytes] = parseDockerBytePair(row?.BlockIO);
	const cpuPercent = parseDockerPercent(row?.CPUPerc);
	const cpuAvailableCores = row ? resolveAvailableCpuCores(snapshot.cpuConfigByName.get(instance.id), snapshot.hostCpuCores) : null;
	return {
		instanceId: instance.id,
		cpuPercent,
		cpuCoresUsed: cpuPercent === null ? null : cpuPercent / 100,
		cpuAvailableCores,
		cpuHostCores: row ? snapshot.hostCpuCores : null,
		cpuCapacityPercent: cpuPercent === null || cpuAvailableCores === null ? null : cpuPercent / cpuAvailableCores,
		memoryBytes,
		memoryLimitBytes,
		memoryPercent: parseDockerPercent(row?.MemPerc),
		diskBytes: null,
		blockReadBytes,
		blockWriteBytes,
		containerName: row?.Name || ""
	};
};
const serializeInstanceResourceMetrics = (instance, snapshot) => ({
	...serializeInstanceRuntimeMetrics(instance, snapshot),
	diskBytes: getDirectorySizeBytes(instanceRoot(instance.id))
});
const findMetricsHistoryRange = (e) => {
	const requested = `${e.request.url.query().get("range") || "30m"}`;
	return requested in historyRanges ? requested : "30m";
};
const serializeStoredMetricPoint = (record) => ({
	timestamp: record.getDateTime("created").unix() * 1e3,
	cpuPercent: record.getFloat("cpuPercent"),
	memoryBytes: record.getInt("memoryBytes"),
	memoryLimitBytes: record.getInt("memoryLimitBytes"),
	memoryPercent: record.getFloat("memoryPercent")
});
const aggregateMetricPoints = (points, bucketMs) => {
	const buckets = /* @__PURE__ */ new Map();
	for (const point of points) {
		if (!Number.isFinite(point.timestamp)) continue;
		const timestamp = Math.floor(point.timestamp / bucketMs) * bucketMs;
		const bucket = buckets.get(timestamp) || {
			count: 0,
			cpuPercent: 0,
			memoryBytes: 0,
			memoryLimitBytes: 0,
			memoryPercent: 0
		};
		bucket.count += 1;
		bucket.cpuPercent += point.cpuPercent;
		bucket.memoryBytes += point.memoryBytes;
		bucket.memoryLimitBytes += point.memoryLimitBytes;
		bucket.memoryPercent += point.memoryPercent;
		buckets.set(timestamp, bucket);
	}
	return Array.from(buckets.entries()).sort(([a], [b]) => a - b).map(([timestamp, bucket]) => ({
		collectedAt: new Date(timestamp).toISOString(),
		cpuPercent: bucket.cpuPercent / bucket.count,
		memoryBytes: Math.round(bucket.memoryBytes / bucket.count),
		memoryLimitBytes: Math.round(bucket.memoryLimitBytes / bucket.count),
		memoryPercent: bucket.memoryPercent / bucket.count
	}));
};
const findAccessibleInstances = (authRecord) => {
	return (authRecord.getBool("superAdmin") ? $app.findRecordsByFilter("instances", "1=1", "subdomain", 500, 0) : $app.findRecordsByFilter("instances", "uid = {:uid}", "subdomain", 500, 0, { uid: authRecord.id })).filter((record) => !!record);
};
const sortBackupsNewestFirst = (backups) => {
	return backups.sort((a, b) => {
		const aValue = a.getString("updated") || a.getString("created") || a.getString("filename") || a.id;
		return (b.getString("updated") || b.getString("created") || b.getString("filename") || b.id).localeCompare(aValue);
	});
};
const findInstanceBackups = (instanceId) => {
	return sortBackupsNewestFirst($app.findRecordsByFilter("instance_backups", "instance = {:instance}", "", 100, 0, { instance: instanceId }).filter((record) => !!record));
};
const HandleInstanceOverview = (e) => {
	const authRecord = requireAuthRecord$1(e.auth);
	const instance = findInstance$1(pathValue$1(e, "id"));
	assertInstanceAccess$1(instance, authRecord);
	const backups = findInstanceBackups(instance.id).map(refreshImportedBackupSizeMetadata).map(serializeInstanceBackup);
	const totalCompressedBytes = backups.reduce((total, backup) => total + backup.compressedBytes, 0);
	const runtime = serializeInstanceResourceMetrics(instance, readDockerMetricsSnapshot([instance.id]));
	return e.json(200, {
		instance,
		backups: {
			count: backups.length,
			readyCount: backups.filter((backup) => backup.status === "ready").length,
			runningCount: backups.filter((backup) => backup.status === "running").length,
			failedCount: backups.filter((backup) => backup.status === "failed").length,
			totalCompressedBytes,
			latest: backups[0] || null
		},
		storage: { instanceBytes: runtime.diskBytes },
		runtime,
		collectedAt: (/* @__PURE__ */ new Date()).toISOString()
	});
};
const HandleInstancesMetrics = (e) => {
	const instances = findAccessibleInstances(requireAuthRecord$1(e.auth));
	const snapshot = readDockerMetricsSnapshot();
	const metrics = {};
	for (const instance of instances) metrics[instance.id] = serializeInstanceResourceMetrics(instance, snapshot);
	return e.json(200, {
		instances: metrics,
		collectedAt: (/* @__PURE__ */ new Date()).toISOString()
	});
};
const HandleInstanceMetrics = (e) => {
	const authRecord = requireAuthRecord$1(e.auth);
	const instance = findInstance$1(pathValue$1(e, "id"));
	assertInstanceAccess$1(instance, authRecord);
	return e.json(200, {
		metric: serializeInstanceRuntimeMetrics(instance, readDockerMetricsSnapshot([instance.id])),
		collectedAt: (/* @__PURE__ */ new Date()).toISOString()
	});
};
const HandleInstanceMetricsHistory = (e) => {
	const authRecord = requireAuthRecord$1(e.auth);
	const instance = findInstance$1(pathValue$1(e, "id"));
	assertInstanceAccess$1(instance, authRecord);
	const range = findMetricsHistoryRange(e);
	const config = historyRanges[range];
	const cutoff = formatPocketBaseDate$1(Date.now() - config.durationMs);
	const records = $app.findRecordsByFilter(INSTANCE_RESOURCE_METRICS_COLLECTION, "instance = {:instance} && created >= {:cutoff}", "created", INSTANCE_METRICS_QUERY_LIMIT, 0, {
		instance: instance.id,
		cutoff
	});
	return e.json(200, {
		range,
		historyEnabled: instance.getBool("metricsHistoryEnabled"),
		bucketSeconds: config.bucketMs / 1e3,
		points: aggregateMetricPoints(records.map(serializeStoredMetricPoint), config.bucketMs),
		collectedAt: (/* @__PURE__ */ new Date()).toISOString()
	});
};
const CollectInstanceResourceMetrics = (providedSnapshot) => {
	const instances = $app.findRecordsByFilter("instances", "metricsHistoryEnabled = true", "", 500, 0);
	if (instances.length === 0) return { saved: 0 };
	const snapshot = providedSnapshot || readDockerMetricsSnapshot();
	const collection = $app.findCollectionByNameOrId(INSTANCE_RESOURCE_METRICS_COLLECTION);
	let saved = 0;
	for (const instance of instances) {
		const metric = serializeInstanceRuntimeMetrics(instance, snapshot);
		if (metric.cpuPercent === null || metric.memoryBytes === null || metric.memoryLimitBytes === null || metric.memoryPercent === null) continue;
		try {
			const record = new Record(collection);
			record.set("instance", instance.id);
			record.set("cpuPercent", metric.cpuPercent);
			record.set("memoryBytes", metric.memoryBytes);
			record.set("memoryLimitBytes", metric.memoryLimitBytes);
			record.set("memoryPercent", metric.memoryPercent);
			$app.save(record);
			saved += 1;
		} catch (error) {
			console.warn(`Impossible d'enregistrer les métriques de l'instance ${instance.id}: ${error}`);
		}
	}
	return { saved };
};
const PurgeExpiredInstanceResourceMetrics = () => {
	const cutoff = formatPocketBaseDate$1(Date.now() - INSTANCE_METRICS_RETENTION_MS);
	let deleted = 0;
	for (;;) {
		const records = $app.findRecordsByFilter(INSTANCE_RESOURCE_METRICS_COLLECTION, "created < {:cutoff}", "created", 1e3, 0, { cutoff });
		if (records.length === 0) break;
		for (const record of records) {
			$app.delete(record);
			deleted += 1;
		}
	}
	return { deleted };
};

//#endregion
//#region src/lib/handlers/instance/api/instanceMonitoring.ts
const MONITORING_DEFAULTS = {
	healthPath: "/api/health",
	healthFailureCount: 3,
	healthRecoveryCount: 2,
	cpuThresholdPercent: 85,
	cpuSustainMinutes: 5,
	memoryThresholdPercent: 85,
	memorySustainMinutes: 5,
	resourceRecoveryCount: 3,
	resourceRecoveryMargin: 5,
	startupGraceMs: 120 * 1e3,
	requestTimeoutSeconds: 5
};
const notificationPayloadText = (value, field) => {
	const text = `${typeof value === "string" ? value : ""}`.trim();
	if (!text) throw new Error(`Payload de notification invalide : champ ${field} absent.`);
	return text;
};
const normalizeMonitoringNotificationPayload = (value) => {
	let parsed = value;
	try {
		if (typeof value === "string") parsed = JSON.parse(value);
		else if (value && typeof value === "object") parsed = JSON.parse(JSON.stringify(value));
	} catch {
		throw new Error("Payload de notification invalide.");
	}
	if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("Payload de notification invalide.");
	const payload = parsed;
	const type = notificationPayloadText(payload.type, "type");
	const phase = notificationPayloadText(payload.phase, "phase");
	if (![
		"health",
		"cpu",
		"memory",
		"backup",
		"test"
	].includes(type)) throw new Error("Payload de notification invalide : type inconnu.");
	if (![
		"opened",
		"resolved",
		"test"
	].includes(phase)) throw new Error("Payload de notification invalide : phase inconnue.");
	return {
		title: notificationPayloadText(payload.title, "title"),
		message: notificationPayloadText(payload.message, "message"),
		instanceId: notificationPayloadText(payload.instanceId, "instanceId"),
		instanceName: notificationPayloadText(payload.instanceName, "instanceName"),
		type,
		phase,
		occurredAt: notificationPayloadText(payload.occurredAt, "occurredAt")
	};
};
const normalizeHealthPath = (value) => {
	const path = `${typeof value === "string" ? value : ""}`.trim();
	if (!path || path.length > 200) throw new Error("Le chemin de santé doit contenir entre 1 et 200 caractères.");
	if (!path.startsWith("/") || path.startsWith("//") || /[\u0000-\u001f\u007f]/.test(path)) throw new Error("Le chemin de santé doit être un chemin relatif commençant par /.");
	if (/^[^?#]*\\/.test(path) || /^\/\w+:\/\//i.test(path)) throw new Error("Le chemin de santé est invalide.");
	return path;
};
const normalizeWebhook = (value) => `${typeof value === "string" ? value : ""}`.trim();
const normalizeDiscordWebhook = (value) => {
	const url = normalizeWebhook(value);
	if (!url) return "";
	if (url.length > 2e3 || !/^https:\/\/(?:discord\.com|discordapp\.com)\/api\/webhooks\/[0-9]+\/[A-Za-z0-9._-]+$/.test(url)) throw new Error("L'URL Discord doit être une URL officielle https://discord.com/api/webhooks/…");
	return url;
};
const normalizeSlackWebhook = (value) => {
	const url = normalizeWebhook(value);
	if (!url) return "";
	if (url.length > 2e3 || !/^https:\/\/hooks\.slack\.com\/services\/[A-Za-z0-9_-]+\/[A-Za-z0-9_-]+\/[A-Za-z0-9_-]+$/.test(url)) throw new Error("L'URL Slack doit être une URL officielle https://hooks.slack.com/services/…");
	return url;
};
const evaluateHealthSignal = (state, healthy, failuresRequired, recoveriesRequired = MONITORING_DEFAULTS.healthRecoveryCount) => {
	const next = { ...state };
	let transition = null;
	if (healthy) {
		next.failureStreak = 0;
		next.successStreak += 1;
		if (state.incidentOpen && next.successStreak >= recoveriesRequired) {
			next.incidentOpen = false;
			transition = "resolve";
		}
	} else {
		next.successStreak = 0;
		next.failureStreak += 1;
		if (!state.incidentOpen && next.failureStreak >= failuresRequired) {
			next.incidentOpen = true;
			transition = "open";
		}
	}
	return {
		state: next,
		transition
	};
};
const evaluateThresholdSignal = (state, value, threshold, highSamplesRequired, normalSamplesRequired = MONITORING_DEFAULTS.resourceRecoveryCount, recoveryMargin = MONITORING_DEFAULTS.resourceRecoveryMargin) => {
	const next = { ...state };
	let transition = null;
	if (value >= threshold) {
		next.highStreak += 1;
		next.normalStreak = 0;
		if (!state.incidentOpen && next.highStreak >= highSamplesRequired) {
			next.incidentOpen = true;
			transition = "open";
		}
	} else if (value <= Math.max(0, threshold - recoveryMargin)) {
		next.highStreak = 0;
		next.normalStreak += 1;
		if (state.incidentOpen && next.normalStreak >= normalSamplesRequired) {
			next.incidentOpen = false;
			transition = "resolve";
		}
	} else {
		next.highStreak = 0;
		next.normalStreak = 0;
	}
	return {
		state: next,
		transition
	};
};
const classifyHealthError = (error) => {
	const message = `${error}`.toLowerCase();
	if (message.includes("timeout") || message.includes("deadline exceeded")) return "timeout";
	if (message.includes("no such host") || message.includes("dns") || message.includes("resolve")) return "dns";
	if (message.includes("tls") || message.includes("certificate") || message.includes("x509")) return "tls";
	if (message.includes("connect") || message.includes("network") || message.includes("refused")) return "network";
	return "unknown";
};
const monitoringRetryDelayMs = (attempts) => {
	const delayMinutes = [
		0,
		1,
		5,
		15,
		60
	];
	return delayMinutes[Math.min(Math.max(0, attempts), delayMinutes.length - 1)] * 60 * 1e3;
};
const MONITORING_MAX_DELIVERY_ATTEMPTS = 5;
const shouldRetryMonitoringDelivery = (phase, attempts) => phase !== "test" && attempts < 5;
const formatMonitoringPercent = (value) => `${Math.round(value * 10) / 10}`.replace(".", ",");
const monitoringHistoryRanges = {
	"24h": {
		durationMs: 1440 * 60 * 1e3,
		bucketSeconds: 60
	},
	"7d": {
		durationMs: 10080 * 60 * 1e3,
		bucketSeconds: 600
	},
	"30d": {
		durationMs: 720 * 60 * 60 * 1e3,
		bucketSeconds: 3600
	}
};

//#endregion
//#region src/lib/handlers/instance/api/HandleInstanceMonitoring.ts
const POLICY_COLLECTION = "instance_monitoring_policies";
const CHECK_COLLECTION = "instance_health_checks";
const INCIDENT_COLLECTION = "instance_monitoring_incidents";
const DELIVERY_COLLECTION = "instance_monitoring_deliveries";
const HEALTH_RETENTION_MS = 720 * 60 * 60 * 1e3;
const INCIDENT_PAGE_SIZE = 25;
const nowIso = () => (/* @__PURE__ */ new Date()).toISOString();
const formatPocketBaseDate = (timestamp) => new Date(timestamp).toISOString().replace("T", " ");
const assertSafeInstanceId = (id) => {
	if (!id.match(/^[a-z0-9]+$/)) throw new BadRequestError("Identifiant d'instance invalide.");
};
const pathValue = (e, name) => {
	if (!e.request) throw new BadRequestError("Requête invalide.");
	return e.request.pathValue(name);
};
const requireAuthRecord = (authRecord) => {
	if (!authRecord) throw new BadRequestError("Session utilisateur attendue.");
	return authRecord;
};
const findInstance = (id) => {
	assertSafeInstanceId(id);
	const instance = $app.findRecordById("instances", id);
	if (!instance) throw new BadRequestError(`Instance ${id} introuvable.`);
	return instance;
};
const assertInstanceAccess = (instance, authRecord) => {
	if (instance.getString("uid") !== authRecord.id && !authRecord.getBool("superAdmin")) throw new BadRequestError("Non autorisé.");
};
const findPolicy = (instanceId) => {
	try {
		return $app.findFirstRecordByFilter(POLICY_COLLECTION, "instance = {:instance}", { instance: instanceId });
	} catch {
		return null;
	}
};
const setPolicyDefaults = (policy, instance) => {
	policy.set("user", instance.getString("uid"));
	policy.set("instance", instance.id);
	policy.set("enabled", false);
	policy.set("healthEnabled", true);
	policy.set("healthPath", MONITORING_DEFAULTS.healthPath);
	policy.set("healthFailureCount", MONITORING_DEFAULTS.healthFailureCount);
	policy.set("cpuEnabled", true);
	policy.set("cpuThresholdPercent", MONITORING_DEFAULTS.cpuThresholdPercent);
	policy.set("cpuSustainMinutes", MONITORING_DEFAULTS.cpuSustainMinutes);
	policy.set("memoryEnabled", true);
	policy.set("memoryThresholdPercent", MONITORING_DEFAULTS.memoryThresholdPercent);
	policy.set("memorySustainMinutes", MONITORING_DEFAULTS.memorySustainMinutes);
	policy.set("backupAlertsEnabled", true);
	policy.set("emailEnabled", true);
	policy.set("discordEnabled", false);
	policy.set("discordWebhook", "");
	policy.set("slackEnabled", false);
	policy.set("slackWebhook", "");
	policy.set("lastHealthStatus", "unknown");
	policy.set("lastCheckAt", "");
	policy.set("graceUntil", "");
	policy.set("lastMetricsAt", "");
	policy.set("healthFailureStreak", 0);
	policy.set("healthSuccessStreak", 0);
	policy.set("cpuHighStreak", 0);
	policy.set("cpuNormalStreak", 0);
	policy.set("memoryHighStreak", 0);
	policy.set("memoryNormalStreak", 0);
	policy.set("lastCpuCapacityPercent", 0);
	policy.set("lastMemoryPercent", 0);
	policy.set("lastLatencyMs", 0);
	policy.set("lastStatusCode", 0);
	policy.set("lastError", "");
	return policy;
};
const createPolicy = (instance) => {
	const policy = new Record($app.findCollectionByNameOrId(POLICY_COLLECTION));
	setPolicyDefaults(policy, instance);
	$app.save(policy);
	return policy;
};
const monitoringApexDomain = () => `${$os.getenv("APEX_DOMAIN") || "pockethost.lvh.me"}`.trim();
const monitoringProtocol = () => `${$os.getenv("HTTP_PROTOCOL") || "https"}`.replace(/:$/, "");
const monitoringTargetUrl = (instance, healthPath) => `${monitoringProtocol()}://${instance.getString("subdomain")}.${monitoringApexDomain()}${healthPath}`;
const defaultPolicyPayload = (instance) => ({
	id: "",
	user: instance.getString("uid"),
	instance: instance.id,
	enabled: false,
	healthEnabled: true,
	healthPath: MONITORING_DEFAULTS.healthPath,
	healthFailureCount: MONITORING_DEFAULTS.healthFailureCount,
	cpuEnabled: true,
	cpuThresholdPercent: MONITORING_DEFAULTS.cpuThresholdPercent,
	cpuSustainMinutes: MONITORING_DEFAULTS.cpuSustainMinutes,
	memoryEnabled: true,
	memoryThresholdPercent: MONITORING_DEFAULTS.memoryThresholdPercent,
	memorySustainMinutes: MONITORING_DEFAULTS.memorySustainMinutes,
	backupAlertsEnabled: true,
	emailEnabled: true,
	discordEnabled: false,
	slackEnabled: false,
	hasDiscordWebhook: false,
	hasSlackWebhook: false,
	lastHealthStatus: "unknown",
	lastCheckAt: "",
	graceUntil: "",
	lastMetricsAt: "",
	healthFailureStreak: 0,
	healthSuccessStreak: 0,
	cpuHighStreak: 0,
	cpuNormalStreak: 0,
	memoryHighStreak: 0,
	memoryNormalStreak: 0,
	lastCpuCapacityPercent: null,
	lastMemoryPercent: null,
	lastLatencyMs: null,
	lastStatusCode: null,
	lastError: "",
	created: "",
	updated: ""
});
const nullableObservedMetric = (policy, field, observedAtField) => {
	if (!policy.getString(observedAtField)) return null;
	const value = Number(policy.get(field));
	return Number.isFinite(value) ? value : null;
};
const serializePolicy = (instance, policy) => {
	if (!policy) return defaultPolicyPayload(instance);
	return {
		id: policy.id,
		user: policy.getString("user"),
		instance: policy.getString("instance"),
		enabled: policy.getBool("enabled"),
		healthEnabled: policy.getBool("healthEnabled"),
		healthPath: policy.getString("healthPath") || MONITORING_DEFAULTS.healthPath,
		healthFailureCount: policy.getInt("healthFailureCount") || MONITORING_DEFAULTS.healthFailureCount,
		cpuEnabled: policy.getBool("cpuEnabled"),
		cpuThresholdPercent: policy.getFloat("cpuThresholdPercent") || MONITORING_DEFAULTS.cpuThresholdPercent,
		cpuSustainMinutes: policy.getInt("cpuSustainMinutes") || MONITORING_DEFAULTS.cpuSustainMinutes,
		memoryEnabled: policy.getBool("memoryEnabled"),
		memoryThresholdPercent: policy.getFloat("memoryThresholdPercent") || MONITORING_DEFAULTS.memoryThresholdPercent,
		memorySustainMinutes: policy.getInt("memorySustainMinutes") || MONITORING_DEFAULTS.memorySustainMinutes,
		backupAlertsEnabled: policy.getBool("backupAlertsEnabled"),
		emailEnabled: policy.getBool("emailEnabled"),
		discordEnabled: policy.getBool("discordEnabled"),
		slackEnabled: policy.getBool("slackEnabled"),
		hasDiscordWebhook: !!policy.getString("discordWebhook"),
		hasSlackWebhook: !!policy.getString("slackWebhook"),
		lastHealthStatus: policy.getString("lastHealthStatus") || "unknown",
		lastCheckAt: policy.getString("lastCheckAt"),
		graceUntil: policy.getString("graceUntil"),
		lastMetricsAt: policy.getString("lastMetricsAt"),
		healthFailureStreak: policy.getInt("healthFailureStreak"),
		healthSuccessStreak: policy.getInt("healthSuccessStreak"),
		cpuHighStreak: policy.getInt("cpuHighStreak"),
		cpuNormalStreak: policy.getInt("cpuNormalStreak"),
		memoryHighStreak: policy.getInt("memoryHighStreak"),
		memoryNormalStreak: policy.getInt("memoryNormalStreak"),
		lastCpuCapacityPercent: nullableObservedMetric(policy, "lastCpuCapacityPercent", "lastMetricsAt"),
		lastMemoryPercent: nullableObservedMetric(policy, "lastMemoryPercent", "lastMetricsAt"),
		lastLatencyMs: nullableObservedMetric(policy, "lastLatencyMs", "lastCheckAt"),
		lastStatusCode: policy.getString("lastCheckAt") && policy.getInt("lastStatusCode") > 0 ? policy.getInt("lastStatusCode") : null,
		lastError: policy.getString("lastError"),
		created: policy.getString("created"),
		updated: policy.getString("updated")
	};
};
const monitoringResponse = (instance, policy) => {
	const serialized = serializePolicy(instance, policy);
	let emailAddress = "";
	try {
		emailAddress = $app.findRecordById("users", instance.getString("uid")).email();
	} catch {}
	return {
		policy: serialized,
		capabilities: {
			emailAddress,
			targetUrl: monitoringTargetUrl(instance, serialized.healthPath),
			requestIntervalSeconds: 60,
			requestTimeoutSeconds: MONITORING_DEFAULTS.requestTimeoutSeconds,
			retentionDays: 30
		}
	};
};
const clampNumber = (value, fallback, min, max, integer = false) => {
	const parsed = Number(value);
	if (!Number.isFinite(parsed)) return fallback;
	const clamped = Math.max(min, Math.min(max, parsed));
	return integer ? Math.round(clamped) : clamped;
};
const findOpenIncident = (instanceId, type, sourceId = "") => {
	const sourceFilter = sourceId ? " && sourceId = {:sourceId}" : "";
	return $app.findRecordsByFilter(INCIDENT_COLLECTION, `instance = {:instance} && type = {:type} && status = "open"${sourceFilter}`, "-openedAt", 1, 0, {
		instance: instanceId,
		type,
		sourceId
	})[0] || null;
};
const incidentLabel = (type) => {
	if (type === "health") return "Instance indisponible";
	if (type === "cpu") return "CPU élevé";
	if (type === "memory") return "Mémoire élevée";
	return "Sauvegarde en échec";
};
const queueDelivery = (instance, incident, phase, channel, payload) => {
	const delivery = new Record($app.findCollectionByNameOrId(DELIVERY_COLLECTION));
	delivery.set("user", instance.getString("uid"));
	delivery.set("instance", instance.id);
	delivery.set("incident", incident?.id || "");
	delivery.set("phase", phase);
	delivery.set("channel", channel);
	delivery.set("status", "pending");
	delivery.set("attempts", 0);
	delivery.set("nextAttemptAt", nowIso());
	delivery.set("lastAttemptAt", "");
	delivery.set("deliveredAt", "");
	delivery.set("lastError", "");
	delivery.set("payload", payload);
	$app.save(delivery);
	return delivery;
};
const queueIncidentNotifications = (policy, instance, incident, phase) => {
	const type = incident.getString("type");
	const payload = {
		title: `${phase === "opened" ? "ALERTE" : "RÉTABLI"} · ${incidentLabel(type)} · ${instance.getString("subdomain")}`,
		message: incident.getString("message"),
		instanceId: instance.id,
		instanceName: instance.getString("subdomain"),
		type,
		phase,
		occurredAt: phase === "opened" ? incident.getString("openedAt") : incident.getString("resolvedAt")
	};
	if (policy.getBool("emailEnabled")) queueDelivery(instance, incident, phase, "email", payload);
	if (policy.getBool("discordEnabled") && policy.getString("discordWebhook")) queueDelivery(instance, incident, phase, "discord", payload);
	if (policy.getBool("slackEnabled") && policy.getString("slackWebhook")) queueDelivery(instance, incident, phase, "slack", payload);
};
const openIncident = (policy, instance, type, message, value = 0, threshold = 0, details = {}, sourceId = "") => {
	const existing = findOpenIncident(instance.id, type, sourceId);
	if (existing) return existing;
	const incident = new Record($app.findCollectionByNameOrId(INCIDENT_COLLECTION));
	incident.set("user", instance.getString("uid"));
	incident.set("instance", instance.id);
	incident.set("type", type);
	incident.set("status", "open");
	incident.set("openedAt", nowIso());
	incident.set("resolvedAt", "");
	incident.set("value", value);
	incident.set("threshold", threshold);
	incident.set("message", message);
	incident.set("details", details);
	incident.set("sourceId", sourceId);
	$app.save(incident);
	queueIncidentNotifications(policy, instance, incident, "opened");
	return incident;
};
const resolveIncident = (policy, instance, incident, message, notify = true, value) => {
	if (!incident || incident.getString("status") !== "open") return null;
	incident.set("status", "resolved");
	incident.set("resolvedAt", nowIso());
	incident.set("message", message);
	if (value !== void 0) incident.set("value", value);
	$app.save(incident);
	if (notify) queueIncidentNotifications(policy, instance, incident, "resolved");
	return incident;
};
const resetRuntimeStreaks = (policy) => {
	policy.set("healthFailureStreak", 0);
	policy.set("healthSuccessStreak", 0);
	policy.set("cpuHighStreak", 0);
	policy.set("cpuNormalStreak", 0);
	policy.set("memoryHighStreak", 0);
	policy.set("memoryNormalStreak", 0);
};
const pauseRuntimeMonitoring = (policy, instance, includeBackups = false) => {
	const types = includeBackups ? [
		"health",
		"cpu",
		"memory",
		"backup"
	] : [
		"health",
		"cpu",
		"memory"
	];
	for (const type of types) resolveIncident(policy, instance, findOpenIncident(instance.id, type), "Surveillance mise en pause.", false);
	policy.set("lastHealthStatus", "paused");
	policy.set("graceUntil", "");
	resetRuntimeStreaks(policy);
	$app.save(policy);
};
const runHealthProbe = (instance, path) => {
	const startedAt = Date.now();
	try {
		const response = $http.send({
			url: monitoringTargetUrl(instance, path),
			method: "GET",
			timeout: MONITORING_DEFAULTS.requestTimeoutSeconds,
			headers: {
				Accept: "application/json",
				"User-Agent": "PocketHost-Monitor/1.0"
			}
		});
		const healthy = response.statusCode >= 200 && response.statusCode < 400;
		return {
			healthy,
			latencyMs: Math.max(0, Date.now() - startedAt),
			statusCode: response.statusCode,
			errorKind: healthy ? "none" : "http",
			error: healthy ? "" : `Réponse HTTP ${response.statusCode}`
		};
	} catch (error) {
		return {
			healthy: false,
			latencyMs: Math.max(0, Date.now() - startedAt),
			statusCode: 0,
			errorKind: classifyHealthError(error),
			error: `${error}`.slice(0, 300)
		};
	}
};
const saveHealthCheck = (instance, probe, checkedAt) => {
	const record = new Record($app.findCollectionByNameOrId(CHECK_COLLECTION));
	record.set("user", instance.getString("uid"));
	record.set("instance", instance.id);
	record.set("checkedAt", checkedAt);
	record.set("result", probe.healthy ? "healthy" : "unhealthy");
	record.set("latencyMs", probe.latencyMs);
	record.set("statusCode", probe.statusCode);
	record.set("errorKind", probe.errorKind);
	record.set("error", probe.error);
	$app.save(record);
};
const evaluateHealth = (policy, instance) => {
	if (!policy.getBool("healthEnabled")) return;
	const checkedAt = nowIso();
	const probe = runHealthProbe(instance, normalizeHealthPath(policy.getString("healthPath")));
	saveHealthCheck(instance, probe, checkedAt);
	policy.set("lastCheckAt", checkedAt);
	policy.set("lastHealthStatus", probe.healthy ? "healthy" : "unhealthy");
	policy.set("lastLatencyMs", probe.latencyMs);
	policy.set("lastStatusCode", probe.statusCode);
	policy.set("lastError", probe.error);
	const incident = findOpenIncident(instance.id, "health");
	if (Date.parse(policy.getString("graceUntil")) > Date.now()) {
		policy.set("healthFailureStreak", 0);
		policy.set("healthSuccessStreak", 0);
		return;
	}
	const evaluation = evaluateHealthSignal({
		failureStreak: policy.getInt("healthFailureStreak"),
		successStreak: policy.getInt("healthSuccessStreak"),
		incidentOpen: !!incident
	}, probe.healthy, policy.getInt("healthFailureCount") || MONITORING_DEFAULTS.healthFailureCount);
	policy.set("healthFailureStreak", evaluation.state.failureStreak);
	policy.set("healthSuccessStreak", evaluation.state.successStreak);
	if (evaluation.transition === "open") openIncident(policy, instance, "health", `${instance.getString("subdomain")} ne répond plus correctement (${probe.error || `HTTP ${probe.statusCode}`}).`, probe.statusCode, 0, {
		latencyMs: probe.latencyMs,
		errorKind: probe.errorKind,
		healthPath: policy.getString("healthPath")
	});
	else if (evaluation.transition === "resolve") resolveIncident(policy, instance, incident, `${instance.getString("subdomain")} répond de nouveau normalement en ${probe.latencyMs} ms.`, true, probe.statusCode);
};
const evaluateResource = (policy, instance, type, value) => {
	const prefix = type === "cpu" ? "cpu" : "memory";
	if (!policy.getBool(`${prefix}Enabled`) || value === null || !Number.isFinite(value)) return;
	const threshold = policy.getFloat(`${prefix}ThresholdPercent`) || MONITORING_DEFAULTS[`${prefix}ThresholdPercent`];
	const sustain = policy.getInt(`${prefix}SustainMinutes`) || MONITORING_DEFAULTS[`${prefix}SustainMinutes`];
	const highField = type === "cpu" ? "cpuHighStreak" : "memoryHighStreak";
	const normalField = type === "cpu" ? "cpuNormalStreak" : "memoryNormalStreak";
	const lastField = type === "cpu" ? "lastCpuCapacityPercent" : "lastMemoryPercent";
	const incident = findOpenIncident(instance.id, type);
	const evaluation = evaluateThresholdSignal({
		highStreak: policy.getInt(highField),
		normalStreak: policy.getInt(normalField),
		incidentOpen: !!incident
	}, value, threshold, sustain);
	policy.set(highField, evaluation.state.highStreak);
	policy.set(normalField, evaluation.state.normalStreak);
	policy.set(lastField, value);
	const formatted = formatMonitoringPercent(value);
	if (evaluation.transition === "open") openIncident(policy, instance, type, `${type === "cpu" ? "Le CPU" : "La mémoire"} de ${instance.getString("subdomain")} atteint ${formatted} % depuis ${sustain} minutes.`, value, threshold, { sustainMinutes: sustain });
	else if (evaluation.transition === "resolve") resolveIncident(policy, instance, incident, `${type === "cpu" ? "Le CPU" : "La mémoire"} de ${instance.getString("subdomain")} est revenu à ${formatted} %.`, true, value);
};
const collectMonitoringPolicy = (policy, snapshot) => {
	let instance;
	try {
		instance = findInstance(policy.getString("instance"));
	} catch {
		return false;
	}
	if (!instance.getBool("power")) {
		pauseRuntimeMonitoring(policy, instance);
		return true;
	}
	if (policy.getString("lastHealthStatus") === "paused" || !policy.getString("graceUntil")) {
		policy.set("graceUntil", new Date(Date.now() + MONITORING_DEFAULTS.startupGraceMs).toISOString());
		resetRuntimeStreaks(policy);
	}
	evaluateHealth(policy, instance);
	const metric = serializeInstanceRuntimeMetrics(instance, snapshot);
	if (metric.cpuCapacityPercent !== null) policy.set("lastCpuCapacityPercent", metric.cpuCapacityPercent);
	if (metric.memoryPercent !== null) policy.set("lastMemoryPercent", metric.memoryPercent);
	if (metric.cpuCapacityPercent !== null || metric.memoryPercent !== null) policy.set("lastMetricsAt", nowIso());
	evaluateResource(policy, instance, "cpu", metric.cpuCapacityPercent);
	evaluateResource(policy, instance, "memory", metric.memoryPercent);
	$app.save(policy);
	return true;
};
const CollectInstanceMonitoring = (snapshot) => {
	const policies = $app.findRecordsByFilter(POLICY_COLLECTION, "enabled = true", "", 500, 0);
	let checked = 0;
	let failed = 0;
	for (const policy of policies) try {
		if (collectMonitoringPolicy(policy, snapshot)) checked += 1;
	} catch (error) {
		failed += 1;
		console.warn(`Surveillance impossible pour l'instance ${policy.getString("instance")}: ${error}`);
	}
	return {
		checked,
		failed
	};
};
const escapeHtml = (value) => `${value ?? ""}`.replace(/[&<>"']/g, (character) => ({
	"&": "&amp;",
	"<": "&lt;",
	">": "&gt;",
	"\"": "&quot;",
	"'": "&#039;"
})[character]);
const deliveryPayload = (delivery) => {
	try {
		return normalizeMonitoringNotificationPayload(delivery.getString("payload"));
	} catch (serializedError) {
		try {
			return normalizeMonitoringNotificationPayload(delivery.get("payload"));
		} catch {
			throw serializedError;
		}
	}
};
const sendEmailDelivery = (delivery, payload) => {
	const user = $app.findRecordById("users", delivery.getString("user"));
	const skipReason = mailRecipientSkipReason(user);
	if (skipReason) throw new Error(`Email non envoyé : compte ${skipReason}.`);
	const settings = $app.settings();
	const senderAddress = `${settings.meta?.senderAddress || ""}`.trim();
	const senderName = `${settings.meta?.senderName || "Gestion PocketBase"}`.trim();
	const recipientAddress = `${user.email() || ""}`.trim();
	if (!settings.smtp?.enabled) throw new Error("Email non envoyé : SMTP est désactivé dans l'administration du serveur.");
	if (!`${settings.smtp.host || ""}`.trim()) throw new Error("Email non envoyé : l'hôte SMTP n'est pas configuré.");
	if (!senderAddress) throw new Error("Email non envoyé : l'adresse expéditeur SMTP n'est pas configurée.");
	if (!recipientAddress) throw new Error("Email non envoyé : l'utilisateur n'a pas d'adresse email.");
	const html = `<h2>${escapeHtml(payload.title)}</h2><p>${escapeHtml(payload.message)}</p><p><strong>Instance :</strong> ${escapeHtml(payload.instanceName)}</p><p><small>${escapeHtml(payload.occurredAt)}</small></p>`;
	const message = new MailerMessage({
		from: {
			address: senderAddress,
			name: senderName
		},
		to: [{ address: recipientAddress }],
		subject: `[PocketHost] ${payload.title}`,
		html
	});
	$app.newMailClient().send(message);
};
const sendWebhookDelivery = (delivery, payload) => {
	const policy = findPolicy(delivery.getString("instance"));
	if (!policy) throw new Error("Configuration de surveillance introuvable.");
	const channel = delivery.getString("channel");
	const content = `**${payload.title}**\n${payload.message}\nInstance : \`${payload.instanceName}\`\n${payload.occurredAt}`;
	const slackText = content.replace(/\*\*/g, "*").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
	const url = channel === "discord" ? normalizeDiscordWebhook(policy.getString("discordWebhook")) : normalizeSlackWebhook(policy.getString("slackWebhook"));
	if (!url) throw new Error(`Webhook ${channel} non configuré.`);
	const response = $http.send({
		url,
		method: "POST",
		timeout: 5,
		headers: {
			"Content-Type": "application/json",
			"User-Agent": "PocketHost-Monitor/1.0"
		},
		body: JSON.stringify(channel === "discord" ? {
			content,
			allowed_mentions: { parse: [] }
		} : { text: slackText })
	});
	if (response.statusCode < 200 || response.statusCode >= 300) throw new Error(`Webhook ${channel} : HTTP ${response.statusCode}`);
};
const processDelivery = (delivery) => {
	if (delivery.getString("status") !== "pending") return delivery;
	const attempts = delivery.getInt("attempts") + 1;
	delivery.set("attempts", attempts);
	delivery.set("lastAttemptAt", nowIso());
	try {
		const payload = deliveryPayload(delivery);
		if (delivery.getString("channel") === "email") sendEmailDelivery(delivery, payload);
		else sendWebhookDelivery(delivery, payload);
		delivery.set("status", "sent");
		delivery.set("deliveredAt", nowIso());
		delivery.set("lastError", "");
	} catch (error) {
		delivery.set("lastError", `${error}`.slice(0, 1e3));
		if (!shouldRetryMonitoringDelivery(delivery.getString("phase"), attempts)) {
			delivery.set("status", "abandoned");
			delivery.set("nextAttemptAt", "");
		} else delivery.set("nextAttemptAt", new Date(Date.now() + monitoringRetryDelayMs(attempts)).toISOString());
	}
	$app.save(delivery);
	return delivery;
};
const ProcessInstanceMonitoringDeliveries = () => {
	const deliveries = $app.findRecordsByFilter(DELIVERY_COLLECTION, "status = \"pending\" && nextAttemptAt <= {:now}", "nextAttemptAt", 100, 0, { now: formatPocketBaseDate(Date.now()) });
	for (const delivery of deliveries) processDelivery(delivery);
	return { processed: deliveries.length };
};
const CollectInstanceMetricsAndMonitoring = () => {
	const snapshot = readDockerMetricsSnapshot();
	return {
		metrics: CollectInstanceResourceMetrics(snapshot),
		monitoring: CollectInstanceMonitoring(snapshot),
		deliveries: ProcessInstanceMonitoringDeliveries()
	};
};
const PurgeExpiredInstanceHealthChecks = () => {
	const cutoff = formatPocketBaseDate(Date.now() - HEALTH_RETENTION_MS);
	let deleted = 0;
	for (;;) {
		const records = $app.findRecordsByFilter(CHECK_COLLECTION, "checkedAt < {:cutoff}", "checkedAt", 1e3, 0, { cutoff });
		if (records.length === 0) break;
		for (const record of records) {
			$app.delete(record);
			deleted += 1;
		}
	}
	return { deleted };
};
const readMonitoringHistoryRange = (e) => {
	const requested = `${e.request.url.query().get("range") || "24h"}`;
	return requested in monitoringHistoryRanges ? requested : "24h";
};
const queryMonitoringHistory = (instanceId, range) => {
	const config = monitoringHistoryRanges[range];
	const rows = arrayOf(new DynamicModel({
		periodStartUnix: 0,
		totalChecks: 0,
		successfulChecks: 0,
		averageLatencyMs: 0,
		maxLatencyMs: 0
	}));
	$app.db().newQuery(`SELECT
        CAST(strftime('%s', checkedAt) / {:bucketSeconds} AS INTEGER) * {:bucketSeconds} AS periodStartUnix,
        COUNT(*) AS totalChecks,
        SUM(CASE WHEN result = 'healthy' THEN 1 ELSE 0 END) AS successfulChecks,
        AVG(latencyMs) AS averageLatencyMs,
        MAX(latencyMs) AS maxLatencyMs
      FROM instance_health_checks
      WHERE instance = {:instance} AND checkedAt >= {:cutoff}
      GROUP BY periodStartUnix
      ORDER BY periodStartUnix`).bind({
		bucketSeconds: config.bucketSeconds,
		instance: instanceId,
		cutoff: formatPocketBaseDate(Date.now() - config.durationMs)
	}).all(rows);
	return rows.map((row) => {
		const totalChecks = Number(row.totalChecks || 0);
		const successfulChecks = Number(row.successfulChecks || 0);
		return {
			collectedAt: (/* @__PURE__ */ new Date(Number(row.periodStartUnix) * 1e3)).toISOString(),
			totalChecks,
			successfulChecks,
			availabilityPercent: totalChecks > 0 ? successfulChecks / totalChecks * 100 : null,
			averageLatencyMs: Math.round(Number(row.averageLatencyMs || 0)),
			maxLatencyMs: Math.round(Number(row.maxLatencyMs || 0))
		};
	});
};
const serializeIncident = (incident) => ({
	id: incident.id,
	type: incident.getString("type"),
	status: incident.getString("status"),
	openedAt: incident.getString("openedAt"),
	resolvedAt: incident.getString("resolvedAt"),
	value: Number(incident.get("value") || 0),
	threshold: Number(incident.get("threshold") || 0),
	message: incident.getString("message"),
	details: incident.get("details") || {},
	sourceId: incident.getString("sourceId"),
	created: incident.getString("created"),
	updated: incident.getString("updated")
});
const HandleInstanceMonitoringGet = (e) => {
	const authRecord = requireAuthRecord(e.auth);
	const instance = findInstance(pathValue(e, "id"));
	assertInstanceAccess(instance, authRecord);
	return e.json(200, monitoringResponse(instance, findPolicy(instance.id)));
};
const HandleInstanceMonitoringUpdate = (e) => {
	const authRecord = requireAuthRecord(e.auth);
	const instance = findInstance(pathValue(e, "id"));
	assertInstanceAccess(instance, authRecord);
	let data = new DynamicModel({
		enabled: false,
		healthEnabled: true,
		healthPath: MONITORING_DEFAULTS.healthPath,
		healthFailureCount: MONITORING_DEFAULTS.healthFailureCount,
		cpuEnabled: true,
		cpuThresholdPercent: MONITORING_DEFAULTS.cpuThresholdPercent,
		cpuSustainMinutes: MONITORING_DEFAULTS.cpuSustainMinutes,
		memoryEnabled: true,
		memoryThresholdPercent: MONITORING_DEFAULTS.memoryThresholdPercent,
		memorySustainMinutes: MONITORING_DEFAULTS.memorySustainMinutes,
		backupAlertsEnabled: true,
		emailEnabled: true,
		discordEnabled: false,
		discordWebhook: "",
		clearDiscordWebhook: false,
		slackEnabled: false,
		slackWebhook: "",
		clearSlackWebhook: false
	});
	e.bindBody(data);
	data = JSON.parse(JSON.stringify(data));
	const policy = findPolicy(instance.id) || createPolicy(instance);
	const wasEnabled = policy.getBool("enabled");
	const wasHealthEnabled = policy.getBool("healthEnabled");
	const healthPath = normalizeHealthPath(data.healthPath);
	const submittedDiscord = `${data.discordWebhook || ""}`.trim();
	const submittedSlack = `${data.slackWebhook || ""}`.trim();
	if (data.clearDiscordWebhook) policy.set("discordWebhook", "");
	else if (submittedDiscord) policy.set("discordWebhook", normalizeDiscordWebhook(submittedDiscord));
	if (data.clearSlackWebhook) policy.set("slackWebhook", "");
	else if (submittedSlack) policy.set("slackWebhook", normalizeSlackWebhook(submittedSlack));
	if (data.discordEnabled && !policy.getString("discordWebhook")) throw new BadRequestError("Ajoutez une URL Discord valide.");
	if (data.slackEnabled && !policy.getString("slackWebhook")) throw new BadRequestError("Ajoutez une URL Slack valide.");
	policy.set("user", instance.getString("uid"));
	policy.set("enabled", !!data.enabled);
	policy.set("healthEnabled", !!data.healthEnabled);
	policy.set("healthPath", healthPath);
	policy.set("healthFailureCount", clampNumber(data.healthFailureCount, MONITORING_DEFAULTS.healthFailureCount, 1, 10, true));
	policy.set("cpuEnabled", !!data.cpuEnabled);
	policy.set("cpuThresholdPercent", clampNumber(data.cpuThresholdPercent, MONITORING_DEFAULTS.cpuThresholdPercent, 1, 100));
	policy.set("cpuSustainMinutes", clampNumber(data.cpuSustainMinutes, MONITORING_DEFAULTS.cpuSustainMinutes, 1, 60, true));
	policy.set("memoryEnabled", !!data.memoryEnabled);
	policy.set("memoryThresholdPercent", clampNumber(data.memoryThresholdPercent, MONITORING_DEFAULTS.memoryThresholdPercent, 1, 100));
	policy.set("memorySustainMinutes", clampNumber(data.memorySustainMinutes, MONITORING_DEFAULTS.memorySustainMinutes, 1, 60, true));
	policy.set("backupAlertsEnabled", !!data.backupAlertsEnabled);
	policy.set("emailEnabled", !!data.emailEnabled);
	policy.set("discordEnabled", !!data.discordEnabled);
	policy.set("slackEnabled", !!data.slackEnabled);
	if (!wasEnabled && data.enabled) {
		policy.set("lastHealthStatus", "unknown");
		policy.set("graceUntil", new Date(Date.now() + MONITORING_DEFAULTS.startupGraceMs).toISOString());
		resetRuntimeStreaks(policy);
	} else if (data.enabled && data.healthEnabled && !wasHealthEnabled) {
		policy.set("lastHealthStatus", "unknown");
		policy.set("graceUntil", new Date(Date.now() + MONITORING_DEFAULTS.startupGraceMs).toISOString());
		policy.set("healthFailureStreak", 0);
		policy.set("healthSuccessStreak", 0);
	}
	if (wasEnabled && !data.enabled) pauseRuntimeMonitoring(policy, instance, true);
	else {
		if (data.enabled && !data.healthEnabled) {
			resolveIncident(policy, instance, findOpenIncident(instance.id, "health"), "Sonde HTTP désactivée.", false);
			policy.set("lastHealthStatus", instance.getBool("power") ? "unknown" : "paused");
			policy.set("lastError", "");
			policy.set("healthFailureStreak", 0);
			policy.set("healthSuccessStreak", 0);
		}
		if (data.enabled && !data.cpuEnabled) {
			resolveIncident(policy, instance, findOpenIncident(instance.id, "cpu"), "Alerte CPU désactivée.", false);
			policy.set("cpuHighStreak", 0);
			policy.set("cpuNormalStreak", 0);
		}
		if (data.enabled && !data.memoryEnabled) {
			resolveIncident(policy, instance, findOpenIncident(instance.id, "memory"), "Alerte mémoire désactivée.", false);
			policy.set("memoryHighStreak", 0);
			policy.set("memoryNormalStreak", 0);
		}
		if (data.enabled && !data.backupAlertsEnabled) {
			const backupIncidents = $app.findRecordsByFilter(INCIDENT_COLLECTION, "instance = {:instance} && type = \"backup\" && status = \"open\"", "openedAt", 100, 0, { instance: instance.id });
			for (const incident of backupIncidents) resolveIncident(policy, instance, incident, "Alerte de sauvegarde désactivée.", false);
		}
		$app.save(policy);
	}
	return e.json(200, monitoringResponse(instance, policy));
};
const HandleInstanceMonitoringHistory = (e) => {
	const authRecord = requireAuthRecord(e.auth);
	const instance = findInstance(pathValue(e, "id"));
	assertInstanceAccess(instance, authRecord);
	const range = readMonitoringHistoryRange(e);
	const points = queryMonitoringHistory(instance.id, range);
	const totalChecks = points.reduce((sum, point) => sum + point.totalChecks, 0);
	const successfulChecks = points.reduce((sum, point) => sum + point.successfulChecks, 0);
	const latencyWeightedSum = points.reduce((sum, point) => sum + point.averageLatencyMs * point.totalChecks, 0);
	return e.json(200, {
		range,
		bucketSeconds: monitoringHistoryRanges[range].bucketSeconds,
		summary: {
			totalChecks,
			successfulChecks,
			availabilityPercent: totalChecks > 0 ? successfulChecks / totalChecks * 100 : null,
			averageLatencyMs: totalChecks > 0 ? Math.round(latencyWeightedSum / totalChecks) : null
		},
		points,
		collectedAt: nowIso()
	});
};
const HandleInstanceMonitoringIncidents = (e) => {
	const authRecord = requireAuthRecord(e.auth);
	const instance = findInstance(pathValue(e, "id"));
	assertInstanceAccess(instance, authRecord);
	const rawCursor = `${e.request.url.query().get("cursor") || ""}`;
	const cursor = rawCursor && Number.isFinite(Date.parse(rawCursor)) ? formatPocketBaseDate(Date.parse(rawCursor)) : "";
	const cursorFilter = cursor ? " && created < {:cursor}" : "";
	const records = $app.findRecordsByFilter(INCIDENT_COLLECTION, `instance = {:instance}${cursorFilter}`, "-created", 26, 0, {
		instance: instance.id,
		cursor
	});
	const hasMore = records.length > INCIDENT_PAGE_SIZE;
	const incidents = records.slice(0, INCIDENT_PAGE_SIZE).map(serializeIncident);
	return e.json(200, {
		incidents,
		nextCursor: hasMore ? incidents.at(-1)?.created || "" : ""
	});
};
const HandleInstanceMonitoringTest = (e) => {
	const authRecord = requireAuthRecord(e.auth);
	const instance = findInstance(pathValue(e, "id"));
	assertInstanceAccess(instance, authRecord);
	const policy = findPolicy(instance.id);
	if (!policy) throw new BadRequestError("Enregistrez la configuration avant le test.");
	const payload = {
		title: `TEST · Surveillance · ${instance.getString("subdomain")}`,
		message: "Les notifications de surveillance PocketHost sont correctement configurées.",
		instanceId: instance.id,
		instanceName: instance.getString("subdomain"),
		type: "test",
		phase: "test",
		occurredAt: nowIso()
	};
	const deliveries = [];
	if (policy.getBool("emailEnabled")) deliveries.push(queueDelivery(instance, null, "test", "email", payload));
	if (policy.getBool("discordEnabled") && policy.getString("discordWebhook")) deliveries.push(queueDelivery(instance, null, "test", "discord", payload));
	if (policy.getBool("slackEnabled") && policy.getString("slackWebhook")) deliveries.push(queueDelivery(instance, null, "test", "slack", payload));
	if (deliveries.length === 0) throw new BadRequestError("Activez au moins un canal de notification.");
	const results = deliveries.map(processDelivery).map((delivery) => ({
		channel: delivery.getString("channel"),
		status: delivery.getString("status"),
		error: delivery.getString("lastError")
	}));
	return e.json(200, { results });
};
const HandleInstanceBackupMonitoringUpdate = (e) => {
	const status = e.record.getString("status");
	if (status === e.record.original().getString("status") || status !== "failed" && status !== "ready") return;
	const instance = findInstance(e.record.getString("instance"));
	const policy = findPolicy(instance.id);
	if (!policy?.getBool("enabled") || !policy.getBool("backupAlertsEnabled")) return;
	if (status === "failed") {
		openIncident(policy, instance, "backup", `La sauvegarde ${e.record.getString("name") || e.record.getString("filename") || e.record.id} a échoué : ${e.record.getString("error") || "erreur inconnue"}.`, 0, 0, {
			backupId: e.record.id,
			kind: e.record.getString("kind")
		}, e.record.id);
		return;
	}
	const openBackups = $app.findRecordsByFilter(INCIDENT_COLLECTION, "instance = {:instance} && type = \"backup\" && status = \"open\"", "openedAt", 100, 0, { instance: instance.id });
	for (const incident of openBackups) resolveIncident(policy, instance, incident, `Une nouvelle sauvegarde de ${instance.getString("subdomain")} a réussi.`);
};

//#endregion
//#region src/lib/handlers/instance/bootstrap/resetInstancesIdle.ts
const resetInstancesIdle = (app) => {
	const records = (() => {
		try {
			return app.findRecordsByFilter(`instances`, `status != 'idle'`).filter((r) => !!r);
		} catch {
			return [];
		}
	})();
	let reset = 0;
	for (const record of records) {
		record.set(`status`, `idle`);
		app.save(record);
		reset++;
	}
	return reset;
};

//#endregion
//#region src/lib/handlers/instance/api/HandleInstancesRuntimeReset.ts
const HandleInstancesRuntimeReset = (e) => {
	const reset = resetInstancesIdle($app);
	refreshAndBroadcastLivePlatformStats();
	return e.json(200, {
		ok: true,
		reset
	});
};

//#endregion
//#region src/lib/util/removeEmptyKeys.ts
const removeEmptyKeys = (obj) => Object.fromEntries(Object.entries(obj).filter(([, value]) => value != null));

//#endregion
//#region src/lib/handlers/instance/api/HandleInstanceUpdate.ts
const callCloudflareAPI = (endpoint, method, body, log) => {
	const apiToken = $os.getenv("MOTHERSHIP_CLOUDFLARE_API_TOKEN");
	const zoneId = $os.getenv("MOTHERSHIP_CLOUDFLARE_ZONE_ID");
	if (!apiToken || !zoneId) {
		if (log) log("Cloudflare API credentials not configured - skipping Cloudflare operations");
		return null;
	}
	const url = `https://api.cloudflare.com/client/v4/zones/${zoneId}/${endpoint}`;
	try {
		const config = {
			url,
			method,
			headers: {
				Authorization: `Bearer ${apiToken}`,
				"Content-Type": "application/json"
			},
			timeout: 30
		};
		if (body) config.body = JSON.stringify(body);
		if (log) log(`Making Cloudflare API call: ${method} ${url}`, config);
		const response = $http.send(config);
		if (log) log(`Cloudflare API response:`, response);
		return response;
	} catch (error) {
		if (log) log(`Cloudflare API error:`, error);
		return null;
	}
};
const createCloudflareCustomHostname = (hostname, log) => {
	return callCloudflareAPI("custom_hostnames", "POST", {
		hostname,
		ssl: {
			method: "http",
			type: "dv"
		}
	}, log);
};
const HandleInstanceUpdate = (e) => {
	const log = mkLog(`PUT:instance`);
	log(`TOP OF PUT`);
	let data = new DynamicModel({
		id: "",
		fields: {
			subdomain: null,
			power: null,
			version: null,
			secrets: null,
			webhooks: null,
			syncAdmin: null,
			autoVacuum: null,
			metricsHistoryEnabled: null,
			dev: null,
			cname: null
		}
	});
	e.bindBody(data);
	log(`After bind`);
	data = JSON.parse(JSON.stringify(data));
	const id = e.request.pathValue("id");
	const { fields: { subdomain, power, version, secrets, webhooks, syncAdmin, autoVacuum, metricsHistoryEnabled, dev, cname } } = data;
	log(`vars`, JSON.stringify({
		id,
		subdomain,
		power,
		version,
		secrets,
		webhooks,
		syncAdmin,
		autoVacuum,
		metricsHistoryEnabled,
		dev,
		cname
	}));
	const record = $app.findRecordById("instances", id);
	const authRecord = e.auth;
	log(`authRecord`, JSON.stringify(authRecord));
	if (!authRecord) throw new Error(`Session utilisateur attendue`);
	if (record.get("uid") !== authRecord.id) throw new BadRequestError(`Non autorisé`);
	const oldCname = record.getString("cname").trim();
	const newCname = cname !== null ? cname.trim() : null;
	const cnameChanged = newCname !== null && oldCname !== newCname;
	if (cnameChanged && newCname.length > 0) {
		log(`CNAME changed from "${oldCname}" to "${newCname}" - adding to Cloudflare`);
		if (createCloudflareCustomHostname(newCname, log)) log(`Cloudflare API call completed for "${newCname}" - frontend will poll for health`);
	}
	const recordAutoVacuum = record.getBool("autoVacuum");
	if (subdomain !== null && subdomain !== record.getString("subdomain") || version !== null && version !== record.getString("version") || syncAdmin !== null && syncAdmin !== record.getBool("syncAdmin") || autoVacuum !== null && autoVacuum !== recordAutoVacuum || dev !== null && dev !== record.getBool("dev") || cnameChanged) {
		if (record.getBool("power") || record.getString("status").toLowerCase() !== "idle") throw new BadRequestError(`L'instance doit d'abord être éteinte.`);
	}
	const sanitized = removeEmptyKeys({
		subdomain,
		version,
		power,
		secrets,
		webhooks,
		syncAdmin,
		autoVacuum,
		metricsHistoryEnabled,
		dev,
		cname
	});
	for (const [key, value] of Object.entries(sanitized)) record.set(key, value);
	$app.save(record);
	return e.json(200, { status: "ok" });
};

//#endregion
//#region src/lib/handlers/instance/bootstrap/HandleInstancesResetIdle.ts
const HandleInstancesResetIdle = (_e) => {
	resetInstancesIdle($app);
	recountLivePlatformStats();
};

//#endregion
//#region src/lib/handlers/instance/bootstrap/HandleMigrateCnamesToDomains.ts
const HandleMigrateCnamesToDomains = (_e) => {
	const log = mkLog(`bootstrap:migrate-cnames`);
	log(`Starting cname to domains migration`);
	try {
		if (!$app.findCollectionByNameOrId("domains")) {
			log(`Domains collection not found, skipping migration`);
			return;
		}
		log(`Checking for instances with cnames`);
		const instancesWithCnames = $app.findRecordsByFilter("instances", "cname != NULL && cname != ''");
		if (instancesWithCnames.length === 0) {
			log(`No cnames to migrate`);
			return;
		}
		log(`Found ${instancesWithCnames.length} instances with cnames`);
		log(`Phase 1: Migrating cnames to domains collection`);
		let cnameMigrated = 0;
		instancesWithCnames.forEach((instance) => {
			if (!instance) return;
			try {
				const cname = instance.getString("cname");
				if (!cname) return;
				const instanceId = instance.id;
				let domainExists = false;
				try {
					$app.findFirstRecordByFilter("domains", `instance = "${instanceId}" && domain = "${cname}"`);
					domainExists = true;
				} catch (e) {}
				if (!domainExists) {
					const domainsCollection = $app.findCollectionByNameOrId("domains");
					const domainRecord = new Record(domainsCollection);
					domainRecord.set("instance", instanceId);
					domainRecord.set("domain", cname);
					domainRecord.set("active", instance.getBool("cname_active"));
					$app.save(domainRecord);
					log(`Created domain record for ${cname}`);
					cnameMigrated++;
				}
			} catch (error) {
				log(`Failed to migrate cname for instance ${instance.id}:`, error);
			}
		});
		log(`Phase 1 complete: migrated ${cnameMigrated} cnames to domains collection`);
		log(`Phase 2: Syncing domains collection with instances.domains arrays`);
		const allDomainRecords = $app.findRecordsByFilter("domains", "1=1");
		log(`Found ${allDomainRecords.length} domain records`);
		let instancesUpdated = 0;
		const domainsByInstance = /* @__PURE__ */ new Map();
		allDomainRecords.forEach((domainRecord) => {
			if (!domainRecord) return;
			const instanceId = domainRecord.getString("instance");
			if (!domainsByInstance.has(instanceId)) domainsByInstance.set(instanceId, []);
			domainsByInstance.get(instanceId).push(domainRecord.id);
		});
		log(`Updating instances.domains arrays`);
		domainsByInstance.forEach((domainIds, instanceId) => {
			try {
				const instance = $app.findRecordById("instances", instanceId);
				if (!instance) return;
				const currentDomains = instance.get("domains") || [];
				log(`Current domains:`, currentDomains);
				const missingIds = domainIds.filter((id) => !currentDomains.includes(id));
				if (missingIds.length > 0) {
					const updatedDomains = [...currentDomains, ...missingIds];
					instance.set("domains", updatedDomains);
					$app.save(instance);
					log(`Updated instance ${instanceId}: added ${missingIds.length} domain IDs to domains array`);
					instancesUpdated++;
				}
			} catch (error) {
				log(`Failed to update domains array for instance ${instanceId}:`, error);
			}
		});
		log(`Phase 2 complete: updated domains arrays for ${instancesUpdated} instances`);
	} catch (error) {
		log(`Error migrating cnames: ${error}`);
	}
};

//#endregion
//#region src/lib/handlers/instance/bootstrap/HandleMigrateInstanceVersions.ts
const HandleMigrateInstanceVersions = (_e) => {
	const log = mkLog(`bootstrap`);
	const versions = listVersions();
	const records = $app.findRecordsByFilter(`instances`, "1=1").filter((r) => !!r);
	const unrecognized = [];
	records.forEach((record) => {
		const v = record.getString("version").trim();
		if (versions.includes(v)) return;
		const newVersion = (() => {
			if (v.startsWith(`~`)) {
				const [major, minor] = v.slice(1).split(".");
				return [
					major,
					minor,
					"*"
				].join(".");
			} else if (v === `^0` || v === `0` || v === "1") return versions[0];
			return v;
		})();
		if (versions.includes(newVersion)) {
			record.set(`version`, newVersion);
			$app.save(record);
		} else unrecognized.push(v);
	});
	log({ unrecognized });
};

//#endregion
//#region src/lib/util/mkAudit.ts
const mkAudit = (log, app) => {
	return (event, note, context) => {
		log(`top of audit`);
		log(`AUDIT:${event}: ${note}`, JSON.stringify({ context }, null, 2));
		app.save(new Record(app.findCollectionByNameOrId("audit"), {
			event,
			note,
			context
		}));
	};
};

//#endregion
//#region src/lib/handlers/instance/model/AfterCreate_notify_discord.ts
const AfterCreate_notify_discord = (e) => {
	const audit = mkAudit(mkLog(`instances:create:discord:notify`), $app);
	const record = e.record;
	if (!record) return;
	const webhookUrl = process.env.DISCORD_STREAM_CHANNEL_URL;
	if (!webhookUrl) return;
	const version = record.get("version");
	try {
		$http.send({
			url: webhookUrl,
			method: "POST",
			body: JSON.stringify({ content: `Someone just created an app running PocketBase v${version}` }),
			headers: { "content-type": "application/json" },
			timeout: 5
		});
	} catch (e) {
		audit(`ERROR`, `Instance creation discord notify failed with ${e}`);
	}
};

//#endregion
//#region src/lib/handlers/instance/model/BeforeCreate_autoVacuum.ts
const BeforeCreate_autoVacuum = (e) => {
	e.record.set("autoVacuum", true);
};

//#endregion
//#region src/lib/handlers/instance/model/BeforeUpdate_cname.ts
const BeforeUpdate_cname = (e) => {
	const log = mkLog(`BeforeUpdate_cname`);
	const record = e.record;
	if (!record) return;
	const id = record.id;
	const newCname = record.get("cname").trim();
	if (newCname.length > 0) {
		const result = new DynamicModel({ id: "" });
		if ((() => {
			try {
				$app.db().newQuery(`select id from instances where cname='${newCname}' and id <> '${id}'`).one(result);
			} catch (e) {
				return false;
			}
			return true;
		})()) {
			const msg = `[ERROR] [${id}] Custom domain ${newCname} already in use.`;
			log(`${msg}`);
			throw new BadRequestError(msg);
		}
	}
};

//#endregion
//#region src/lib/handlers/instance/model/BeforeUpdate_version.ts
const BeforeUpdate_version = (e) => {
	const log = mkLog(`BeforeUpdate_version`);
	const record = e.record;
	if (!record) return;
	const version = record.get("version");
	if (version === record.original().get("version")) return;
	const versions = listVersions();
	if (!versions.length) return;
	if (!versions.includes(version)) {
		const msg = `Invalid version ${version}. Version must be one of: ${versions.join(", ")}`;
		log(`[ERROR] ${msg}`);
		throw new BadRequestError(msg);
	}
};

//#endregion
//#region src/lib/util/mkNotifier.ts
const mkNotifier = (log, app) => (channel, template, user_id, context = {}) => {
	log({
		channel,
		template,
		user_id
	});
	const emailTemplate = app.findFirstRecordByData("message_templates", `slug`, template);
	log(`got email template`, emailTemplate);
	if (!emailTemplate) throw new Error(`Template ${template} not found`);
	const templateVars = {
		user_id,
		...context
	};
	const emailNotification = new Record(app.findCollectionByNameOrId("notifications"), {
		user: user_id,
		channel,
		message_template: emailTemplate.id,
		message_template_vars: templateVars,
		payload: templateVars
	});
	log(`built notification record`, emailNotification);
	app.save(emailNotification);
};

//#endregion
//#region src/lib/handlers/lemon/api/HandleLemonSqueezySale.ts
const HandleLemonSqueezySale = (e) => {
	const log = mkLog(`ls`);
	const audit = mkAudit(log, $app);
	const context = {};
	log(`Top of ls`);
	try {
		context.secret = process.env.LS_WEBHOOK_SECRET;
		if (!context.secret) throw new Error(`No secret`);
		log(`Secret`, context.secret);
		context.raw = readerToString(e.request.body);
		context.body_hash = $security.hs256(context.raw, context.secret);
		log(`Body hash`, context.body_hash);
		context.xsignature_header = e.request.header.get("X-Signature");
		log(`Signature`, context.xsignature_header);
		if (context.xsignature_header == void 0 || !$security.equal(context.body_hash, context.xsignature_header)) throw new BadRequestError(`Invalid signature`);
		log(`Signature verified`);
		context.data = JSON.parse(context.raw);
		log(`payload`, JSON.stringify(context.data, null, 2));
		context.type = context.data?.data?.type;
		if (!context.type) throw new Error(`No type`);
		else log(`type ok`, context.type);
		context.event_name = context.data?.meta?.event_name;
		if (!context.event_name) throw new Error(`No event name`);
		else log(`event name ok`, context.event_name);
		context.user_id = context.data?.meta?.custom_data?.user_id;
		if (!context.user_id) throw new Error(`No user ID`);
		else log(`user ID ok`, context.user_id);
		context.product_id = context.data?.data?.attributes?.first_order_item?.product_id || context.data?.data?.attributes?.product_id || 0;
		if (!context.product_id) throw new Error(`No product ID`);
		else log(`product ID ok`, context.product_id);
		context.product_name = context.data?.data?.attributes?.first_order_item?.product_name || context.data?.data?.attributes?.product_name || "";
		log(`product name ok`, context.product_name);
		context.variant_id = context.data?.data?.attributes?.first_order_item?.variant_id || context.data?.data?.attributes?.variant_id || 0;
		if (!context.variant_id) throw new Error(`No variant ID`);
		else log(`variant ID ok`, context.variant_id);
		context.variant_name = context.data?.data?.attributes?.first_order_item?.variant_name || context.data?.data?.attributes?.variant_name || "";
		log(`variant name ok`, context.variant_name);
		context.quantity = context.data?.data?.attributes?.first_order_item?.quantity || 0;
		log(`quantity ok`, context.quantity);
		const FLOUNDER_ANNUAL_PV_ID = `367781-200790`;
		const FLOUNDER_LIFETIME_PV_ID = `306534-441845`;
		const PRO_MONTHLY_PV_ID = `159790-200788`;
		const PRO_ANNUAL_PV_ID = `159791-200789`;
		const FOUNDER_ANNUAL_PV_ID = `159792-200790`;
		const PAYWALL_INSTANCE_MONTHLY_PV_ID = `424532-651625`;
		const PAYWALL_PRO_MONTHLY_PV_ID = `424532-651629`;
		const PAYWALL_PRO_ANNUAL_PV_ID = `424532-651634`;
		const PAYWALL_FLOUNDER_PV_ID = `424532-651627`;
		const pv_id = `${context.product_id}-${context.variant_id}`;
		if (![
			FLOUNDER_ANNUAL_PV_ID,
			FLOUNDER_LIFETIME_PV_ID,
			PRO_MONTHLY_PV_ID,
			PRO_ANNUAL_PV_ID,
			FOUNDER_ANNUAL_PV_ID,
			PAYWALL_INSTANCE_MONTHLY_PV_ID,
			PAYWALL_PRO_MONTHLY_PV_ID,
			PAYWALL_PRO_ANNUAL_PV_ID,
			PAYWALL_FLOUNDER_PV_ID
		].includes(pv_id)) throw new Error(`Product and variant not found: ${pv_id}`);
		const userRec = (() => {
			try {
				return $app.findFirstRecordByData("users", "id", context.user_id);
			} catch (e) {
				throw new Error(`User ${context.user_id} not found`);
			}
		})();
		log(`user record ok`, userRec);
		const event_handler = {
			order_created: () => {
				signup_finalizer();
			},
			order_refunded: () => {
				signup_canceller();
			},
			subscription_expired: () => {
				signup_canceller();
			},
			subscription_payment_refunded: () => {
				signup_canceller();
			}
		}[context.event_name];
		if (!event_handler) throw new Error(`Unsupported event: ${context.event_name}`);
		else log(`event handler ok`, event_handler);
		const product_handler = {
			[FOUNDER_ANNUAL_PV_ID]: () => {
				userRec.set(`subscription`, `founder`);
				userRec.set(`subscription_interval`, `year`);
				userRec.set(`subscription_quantity`, 2147483647);
			},
			[PRO_ANNUAL_PV_ID]: () => {
				userRec.set(`subscription`, `premium`);
				userRec.set(`subscription_interval`, `year`);
				userRec.set(`subscription_quantity`, 250);
			},
			[PRO_MONTHLY_PV_ID]: () => {
				userRec.set(`subscription`, `premium`);
				userRec.set(`subscription_interval`, `month`);
				userRec.set(`subscription_quantity`, 250);
			},
			[FLOUNDER_LIFETIME_PV_ID]: () => {
				userRec.set(`subscription`, `flounder`);
				userRec.set(`subscription_interval`, `life`);
				userRec.set(`subscription_quantity`, 250);
			},
			[FLOUNDER_ANNUAL_PV_ID]: () => {
				userRec.set(`subscription`, `flounder`);
				userRec.set(`subscription_interval`, `year`);
				userRec.set(`subscription_quantity`, 250);
			},
			[PAYWALL_INSTANCE_MONTHLY_PV_ID]: () => {
				userRec.set(`subscription`, `premium`);
				userRec.set(`subscription_interval`, `month`);
				userRec.set(`subscription_quantity`, context.quantity);
			},
			[PAYWALL_PRO_MONTHLY_PV_ID]: () => {
				userRec.set(`subscription`, `premium`);
				userRec.set(`subscription_interval`, `month`);
				userRec.set(`subscription_quantity`, 250);
			},
			[PAYWALL_PRO_ANNUAL_PV_ID]: () => {
				userRec.set(`subscription`, `premium`);
				userRec.set(`subscription_interval`, `year`);
				userRec.set(`subscription_quantity`, 250);
			},
			[PAYWALL_FLOUNDER_PV_ID]: () => {
				userRec.set(`subscription`, `flounder`);
				userRec.set(`subscription_interval`, `life`);
				userRec.set(`subscription_quantity`, 250);
			}
		}[pv_id];
		if (!product_handler) throw new Error(`No product handler for ${pv_id}`);
		else log(`product handler ok`, pv_id);
		const signup_finalizer = () => {
			product_handler();
			$app.save(userRec);
			log(`saved user`);
			const notify = mkNotifier(log, $app);
			const { user_id } = context;
			if (!user_id) throw new Error(`User ID expected here`);
			notify(`lemonbot`, `lemon_order_discord`, user_id, context);
			log(`saved discord notice`);
			audit(`LS`, `Signup processed.`, context);
		};
		const signup_canceller = () => {
			if (userRec.get(`subscription`) !== `premium`) return;
			const currentQuantity = userRec.get(`subscription_quantity`);
			const newQuantity = Math.max(currentQuantity - (context.quantity || 1), 0);
			userRec.set(`subscription_quantity`, newQuantity);
			if (newQuantity === 0) {
				userRec.set(`subscription`, `free`);
				userRec.set(`subscription_interval`, ``);
			}
			$app.save(userRec);
			log(`saved user`);
			audit(`LS`, `Signup cancelled.`, context);
		};
		event_handler();
		return e.json(200, { status: "ok" });
	} catch (err) {
		audit(`LS_ERR`, `${err}`, context);
		return e.json(500, {
			status: `error`,
			error: `${err}`
		});
	}
};

//#endregion
//#region src/lib/handlers/mail/api/HandleMailSend.ts
const HandleMailSend = (e) => {
	const log = mkLog(`mail`);
	let data = new DynamicModel({
		to: "",
		subject: "",
		body: ""
	});
	log(`before bind`);
	e.bindBody(data);
	log(`after bind`);
	data = JSON.parse(JSON.stringify(data));
	log(`bind parsed`, JSON.stringify(data));
	const { to, subject, body } = data;
	try {
		const skipReason = mailRecipientSkipReason($app.findFirstRecordByData("users", "email", to));
		if (skipReason) {
			log(`skipped ${to}: ${skipReason}`);
			return e.json(200, {
				status: "skipped",
				reason: skipReason
			});
		}
	} catch (_e) {
		log(`no user record for ${to}, sending anyway`);
	}
	const email = new MailerMessage({
		from: {
			address: $app.settings().meta.senderAddress,
			name: $app.settings().meta.senderName
		},
		to: [{ address: to }],
		bcc: [process.env.TEST_EMAIL].filter((e) => !!e).map((e) => ({ address: e })),
		subject,
		html: body
	});
	$app.newMailClient().send(email);
	log(`Sent to ${to}`);
	return e.json(200, { status: "ok" });
};

//#endregion
//#region src/lib/handlers/meta/boot/HandleMetaUpdateAtBoot.ts
const HandleMetaUpdateAtBoot = (_e) => {
	const log = mkLog("HandleMetaUpdateAtBoot");
	log(`At top of HandleMetaUpdateAtBoot`);
	log(`app URL`, process.env.APP_URL);
	const settings = $app.settings();
	settings.meta = {
		...settings.meta,
		appUrl: process.env.APP_URL || settings.meta.appUrl,
		verificationTemplate: {
			...settings.meta.verificationTemplate,
			actionUrl: `{APP_URL}/login/confirm-account/{TOKEN}`
		},
		resetPasswordTemplate: {
			...settings.meta.resetPasswordTemplate,
			actionUrl: `{APP_URL}/login/password-reset/confirm/{TOKEN}`
		},
		confirmEmailChangeTemplate: {
			...settings.meta.confirmEmailChangeTemplate,
			actionUrl: `{APP_URL}/login/confirm-email-change/{TOKEN}`
		}
	};
	log(`Saving settings`);
	$app.save(settings);
	try {
		applyOperatorMailSettings(readOperatorSettings());
	} catch (error) {
		log(`Could not apply operator mail settings`, `${error}`);
	}
	log(`Saved settings`);
};

//#endregion
//#region src/lib/handlers/mirror/lib/buildMirrorDump.ts
const exportRecord = (record) => record.publicExport();
const buildMirrorDump = (app) => {
	return {
		users: app.findRecordsByFilter(`users`, `verified = true`).filter((r) => !!r).map(exportRecord),
		instances: app.findAllRecords(`instances`, $dbx.exp(`instances.uid in (select id from users where verified = 1)`)).filter((r) => !!r).map(exportRecord)
	};
};

//#endregion
//#region src/lib/handlers/mirror/api/HandleMirrorData.ts
const HandleMirrorData = (e) => {
	return e.json(200, buildMirrorDump($app));
};

//#endregion
//#region src/lib/handlers/mirror/lib/applyLiveInstances.ts
/** Save per row (not bulk SQL) so dashboard SSE clients get status updates. */
const applyLiveInstances = (app, liveInstances) => {
	let updated = 0;
	for (const live of liveInstances) {
		const id = live?.id?.trim();
		const status = live?.status?.trim();
		if (!id || !status) continue;
		if (status !== `starting` && status !== `running`) continue;
		let record;
		try {
			record = app.findRecordById(`instances`, id);
		} catch {
			continue;
		}
		if (!record.get(`power`)) continue;
		if (record.getString(`status`) === status) continue;
		record.set(`status`, status);
		app.save(record);
		updated++;
	}
	return updated;
};

//#endregion
//#region src/lib/handlers/mirror/api/HandleMirrorSync.ts
const HandleMirrorSync = (e) => {
	const { body } = e.requestInfo();
	if (body.resetIdle) resetInstancesIdle($app);
	const liveInstances = Array.isArray(body.instances) ? body.instances : [];
	const updated = applyLiveInstances($app, liveInstances);
	if (body.resetIdle || updated > 0) refreshAndBroadcastLivePlatformStats();
	return e.json(200, {
		...buildMirrorDump($app),
		updated
	});
};

//#endregion
//#region src/lib/util/mkNotificationProcessor.ts
const mkNotificationProcessor = (log, app, test = false) => (notificationRec) => {
	log({ notificationRec });
	const channel = notificationRec.getString(`channel`);
	app.expandRecord(notificationRec, ["message_template", "user"]);
	const messageTemplateRec = notificationRec.expandedOne("message_template");
	if (!messageTemplateRec) throw new Error(`Missing message template`);
	const userRec = notificationRec.expandedOne("user");
	if (!userRec) throw new Error(`Missing user record`);
	const vars = JSON.parse(notificationRec.getString(`message_template_vars`));
	const to = userRec.email();
	const subject = interpolateString(messageTemplateRec.getString(`subject`), vars);
	const html = interpolateString(messageTemplateRec.getString(`body_html`), vars);
	log({
		channel,
		messageTemplateRec,
		userRec,
		vars,
		to,
		subject,
		html
	});
	switch (channel) {
		case `email`:
			const skipReason = mailRecipientSkipReason(userRec);
			if (skipReason) {
				log(`skipped email to ${to}: ${skipReason}`);
				if (!test) {
					notificationRec.set(`delivered`, new DateTime());
					app.save(notificationRec);
				}
				break;
			}
			/** @type {Partial<mailer.Message_In>} */
			const msgArgs = {
				from: {
					address: $app.settings().meta.senderAddress,
					name: $app.settings().meta.senderName
				},
				to: [{ address: to }],
				bcc: [{ address: `pockethost+notifications@benallfree.com` }],
				subject,
				html
			};
			if (test) {
				msgArgs.to = [{ address: `ben@benallfree.com` }];
				msgArgs.subject = `***TEST ${to} *** ${msgArgs.subject}`;
			}
			log({ msgArgs });
			const msg = new MailerMessage(msgArgs);
			$app.newMailClient().send(msg);
			log(`email sent`);
			break;
		case `lemonbot`:
			const url = test ? process.env.DISCORD_TEST_CHANNEL_URL : process.env.DISCORD_STREAM_CHANNEL_URL;
			if (url) {
				const params = {
					url,
					method: "POST",
					body: JSON.stringify({ content: subject }),
					headers: { "content-type": "application/json" },
					timeout: 5
				};
				log(`sending discord message`, params);
				log(`discord sent`, $http.send(params));
			}
			break;
		default: throw new Error(`Unsupported channel: ${channel}`);
	}
	if (!test) {
		notificationRec.set(`delivered`, new DateTime());
		app.save(notificationRec);
	}
};

//#endregion
//#region src/lib/handlers/notify/api/HandleProcessSingleNotification.ts
const HandleProcessSingleNotification = (e) => {
	const log = mkLog(`process_single_notification`);
	log(`start`);
	const test = !!e.request.url.query().get(`test`);
	const processNotification = mkNotificationProcessor(log, $app, test);
	try {
		const notification = $app.findFirstRecordByData(`notifications`, `delivered`, ``);
		if (!notification) return e.json(200, `No notifications to send`);
		processNotification(notification);
	} catch (err) {
		return e.json(500, `${err}`);
	}
	return e.json(200, { status: "ok" });
};

//#endregion
//#region src/lib/handlers/notify/model/HandleProcessNotification.ts
const HandleProcessNotification = (e) => {
	const log = mkLog(`notification:sendImmediately`);
	const audit = mkAudit(log, $app);
	const processNotification = mkNotificationProcessor(log, $app, false);
	const notificationRec = e.record;
	if (!notificationRec) return;
	log({ notificationRec });
	try {
		$app.expandRecord(notificationRec, ["message_template"]);
		if (!notificationRec.expandedOne(`message_template`)) throw new Error(`Missing message template`);
		processNotification(notificationRec);
	} catch (e) {
		audit(`ERROR`, `${e}`, { notification: notificationRec.id });
	}
};

//#endregion
//#region src/lib/handlers/notify/model/HandleUserWelcomeMessage.ts
const HandleUserWelcomeMessage = (e) => {
	const newModel = e.record;
	if (!newModel) return;
	const oldModel = newModel.original();
	const log = mkLog(`user-welcome-msg`);
	const notify = mkNotifier(log, $app);
	const audit = mkAudit(log, $app);
	try {
		log({
			newModel,
			oldModel
		});
		const isVerified = newModel.getBool("verified");
		if (!isVerified) return;
		if (isVerified === oldModel.getBool(`verified`)) return;
		log(`user just became verified`);
		const uid = newModel.id;
		notify(`email`, `welcome`, uid);
		newModel.set(`welcome`, new DateTime());
	} catch (e) {
		audit(`ERROR`, `${e}`, { user: newModel.id });
	}
};

//#endregion
//#region src/lib/handlers/operatorAdmin/auth.ts
const requireOperatorAdmin = (e) => {
	const authRecord = e.auth;
	if (!authRecord) throw new UnauthorizedError("Authentification requise.");
	if (!authRecord.getBool("superAdmin")) throw new ForbiddenError("Acces superadmin requis.");
	return authRecord;
};

//#endregion
//#region src/lib/handlers/operatorAdmin/api.ts
const readJsonBody = (e) => {
	const rawBody = readerToString(e.request.body);
	if (!rawBody.trim()) return {};
	try {
		return JSON.parse(rawBody);
	} catch (error) {
		throw new BadRequestError(`Impossible d'analyser la requete JSON.`, error);
	}
};
const suggestUniqueAuthRecordUsername$1 = (collection, baseUsername) => {
	let username = baseUsername;
	for (let i = 0; i < 10; i++) {
		try {
			if ($app.countRecords(collection, $dbx.exp("LOWER([[username]])={:username}", { username: username.toLowerCase() })) === 0) break;
		} catch {}
		username = baseUsername + $security.randomStringWithAlphabet(3 + i, "123456789");
	}
	return username;
};
const userExists = (email, exceptId = "") => {
	try {
		return $app.findFirstRecordByData("users", "email", email).id !== exceptId;
	} catch {
		return false;
	}
};
const countInstancesForUser = (userId) => {
	try {
		return $app.countRecords("instances", $dbx.exp("uid = {:uid}", { uid: userId }));
	} catch {
		return 0;
	}
};
const serializeUser = (record) => ({
	id: record.id,
	email: record.getString("email"),
	username: record.getString("username"),
	name: record.getString("name"),
	verified: record.getBool("verified"),
	superAdmin: record.getBool("superAdmin"),
	subscription: record.getString("subscription") || "free",
	subscription_interval: record.getString("subscription_interval"),
	subscription_quantity: Number(record.get("subscription_quantity") || 0),
	suspension: record.getString("suspension"),
	created: record.getString("created"),
	updated: record.getString("updated"),
	instanceCount: countInstancesForUser(record.id)
});
const userTimestampMs = (record) => {
	const timestamp = Date.parse(record.getString("created") || record.getString("updated") || "");
	return Number.isFinite(timestamp) ? timestamp : 0;
};
const listOperatorUsers = () => $app.findRecordsByFilter("users", "id != \"\"", "").filter((record) => !!record).sort((a, b) => userTimestampMs(b) - userTimestampMs(a) || b.id.localeCompare(a.id)).map(serializeUser);
const ensureAnotherSuperAdminExists = (currentUserId) => {
	const superAdmins = $app.findRecordsByFilter("users", "superAdmin = true").filter((record) => !!record);
	if (superAdmins.length <= 1 && superAdmins[0]?.id === currentUserId) throw new BadRequestError("Impossible de retirer le dernier superadmin.");
};
const ensureValidServerTimezone = (timezoneName) => {
	const loadedName = new Timezone(timezoneName).string();
	if (![
		"UTC",
		"Etc/UTC",
		"Local"
	].includes(timezoneName) && loadedName === "UTC") throw new BadRequestError(`Fuseau horaire serveur invalide: ${timezoneName}. Exemple: Indian/Reunion.`);
};
const normalizeEmailAddress = (value, field = "Email") => {
	const email = `${value || ""}`.trim().toLowerCase();
	if (!email) throw new BadRequestError(`${field} obligatoire.`);
	if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) throw new BadRequestError(`${field} invalide.`);
	return email;
};
const mergeOperatorSettingsInput = (current, input) => {
	const backupS3 = {
		...current.backupS3,
		...input.backupS3 || {}
	};
	if (!`${backupS3.secretAccessKey || ""}`.trim()) backupS3.secretAccessKey = current.backupS3.secretAccessKey;
	const smtp = {
		...current.smtp,
		...input.smtp || {}
	};
	if (!`${smtp.password || ""}`.trim()) smtp.password = current.smtp.password;
	return normalizeOperatorSettings({
		...current,
		...input,
		backupS3,
		smtp
	});
};
const sendOperatorSMTPTest = (settings, to) => {
	if (!settings.smtp.enabled) throw new BadRequestError("SMTP est desactive.");
	if (!settings.smtp.host) throw new BadRequestError("Hote SMTP obligatoire.");
	if (!settings.smtp.port) throw new BadRequestError("Port SMTP obligatoire.");
	if (!settings.smtp.senderAddress) throw new BadRequestError("Adresse expediteur obligatoire.");
	const message = new MailerMessage({
		from: {
			address: settings.smtp.senderAddress,
			name: settings.smtp.senderName || "Gestion PocketBase"
		},
		to: [{ address: to }],
		subject: "Test SMTP - Gestion PocketBase",
		text: `Ce message confirme que la configuration SMTP de Gestion PocketBase fonctionne.`,
		html: `<p>Ce message confirme que la configuration SMTP de Gestion PocketBase fonctionne.</p>`
	});
	$app.newMailClient().send(message);
	return {
		to,
		host: settings.smtp.host,
		port: settings.smtp.port
	};
};
const HandleOperatorAdminOverview = (e) => {
	requireOperatorAdmin(e);
	const users = listOperatorUsers();
	const totalInstances = $app.countRecords("instances");
	return e.json(200, {
		settings: serializeOperatorSettings(readOperatorSettings()),
		users,
		stats: {
			totalUsers: users.length,
			verifiedUsers: users.filter((user) => user.verified).length,
			superAdmins: users.filter((user) => user.superAdmin).length,
			totalInstances,
			suspendedUsers: users.filter((user) => !!user.suspension).length
		}
	});
};
const HandleOperatorAdminCreateUser = (e) => {
	const log = mkLog("operator-admin:create-user");
	requireOperatorAdmin(e);
	const settings = readOperatorSettings();
	const body = readJsonBody(e);
	const email = `${body.email || ""}`.trim().toLowerCase();
	const password = `${body.password || ""}`.trim();
	if (!email) throw new BadRequestError("L'email est obligatoire.");
	if (userExists(email)) throw new BadRequestError("Ce compte existe deja.");
	if (password.length < 8) throw new BadRequestError("Le mot de passe doit contenir au moins 8 caracteres.");
	const collection = $app.findCollectionByNameOrId("users");
	const record = new Record(collection);
	const username = suggestUniqueAuthRecordUsername$1("users", "user" + $security.randomStringWithAlphabet(5, "123456789"));
	record.set("username", username);
	record.set("email", email);
	record.set("subscription", body.subscription || settings.defaultSubscription);
	record.set("subscription_quantity", body.subscription_quantity ?? settings.defaultUserQuota);
	record.set("verified", body.verified ?? settings.autoVerifyUsers);
	record.set("superAdmin", !!body.superAdmin);
	record.set("suspension", `${body.suspension || ""}`.trim());
	record.setPassword(password);
	$app.save(record);
	log(`created ${email}`);
	if (!record.getBool("verified")) $mails.sendRecordVerification($app, record);
	return e.json(200, { user: serializeUser(record) });
};
const HandleOperatorAdminUpdateUser = (e) => {
	requireOperatorAdmin(e);
	const id = e.request.pathValue("id");
	if (!id) throw new BadRequestError("L'identifiant utilisateur est obligatoire.");
	const body = readJsonBody(e);
	const record = $app.findRecordById("users", id);
	if (typeof body.email === "string") {
		const email = body.email.trim().toLowerCase();
		if (!email) throw new BadRequestError("L'email est obligatoire.");
		if (userExists(email, id)) throw new BadRequestError("Cet email est deja utilise.");
		record.set("email", email);
	}
	if (typeof body.password === "string" && body.password.trim()) {
		const password = body.password.trim();
		if (password.length < 8) throw new BadRequestError("Le mot de passe doit contenir au moins 8 caracteres.");
		record.setPassword(password);
	}
	if (typeof body.verified === "boolean") record.set("verified", body.verified);
	if (typeof body.subscription === "string") record.set("subscription", body.subscription);
	if (typeof body.subscription_quantity !== "undefined") record.set("subscription_quantity", Math.max(0, Math.floor(Number(body.subscription_quantity) || 0)));
	if (typeof body.suspension === "string") record.set("suspension", body.suspension.trim());
	if (typeof body.superAdmin === "boolean") {
		if (!body.superAdmin && record.getBool("superAdmin")) ensureAnotherSuperAdminExists(record.id);
		record.set("superAdmin", body.superAdmin);
	}
	$app.save(record);
	return e.json(200, { user: serializeUser(record) });
};
const HandleOperatorAdminUpdateSettings = (e) => {
	requireOperatorAdmin(e);
	const nextSettings = mergeOperatorSettingsInput(readOperatorSettings(), readJsonBody(e));
	ensureValidServerTimezone(nextSettings.serverTimezone);
	const settings = writeOperatorSettings(nextSettings);
	ReconcileBackupPolicyCrons();
	return e.json(200, { settings: serializeOperatorSettings(settings) });
};
const HandleOperatorAdminTestBackupS3 = (e) => {
	requireOperatorAdmin(e);
	const result = TestBackupS3Config(mergeOperatorSettingsInput(readOperatorSettings(), readJsonBody(e)));
	return e.json(200, { test: {
		...result,
		message: `Connexion S3/R2 valide pour ${result.bucket}.`
	} });
};
const HandleOperatorAdminTestSMTP = (e) => {
	requireOperatorAdmin(e);
	const current = readOperatorSettings();
	const body = readJsonBody(e);
	const settings = mergeOperatorSettingsInput(current, body);
	const target = normalizeEmailAddress(body.testEmail || settings.supportEmail || settings.smtp.senderAddress, "Email de test");
	const savedSettings = writeOperatorSettings(settings);
	const result = sendOperatorSMTPTest(savedSettings, target);
	return e.json(200, {
		settings: serializeOperatorSettings(savedSettings),
		test: {
			...result,
			message: `Email de test envoye a ${result.to}.`
		}
	});
};
const HandleOperatorAdminDiskCleanupPreview = (e) => {
	requireOperatorAdmin(e);
	return e.json(200, { cleanup: scanOrphanInstanceStorage(false) });
};
const HandleOperatorAdminDiskCleanupRun = (e) => {
	requireOperatorAdmin(e);
	return e.json(200, { cleanup: scanOrphanInstanceStorage(true) });
};

//#endregion
//#region src/lib/handlers/outpost/api/HandleOutpostUnsubscribe.ts
const HandleOutpostUnsubscribe = (e) => {
	const audit = mkAudit(mkLog(`unsubscribe`), $app);
	const id = e.request.url.query().get("e");
	try {
		const record = $app.findRecordById("users", id);
		record.set(`unsubscribe`, true);
		$app.save(record);
		const email = record.getString("email");
		audit("UNSUBSCRIBE", "", {
			email,
			user: id
		});
		$app.newMailClient().send(new MailerMessage({
			from: {
				address: $app.settings().meta.senderAddress,
				name: $app.settings().meta.senderName
			},
			to: [{ address: `ben@benallfree.com` }],
			subject: `UNSUBSCRIBE ${email}`
		}));
		return e.html(200, `<p>${email} has been unsubscribed.`);
	} catch (_err) {
		audit("UNSUBSCRIBE_ERR", `User ${id} not found`);
		return e.html(200, `<p>Vous êtes déjà désabonné.`);
	}
};

//#endregion
//#region src/lib/handlers/signup/error.ts
const error = (fieldName, slug, description, extra) => new ApiError(500, description, {
	[fieldName]: new ValidationError(slug, description),
	...extra
});

//#endregion
//#region src/lib/handlers/signup/isAvailable.ts
const isAvailable = (slug) => {
	try {
		$app.findFirstRecordByData("instances", "subdomain", slug);
		return false;
	} catch {
		return true;
	}
};

//#endregion
//#region src/lib/handlers/signup/random-words/wordList.ts
const wordList = [
	"ability",
	"able",
	"aboard",
	"about",
	"above",
	"accept",
	"accident",
	"according",
	"account",
	"accurate",
	"acres",
	"across",
	"act",
	"action",
	"active",
	"activity",
	"actual",
	"actually",
	"add",
	"addition",
	"additional",
	"adjective",
	"adult",
	"adventure",
	"advice",
	"affect",
	"afraid",
	"after",
	"afternoon",
	"again",
	"against",
	"age",
	"ago",
	"agree",
	"ahead",
	"aid",
	"air",
	"airplane",
	"alike",
	"alive",
	"all",
	"allow",
	"almost",
	"alone",
	"along",
	"aloud",
	"alphabet",
	"already",
	"also",
	"although",
	"am",
	"among",
	"amount",
	"ancient",
	"angle",
	"angry",
	"animal",
	"announced",
	"another",
	"answer",
	"ants",
	"any",
	"anybody",
	"anyone",
	"anything",
	"anyway",
	"anywhere",
	"apart",
	"apartment",
	"appearance",
	"apple",
	"applied",
	"appropriate",
	"are",
	"area",
	"arm",
	"army",
	"around",
	"arrange",
	"arrangement",
	"arrive",
	"arrow",
	"art",
	"article",
	"as",
	"aside",
	"ask",
	"asleep",
	"at",
	"ate",
	"atmosphere",
	"atom",
	"atomic",
	"attached",
	"attack",
	"attempt",
	"attention",
	"audience",
	"author",
	"automobile",
	"available",
	"average",
	"avoid",
	"aware",
	"away",
	"baby",
	"back",
	"bad",
	"badly",
	"bag",
	"balance",
	"ball",
	"balloon",
	"band",
	"bank",
	"bar",
	"bare",
	"bark",
	"barn",
	"base",
	"baseball",
	"basic",
	"basis",
	"basket",
	"bat",
	"battle",
	"be",
	"bean",
	"bear",
	"beat",
	"beautiful",
	"beauty",
	"became",
	"because",
	"become",
	"becoming",
	"bee",
	"been",
	"before",
	"began",
	"beginning",
	"begun",
	"behavior",
	"behind",
	"being",
	"believed",
	"bell",
	"belong",
	"below",
	"belt",
	"bend",
	"beneath",
	"bent",
	"beside",
	"best",
	"bet",
	"better",
	"between",
	"beyond",
	"bicycle",
	"bigger",
	"biggest",
	"bill",
	"birds",
	"birth",
	"birthday",
	"bit",
	"bite",
	"black",
	"blank",
	"blanket",
	"blew",
	"blind",
	"block",
	"blood",
	"blow",
	"blue",
	"board",
	"boat",
	"body",
	"bone",
	"book",
	"border",
	"born",
	"both",
	"bottle",
	"bottom",
	"bound",
	"bow",
	"bowl",
	"box",
	"boy",
	"brain",
	"branch",
	"brass",
	"brave",
	"bread",
	"break",
	"breakfast",
	"breath",
	"breathe",
	"breathing",
	"breeze",
	"brick",
	"bridge",
	"brief",
	"bright",
	"bring",
	"broad",
	"broke",
	"broken",
	"brother",
	"brought",
	"brown",
	"brush",
	"buffalo",
	"build",
	"building",
	"built",
	"buried",
	"burn",
	"burst",
	"bus",
	"bush",
	"business",
	"busy",
	"but",
	"butter",
	"buy",
	"by",
	"cabin",
	"cage",
	"cake",
	"call",
	"calm",
	"came",
	"camera",
	"camp",
	"can",
	"canal",
	"cannot",
	"cap",
	"capital",
	"captain",
	"captured",
	"car",
	"carbon",
	"card",
	"care",
	"careful",
	"carefully",
	"carried",
	"carry",
	"case",
	"cast",
	"castle",
	"cat",
	"catch",
	"cattle",
	"caught",
	"cause",
	"cave",
	"cell",
	"cent",
	"center",
	"central",
	"century",
	"certain",
	"certainly",
	"chain",
	"chair",
	"chamber",
	"chance",
	"change",
	"changing",
	"chapter",
	"character",
	"characteristic",
	"charge",
	"chart",
	"check",
	"cheese",
	"chemical",
	"chest",
	"chicken",
	"chief",
	"child",
	"children",
	"choice",
	"choose",
	"chose",
	"chosen",
	"church",
	"circle",
	"circus",
	"citizen",
	"city",
	"class",
	"classroom",
	"claws",
	"clay",
	"clean",
	"clear",
	"clearly",
	"climate",
	"climb",
	"clock",
	"close",
	"closely",
	"closer",
	"cloth",
	"clothes",
	"clothing",
	"cloud",
	"club",
	"coach",
	"coal",
	"coast",
	"coat",
	"coffee",
	"cold",
	"collect",
	"college",
	"colony",
	"color",
	"column",
	"combination",
	"combine",
	"come",
	"comfortable",
	"coming",
	"command",
	"common",
	"community",
	"company",
	"compare",
	"compass",
	"complete",
	"completely",
	"complex",
	"composed",
	"composition",
	"compound",
	"concerned",
	"condition",
	"congress",
	"connected",
	"consider",
	"consist",
	"consonant",
	"constantly",
	"construction",
	"contain",
	"continent",
	"continued",
	"contrast",
	"control",
	"conversation",
	"cook",
	"cookies",
	"cool",
	"copper",
	"copy",
	"corn",
	"corner",
	"correct",
	"correctly",
	"cost",
	"cotton",
	"could",
	"count",
	"country",
	"couple",
	"courage",
	"course",
	"court",
	"cover",
	"cow",
	"cowboy",
	"crack",
	"cream",
	"create",
	"creature",
	"crew",
	"crop",
	"cross",
	"crowd",
	"cry",
	"cup",
	"curious",
	"current",
	"curve",
	"customs",
	"cut",
	"cutting",
	"daily",
	"damage",
	"dance",
	"danger",
	"dangerous",
	"dark",
	"darkness",
	"date",
	"daughter",
	"dawn",
	"day",
	"dead",
	"deal",
	"dear",
	"death",
	"decide",
	"declared",
	"deep",
	"deeply",
	"deer",
	"definition",
	"degree",
	"depend",
	"depth",
	"describe",
	"desert",
	"design",
	"desk",
	"detail",
	"determine",
	"develop",
	"development",
	"diagram",
	"diameter",
	"did",
	"die",
	"differ",
	"difference",
	"different",
	"difficult",
	"difficulty",
	"dig",
	"dinner",
	"direct",
	"direction",
	"directly",
	"dirt",
	"dirty",
	"disappear",
	"discover",
	"discovery",
	"discuss",
	"discussion",
	"disease",
	"dish",
	"distance",
	"distant",
	"divide",
	"division",
	"do",
	"doctor",
	"does",
	"dog",
	"doing",
	"doll",
	"dollar",
	"done",
	"donkey",
	"door",
	"dot",
	"double",
	"doubt",
	"down",
	"dozen",
	"draw",
	"drawn",
	"dream",
	"dress",
	"drew",
	"dried",
	"drink",
	"drive",
	"driven",
	"driver",
	"driving",
	"drop",
	"dropped",
	"drove",
	"dry",
	"duck",
	"due",
	"dug",
	"dull",
	"during",
	"dust",
	"duty",
	"each",
	"eager",
	"ear",
	"earlier",
	"early",
	"earn",
	"earth",
	"easier",
	"easily",
	"east",
	"easy",
	"eat",
	"eaten",
	"edge",
	"education",
	"effect",
	"effort",
	"egg",
	"eight",
	"either",
	"electric",
	"electricity",
	"element",
	"elephant",
	"eleven",
	"else",
	"empty",
	"end",
	"enemy",
	"energy",
	"engine",
	"engineer",
	"enjoy",
	"enough",
	"enter",
	"entire",
	"entirely",
	"environment",
	"equal",
	"equally",
	"equator",
	"equipment",
	"escape",
	"especially",
	"essential",
	"establish",
	"even",
	"evening",
	"event",
	"eventually",
	"ever",
	"every",
	"everybody",
	"everyone",
	"everything",
	"everywhere",
	"evidence",
	"exact",
	"exactly",
	"examine",
	"example",
	"excellent",
	"except",
	"exchange",
	"excited",
	"excitement",
	"exciting",
	"exclaimed",
	"exercise",
	"exist",
	"expect",
	"experience",
	"experiment",
	"explain",
	"explanation",
	"explore",
	"express",
	"expression",
	"extra",
	"eye",
	"face",
	"facing",
	"fact",
	"factor",
	"factory",
	"failed",
	"fair",
	"fairly",
	"fall",
	"fallen",
	"familiar",
	"family",
	"famous",
	"far",
	"farm",
	"farmer",
	"farther",
	"fast",
	"fastened",
	"faster",
	"fat",
	"father",
	"favorite",
	"fear",
	"feathers",
	"feature",
	"fed",
	"feed",
	"feel",
	"feet",
	"fell",
	"fellow",
	"felt",
	"fence",
	"few",
	"fewer",
	"field",
	"fierce",
	"fifteen",
	"fifth",
	"fifty",
	"fight",
	"fighting",
	"figure",
	"fill",
	"film",
	"final",
	"finally",
	"find",
	"fine",
	"finest",
	"finger",
	"finish",
	"fire",
	"fireplace",
	"firm",
	"first",
	"fish",
	"five",
	"fix",
	"flag",
	"flame",
	"flat",
	"flew",
	"flies",
	"flight",
	"floating",
	"floor",
	"flow",
	"flower",
	"fly",
	"fog",
	"folks",
	"follow",
	"food",
	"foot",
	"football",
	"for",
	"force",
	"foreign",
	"forest",
	"forget",
	"forgot",
	"forgotten",
	"form",
	"former",
	"fort",
	"forth",
	"forty",
	"forward",
	"fought",
	"found",
	"four",
	"fourth",
	"fox",
	"frame",
	"free",
	"freedom",
	"frequently",
	"fresh",
	"friend",
	"friendly",
	"frighten",
	"frog",
	"from",
	"front",
	"frozen",
	"fruit",
	"fuel",
	"full",
	"fully",
	"fun",
	"function",
	"funny",
	"fur",
	"furniture",
	"further",
	"future",
	"gain",
	"game",
	"garage",
	"garden",
	"gas",
	"gasoline",
	"gate",
	"gather",
	"gave",
	"general",
	"generally",
	"gentle",
	"gently",
	"get",
	"getting",
	"giant",
	"gift",
	"girl",
	"give",
	"given",
	"giving",
	"glad",
	"glass",
	"globe",
	"go",
	"goes",
	"gold",
	"golden",
	"gone",
	"good",
	"goose",
	"got",
	"government",
	"grabbed",
	"grade",
	"gradually",
	"grain",
	"grandfather",
	"grandmother",
	"graph",
	"grass",
	"gravity",
	"gray",
	"great",
	"greater",
	"greatest",
	"greatly",
	"green",
	"grew",
	"ground",
	"group",
	"grow",
	"grown",
	"growth",
	"guard",
	"guess",
	"guide",
	"gulf",
	"gun",
	"habit",
	"had",
	"hair",
	"half",
	"halfway",
	"hall",
	"hand",
	"handle",
	"handsome",
	"hang",
	"happen",
	"happened",
	"happily",
	"happy",
	"harbor",
	"hard",
	"harder",
	"hardly",
	"has",
	"hat",
	"have",
	"having",
	"hay",
	"he",
	"headed",
	"heading",
	"health",
	"heard",
	"hearing",
	"heart",
	"heat",
	"heavy",
	"height",
	"held",
	"hello",
	"help",
	"helpful",
	"her",
	"herd",
	"here",
	"herself",
	"hidden",
	"hide",
	"high",
	"higher",
	"highest",
	"highway",
	"hill",
	"him",
	"himself",
	"his",
	"history",
	"hit",
	"hold",
	"hole",
	"hollow",
	"home",
	"honor",
	"hope",
	"horn",
	"horse",
	"hospital",
	"hot",
	"hour",
	"house",
	"how",
	"however",
	"huge",
	"human",
	"hundred",
	"hung",
	"hungry",
	"hunt",
	"hunter",
	"hurried",
	"hurry",
	"hurt",
	"husband",
	"ice",
	"idea",
	"identity",
	"if",
	"ill",
	"image",
	"imagine",
	"immediately",
	"importance",
	"important",
	"impossible",
	"improve",
	"in",
	"inch",
	"include",
	"including",
	"income",
	"increase",
	"indeed",
	"independent",
	"indicate",
	"individual",
	"industrial",
	"industry",
	"influence",
	"information",
	"inside",
	"instance",
	"instant",
	"instead",
	"instrument",
	"interest",
	"interior",
	"into",
	"introduced",
	"invented",
	"involved",
	"iron",
	"is",
	"island",
	"it",
	"its",
	"itself",
	"jack",
	"jar",
	"jet",
	"job",
	"join",
	"joined",
	"journey",
	"joy",
	"judge",
	"jump",
	"jungle",
	"just",
	"keep",
	"kept",
	"key",
	"kids",
	"kill",
	"kind",
	"kitchen",
	"knew",
	"knife",
	"know",
	"knowledge",
	"known",
	"label",
	"labor",
	"lack",
	"lady",
	"laid",
	"lake",
	"lamp",
	"land",
	"language",
	"large",
	"larger",
	"largest",
	"last",
	"late",
	"later",
	"laugh",
	"law",
	"lay",
	"layers",
	"lead",
	"leader",
	"leaf",
	"learn",
	"least",
	"leather",
	"leave",
	"leaving",
	"led",
	"left",
	"leg",
	"length",
	"lesson",
	"let",
	"letter",
	"level",
	"library",
	"lie",
	"life",
	"lift",
	"light",
	"like",
	"likely",
	"limited",
	"line",
	"lion",
	"lips",
	"liquid",
	"list",
	"listen",
	"little",
	"live",
	"living",
	"load",
	"local",
	"locate",
	"location",
	"log",
	"lonely",
	"long",
	"longer",
	"look",
	"loose",
	"lose",
	"loss",
	"lost",
	"lot",
	"loud",
	"love",
	"lovely",
	"low",
	"lower",
	"luck",
	"lucky",
	"lunch",
	"lungs",
	"lying",
	"machine",
	"machinery",
	"mad",
	"made",
	"magic",
	"magnet",
	"mail",
	"main",
	"mainly",
	"major",
	"make",
	"making",
	"man",
	"managed",
	"manner",
	"manufacturing",
	"many",
	"map",
	"mark",
	"market",
	"married",
	"mass",
	"massage",
	"master",
	"material",
	"mathematics",
	"matter",
	"may",
	"maybe",
	"me",
	"meal",
	"mean",
	"means",
	"meant",
	"measure",
	"meat",
	"medicine",
	"meet",
	"melted",
	"member",
	"memory",
	"men",
	"mental",
	"merely",
	"met",
	"metal",
	"method",
	"mice",
	"middle",
	"might",
	"mighty",
	"mile",
	"military",
	"milk",
	"mill",
	"mind",
	"mine",
	"minerals",
	"minute",
	"mirror",
	"missing",
	"mission",
	"mistake",
	"mix",
	"mixture",
	"model",
	"modern",
	"molecular",
	"moment",
	"money",
	"monkey",
	"month",
	"mood",
	"moon",
	"more",
	"morning",
	"most",
	"mostly",
	"mother",
	"motion",
	"motor",
	"mountain",
	"mouse",
	"mouth",
	"move",
	"movement",
	"movie",
	"moving",
	"mud",
	"muscle",
	"music",
	"musical",
	"must",
	"my",
	"myself",
	"mysterious",
	"nails",
	"name",
	"nation",
	"national",
	"native",
	"natural",
	"naturally",
	"nature",
	"near",
	"nearby",
	"nearer",
	"nearest",
	"nearly",
	"necessary",
	"neck",
	"needed",
	"needle",
	"needs",
	"negative",
	"neighbor",
	"neighborhood",
	"nervous",
	"nest",
	"never",
	"new",
	"news",
	"newspaper",
	"next",
	"nice",
	"night",
	"nine",
	"no",
	"nobody",
	"nodded",
	"noise",
	"none",
	"noon",
	"nor",
	"north",
	"nose",
	"not",
	"note",
	"noted",
	"nothing",
	"notice",
	"noun",
	"now",
	"number",
	"numeral",
	"nuts",
	"object",
	"observe",
	"obtain",
	"occasionally",
	"occur",
	"ocean",
	"of",
	"off",
	"offer",
	"office",
	"officer",
	"official",
	"oil",
	"old",
	"older",
	"oldest",
	"on",
	"once",
	"one",
	"only",
	"onto",
	"open",
	"operation",
	"opinion",
	"opportunity",
	"opposite",
	"or",
	"orange",
	"orbit",
	"order",
	"ordinary",
	"organization",
	"organized",
	"origin",
	"original",
	"other",
	"ought",
	"our",
	"ourselves",
	"out",
	"outer",
	"outline",
	"outside",
	"over",
	"own",
	"owner",
	"oxygen",
	"pack",
	"package",
	"page",
	"paid",
	"pain",
	"paint",
	"pair",
	"palace",
	"pale",
	"pan",
	"paper",
	"paragraph",
	"parallel",
	"parent",
	"park",
	"part",
	"particles",
	"particular",
	"particularly",
	"partly",
	"parts",
	"party",
	"pass",
	"passage",
	"past",
	"path",
	"pattern",
	"pay",
	"peace",
	"pen",
	"pencil",
	"people",
	"per",
	"percent",
	"perfect",
	"perfectly",
	"perhaps",
	"period",
	"person",
	"personal",
	"pet",
	"phrase",
	"physical",
	"piano",
	"pick",
	"picture",
	"pictured",
	"pie",
	"piece",
	"pig",
	"pile",
	"pilot",
	"pine",
	"pink",
	"pipe",
	"pitch",
	"place",
	"plain",
	"plan",
	"plane",
	"planet",
	"planned",
	"planning",
	"plant",
	"plastic",
	"plate",
	"plates",
	"play",
	"pleasant",
	"please",
	"pleasure",
	"plenty",
	"plural",
	"plus",
	"pocket",
	"poem",
	"poet",
	"poetry",
	"point",
	"pole",
	"police",
	"policeman",
	"political",
	"pond",
	"pony",
	"pool",
	"poor",
	"popular",
	"population",
	"porch",
	"port",
	"position",
	"positive",
	"possible",
	"possibly",
	"post",
	"pot",
	"potatoes",
	"pound",
	"pour",
	"powder",
	"power",
	"powerful",
	"practical",
	"practice",
	"prepare",
	"present",
	"president",
	"press",
	"pressure",
	"pretty",
	"prevent",
	"previous",
	"price",
	"pride",
	"primitive",
	"principal",
	"principle",
	"printed",
	"private",
	"prize",
	"probably",
	"problem",
	"process",
	"produce",
	"product",
	"production",
	"program",
	"progress",
	"promised",
	"proper",
	"properly",
	"property",
	"protection",
	"proud",
	"prove",
	"provide",
	"public",
	"pull",
	"pupil",
	"pure",
	"purple",
	"purpose",
	"push",
	"put",
	"putting",
	"quarter",
	"queen",
	"question",
	"quick",
	"quickly",
	"quiet",
	"quietly",
	"quite",
	"rabbit",
	"race",
	"radio",
	"railroad",
	"rain",
	"raise",
	"ran",
	"ranch",
	"range",
	"rapidly",
	"rate",
	"rather",
	"raw",
	"rays",
	"reach",
	"read",
	"reader",
	"ready",
	"real",
	"realize",
	"rear",
	"reason",
	"recall",
	"receive",
	"recent",
	"recently",
	"recognize",
	"record",
	"red",
	"refer",
	"refused",
	"region",
	"regular",
	"related",
	"relationship",
	"religious",
	"remain",
	"remarkable",
	"remember",
	"remove",
	"repeat",
	"replace",
	"replied",
	"report",
	"represent",
	"require",
	"research",
	"respect",
	"rest",
	"result",
	"return",
	"review",
	"rhyme",
	"rhythm",
	"rice",
	"rich",
	"ride",
	"riding",
	"right",
	"ring",
	"rise",
	"rising",
	"river",
	"road",
	"roar",
	"rock",
	"rocket",
	"rocky",
	"rod",
	"roll",
	"roof",
	"room",
	"root",
	"rope",
	"rose",
	"rough",
	"round",
	"route",
	"row",
	"rubbed",
	"rubber",
	"rule",
	"ruler",
	"run",
	"running",
	"rush",
	"sad",
	"saddle",
	"safe",
	"safety",
	"said",
	"sail",
	"sale",
	"salmon",
	"salt",
	"same",
	"sand",
	"sang",
	"sat",
	"satellites",
	"satisfied",
	"save",
	"saved",
	"saw",
	"say",
	"scale",
	"scared",
	"scene",
	"school",
	"science",
	"scientific",
	"scientist",
	"score",
	"screen",
	"sea",
	"search",
	"season",
	"seat",
	"second",
	"secret",
	"section",
	"see",
	"seed",
	"seeing",
	"seems",
	"seen",
	"seldom",
	"select",
	"selection",
	"sell",
	"send",
	"sense",
	"sent",
	"sentence",
	"separate",
	"series",
	"serious",
	"serve",
	"service",
	"sets",
	"setting",
	"settle",
	"settlers",
	"seven",
	"several",
	"shade",
	"shadow",
	"shake",
	"shaking",
	"shall",
	"shallow",
	"shape",
	"share",
	"sharp",
	"she",
	"sheep",
	"sheet",
	"shelf",
	"shells",
	"shelter",
	"shine",
	"shinning",
	"ship",
	"shirt",
	"shoe",
	"shoot",
	"shop",
	"shore",
	"short",
	"shorter",
	"shot",
	"should",
	"shoulder",
	"shout",
	"show",
	"shown",
	"shut",
	"sick",
	"sides",
	"sight",
	"sign",
	"signal",
	"silence",
	"silent",
	"silk",
	"silly",
	"silver",
	"similar",
	"simple",
	"simplest",
	"simply",
	"since",
	"sing",
	"single",
	"sink",
	"sister",
	"sit",
	"sitting",
	"situation",
	"six",
	"size",
	"skill",
	"skin",
	"sky",
	"slabs",
	"slave",
	"sleep",
	"slept",
	"slide",
	"slight",
	"slightly",
	"slip",
	"slipped",
	"slope",
	"slow",
	"slowly",
	"small",
	"smaller",
	"smallest",
	"smell",
	"smile",
	"smoke",
	"smooth",
	"snake",
	"snow",
	"so",
	"soap",
	"social",
	"society",
	"soft",
	"softly",
	"soil",
	"solar",
	"sold",
	"soldier",
	"solid",
	"solution",
	"solve",
	"some",
	"somebody",
	"somehow",
	"someone",
	"something",
	"sometime",
	"somewhere",
	"son",
	"song",
	"soon",
	"sort",
	"sound",
	"source",
	"south",
	"southern",
	"space",
	"speak",
	"special",
	"species",
	"specific",
	"speech",
	"speed",
	"spell",
	"spend",
	"spent",
	"spider",
	"spin",
	"spirit",
	"spite",
	"split",
	"spoken",
	"sport",
	"spread",
	"spring",
	"square",
	"stage",
	"stairs",
	"stand",
	"standard",
	"star",
	"stared",
	"start",
	"state",
	"statement",
	"station",
	"stay",
	"steady",
	"steam",
	"steel",
	"steep",
	"stems",
	"step",
	"stepped",
	"stick",
	"stiff",
	"still",
	"stock",
	"stomach",
	"stone",
	"stood",
	"stop",
	"stopped",
	"store",
	"storm",
	"story",
	"stove",
	"straight",
	"strange",
	"stranger",
	"straw",
	"stream",
	"street",
	"strength",
	"stretch",
	"strike",
	"string",
	"strip",
	"strong",
	"stronger",
	"struck",
	"structure",
	"struggle",
	"stuck",
	"student",
	"studied",
	"studying",
	"subject",
	"substance",
	"success",
	"successful",
	"such",
	"sudden",
	"suddenly",
	"sugar",
	"suggest",
	"suit",
	"sum",
	"summer",
	"sun",
	"sunlight",
	"supper",
	"supply",
	"support",
	"suppose",
	"sure",
	"surface",
	"surprise",
	"surrounded",
	"swam",
	"sweet",
	"swept",
	"swim",
	"swimming",
	"swing",
	"swung",
	"syllable",
	"symbol",
	"system",
	"table",
	"tail",
	"take",
	"taken",
	"tales",
	"talk",
	"tall",
	"tank",
	"tape",
	"task",
	"taste",
	"taught",
	"tax",
	"tea",
	"teach",
	"teacher",
	"team",
	"tears",
	"teeth",
	"telephone",
	"television",
	"tell",
	"temperature",
	"ten",
	"tent",
	"term",
	"terrible",
	"test",
	"than",
	"thank",
	"that",
	"thee",
	"them",
	"themselves",
	"then",
	"theory",
	"there",
	"therefore",
	"these",
	"they",
	"thick",
	"thin",
	"thing",
	"think",
	"third",
	"thirty",
	"this",
	"those",
	"thou",
	"though",
	"thought",
	"thousand",
	"thread",
	"three",
	"threw",
	"throat",
	"through",
	"throughout",
	"throw",
	"thrown",
	"thumb",
	"thus",
	"thy",
	"tide",
	"tie",
	"tight",
	"tightly",
	"till",
	"time",
	"tin",
	"tiny",
	"tip",
	"tired",
	"title",
	"to",
	"tobacco",
	"today",
	"together",
	"told",
	"tomorrow",
	"tone",
	"tongue",
	"tonight",
	"too",
	"took",
	"tool",
	"top",
	"topic",
	"torn",
	"total",
	"touch",
	"toward",
	"tower",
	"town",
	"toy",
	"trace",
	"track",
	"trade",
	"traffic",
	"trail",
	"train",
	"transportation",
	"trap",
	"travel",
	"treated",
	"tree",
	"triangle",
	"tribe",
	"trick",
	"tried",
	"trip",
	"troops",
	"tropical",
	"trouble",
	"truck",
	"trunk",
	"truth",
	"try",
	"tube",
	"tune",
	"turn",
	"twelve",
	"twenty",
	"twice",
	"two",
	"type",
	"typical",
	"uncle",
	"under",
	"underline",
	"understanding",
	"unhappy",
	"union",
	"unit",
	"universe",
	"unknown",
	"unless",
	"until",
	"unusual",
	"up",
	"upon",
	"upper",
	"upward",
	"us",
	"use",
	"useful",
	"using",
	"usual",
	"usually",
	"valley",
	"valuable",
	"value",
	"vapor",
	"variety",
	"various",
	"vast",
	"vegetable",
	"verb",
	"vertical",
	"very",
	"vessels",
	"victory",
	"view",
	"village",
	"visit",
	"visitor",
	"voice",
	"volume",
	"vote",
	"vowel",
	"voyage",
	"wagon",
	"wait",
	"walk",
	"wall",
	"want",
	"war",
	"warm",
	"warn",
	"was",
	"wash",
	"waste",
	"watch",
	"water",
	"wave",
	"way",
	"we",
	"weak",
	"wealth",
	"wear",
	"weather",
	"week",
	"weigh",
	"weight",
	"welcome",
	"well",
	"went",
	"were",
	"west",
	"western",
	"wet",
	"whale",
	"what",
	"whatever",
	"wheat",
	"wheel",
	"when",
	"whenever",
	"where",
	"wherever",
	"whether",
	"which",
	"while",
	"whispered",
	"whistle",
	"white",
	"who",
	"whole",
	"whom",
	"whose",
	"why",
	"wide",
	"widely",
	"wife",
	"wild",
	"will",
	"willing",
	"win",
	"wind",
	"window",
	"wing",
	"winter",
	"wire",
	"wise",
	"wish",
	"with",
	"within",
	"without",
	"wolf",
	"women",
	"won",
	"wonder",
	"wonderful",
	"wood",
	"wooden",
	"wool",
	"word",
	"wore",
	"work",
	"worker",
	"world",
	"worried",
	"worry",
	"worse",
	"worth",
	"would",
	"wrapped",
	"write",
	"writer",
	"writing",
	"written",
	"wrong",
	"wrote",
	"yard",
	"year",
	"yellow",
	"yes",
	"yesterday",
	"yet",
	"you",
	"young",
	"younger",
	"your",
	"yourself",
	"youth",
	"zero",
	"zebra",
	"zipper",
	"zoo",
	"zulu"
];

//#endregion
//#region src/lib/handlers/signup/random-words/index.ts
const shortestWordSize = wordList.reduce((shortestWord, currentWord) => currentWord.length < shortestWord.length ? currentWord : shortestWord).length;
const longestWordSize = wordList.reduce((longestWord, currentWord) => currentWord.length > longestWord.length ? currentWord : longestWord).length;
function generate(options) {
	const { minLength, maxLength, ...rest } = options || {};
	function word() {
		let min = typeof minLength !== "number" ? shortestWordSize : limitWordSize(minLength);
		const max = typeof maxLength !== "number" ? longestWordSize : limitWordSize(maxLength);
		if (min > max) min = max;
		let rightSize = false;
		let wordUsed;
		while (!rightSize) {
			wordUsed = generateRandomWord();
			rightSize = wordUsed.length <= max && wordUsed.length >= min;
		}
		return wordUsed;
	}
	function generateRandomWord() {
		return wordList[randInt(wordList.length)];
	}
	function limitWordSize(wordSize) {
		if (wordSize < shortestWordSize) wordSize = shortestWordSize;
		if (wordSize > longestWordSize) wordSize = longestWordSize;
		return wordSize;
	}
	function randInt(lessThan) {
		return Math.floor(Math.random() * lessThan);
	}
	if (options === void 0) return word();
	if (typeof options === "number") options = { exactly: options };
	else if (Object.keys(rest).length === 0) return word();
	if (options.exactly) {
		options.min = options.exactly;
		options.max = options.exactly;
	}
	if (typeof options.wordsPerString !== "number") options.wordsPerString = 1;
	if (typeof options.formatter !== "function") options.formatter = (word) => word;
	if (typeof options.separator !== "string") options.separator = " ";
	const total = options.min + randInt(options.max + 1 - options.min);
	let results = [];
	let token = "";
	let relativeIndex = 0;
	for (let i = 0; i < total * options.wordsPerString; i++) {
		if (relativeIndex === options.wordsPerString - 1) token += options.formatter(word(), relativeIndex);
		else token += options.formatter(word(), relativeIndex) + options.separator;
		relativeIndex++;
		if ((i + 1) % options.wordsPerString === 0) {
			results.push(token);
			token = "";
			relativeIndex = 0;
		}
	}
	if (typeof options.join === "string") results = results.join(options.join);
	return results;
}

//#endregion
//#region src/lib/handlers/signup/api/HandleSignupCheck.ts
const HandleSignupCheck = (e) => {
	const instanceName = (() => {
		const name = (e.request.url.query().get("name") || "").trim();
		if (name) {
			if (name.match(/^[a-z][a-z0-9-]{2,39}$/) === null) throw error(`instanceName`, `invalid`, `Le nom d'instance doit commencer par une lettre, contenir entre 3 et 40 caractères, et utiliser seulement a-z, 0-9 et le tiret (-).`);
			if (isAvailable(name)) return name;
			throw error(`instanceName`, `exists`, `Le nom d'instance ${name} n'est pas disponible.`);
		} else {
			let i = 0;
			while (true) {
				i++;
				if (i > 100) return +/* @__PURE__ */ new Date();
				const slug = generate(2).join(`-`);
				if (isAvailable(slug)) return slug;
			}
		}
	})();
	return e.json(200, { instanceName });
};

//#endregion
//#region src/lib/handlers/signup/api/HandleSignupConfirm.ts
const suggestUniqueAuthRecordUsername = (collection, baseUsername) => {
	let username = baseUsername;
	for (let i = 0; i < 10; i++) {
		try {
			if ($app.countRecords(collection, $dbx.exp("LOWER([[username]])={:username}", { username: username.toLowerCase() })) === 0) break;
		} catch {}
		username = baseUsername + $security.randomStringWithAlphabet(3 + i, "123456789");
	}
	return username;
};
const HandleSignupConfirm = (e) => {
	const settings = readOperatorSettings();
	if (!settings.publicSignupEnabled) throw new BadRequestError("La creation publique de compte est desactivee.");
	const parsed = (() => {
		const rawBody = readerToString(e.request.body);
		try {
			return JSON.parse(rawBody);
		} catch (e) {
			throw new BadRequestError(`Impossible d'analyser la requête JSON. Corps reçu : ${rawBody}`, e);
		}
	})();
	const email = parsed.email?.trim().toLowerCase();
	const password = parsed.password?.trim();
	const desiredInstanceName = parsed.instanceName?.trim();
	const version = parsed.version?.trim() || listVersions()[0];
	if (!email) throw error(`email`, "required", "L'email est obligatoire");
	if (!password) throw error(`password`, `required`, "Le mot de passe est obligatoire");
	if (!desiredInstanceName) throw error(`instanceName`, `required`, `Le nom de l'instance est obligatoire`);
	if ((() => {
		try {
			$app.findFirstRecordByData("users", "email", email);
			return true;
		} catch {
			return false;
		}
	})()) throw error(`email`, `exists`, `Ce compte utilisateur existe déjà. Essayez une réinitialisation du mot de passe.`);
	$app.runInTransaction((txApp) => {
		const usersCollection = $app.findCollectionByNameOrId("users");
		const instanceCollection = $app.findCollectionByNameOrId("instances");
		const user = new Record(usersCollection);
		try {
			const username = suggestUniqueAuthRecordUsername("users", "user" + $security.randomStringWithAlphabet(5, "123456789"));
			user.set("username", username);
			user.set("email", email);
			user.set("subscription", settings.defaultSubscription);
			user.set("subscription_quantity", settings.defaultUserQuota);
			if (settings.autoVerifyUsers) user.set("verified", true);
			user.setPassword(password);
			txApp.save(user);
		} catch (e) {
			throw error(`email`, `fail`, `Impossible de créer l'utilisateur : ${e}`);
		}
		try {
			const instance = new Record(instanceCollection);
			instance.set("subdomain", desiredInstanceName);
			instance.set("uid", user.get("id"));
			instance.set("status", "idle");
			instance.set("power", settings.defaultInstancePower);
			instance.set("syncAdmin", settings.defaultSyncAdmin);
			instance.set("autoVacuum", settings.defaultAutoVacuum);
			instance.set("dev", settings.defaultInstanceDevMode);
			instance.set("version", version);
			txApp.save(instance);
		} catch (e) {
			if (`${e}`.match(/ UNIQUE /)) throw error(`instanceName`, `exists`, `Ce nom d'instance vient d'être pris. Essayez-en un autre.`);
			throw error(`instanceName`, `fail`, `Impossible de créer l'instance : ${e}`);
		}
		if (!settings.autoVerifyUsers) $mails.sendRecordVerification($app, user);
	});
	return e.json(200, { status: "ok" });
};

//#endregion
//#region src/lib/handlers/sns/api/HandleSesError.ts
function isSnsSubscriptionConfirmationEvent(event) {
	return event.Type === "SubscriptionConfirmation";
}
function isSnsNotificationEvent(event) {
	return event.Type === "Notification";
}
function sesEventType(msg) {
	return String(msg.notificationType ?? msg.eventType ?? "");
}
const HandleSesError = (e) => {
	const log = mkLog(`sns`);
	const audit = mkAudit(log, $app);
	const processBounce = (emailAddress) => {
		log(`Processing bounce ${emailAddress}`);
		const extra = { email: emailAddress };
		try {
			const user = $app.findFirstRecordByData("users", "email", emailAddress);
			log(`user is`, user);
			extra.user = user.id;
			suppressUserEmail(user);
			$app.save(user);
			audit("PBOUNCE", `User ${emailAddress} has been disabled`, extra);
		} catch (e) {
			audit("PBOUNCE_ERR", `${e}`, extra);
		}
	};
	const processComplaint = (emailAddress) => {
		log(`Processing complaint ${emailAddress}`);
		const extra = { email: emailAddress };
		try {
			const user = $app.findFirstRecordByData("users", "email", emailAddress);
			log(`user is`, user);
			extra.user = user.id;
			suppressUserEmail(user);
			$app.save(user);
			audit("COMPLAINT", `User ${emailAddress} has been unsubscribed`, extra);
		} catch (e) {
			audit("COMPLAINT_ERR", `${emailAddress} is not in the system.`, extra);
		}
	};
	const raw = readerToString(e.request.body);
	const data = JSON.parse(raw);
	log(JSON.stringify(data, null, 2));
	if (isSnsSubscriptionConfirmationEvent(data)) {
		const url = data.SubscribeURL;
		log(url);
		$http.send({ url });
		return e.json(200, { status: "ok" });
	}
	if (!isSnsNotificationEvent(data)) {
		audit("SNS_ERR", `Unrecognized SNS envelope type ${data.Type}`, { raw });
		return e.json(200, { status: "ok" });
	}
	const msg = JSON.parse(data.Message);
	log(msg);
	const eventType = sesEventType(msg);
	if (eventType === "Bounce") {
		log(`Message is a bounce`);
		const bounce = msg.bounce;
		if (!bounce) {
			audit("SNS_ERR", "Bounce event missing bounce object", { raw });
			return e.json(200, { status: "ok" });
		}
		const { bounceType, bouncedRecipients } = bounce;
		if (bounceType === `Permanent`) {
			log(`Message is a permanent bounce`);
			bouncedRecipients.forEach((recipient) => {
				processBounce(recipient.emailAddress);
			});
		} else audit("SNS_ERR", `Unrecognized bounce type ${bounceType}`, { raw });
		return e.json(200, { status: "ok" });
	}
	if (eventType === "Complaint") {
		log(`Message is a Complaint`, msg);
		const complaint = msg.complaint;
		if (!complaint) {
			audit("SNS_ERR", "Complaint event missing complaint object", { raw });
			return e.json(200, { status: "ok" });
		}
		complaint.complainedRecipients.forEach((recipient) => {
			processComplaint(recipient.emailAddress);
		});
		return e.json(200, { status: "ok" });
	}
	audit("SNS_ERR", `Unrecognized SES event type ${eventType}`, { raw });
	return e.json(200, { status: "ok" });
};

//#endregion
//#region ../common/sshPublicKey.ts
/** JSVM-safe ssh-ed25519 public key parsing. Safe for pb_hooks (Goja) and Node/browser consumers. */
const ED25519_ALGO = "ssh-ed25519";
const ED25519_WIRE_KEY_LEN = 32;
const ED25519_WIRE_LEN = 51;
const readUint32BE = (bytes, offset) => {
	if (offset + 4 > bytes.length) throw new Error("Invalid public key encoding.");
	return (bytes[offset] << 24 | bytes[offset + 1] << 16 | bytes[offset + 2] << 8 | bytes[offset + 3]) >>> 0;
};
const readSshString = (bytes, offset) => {
	const length = readUint32BE(bytes, offset);
	offset += 4;
	if (length < 0 || offset + length > bytes.length) throw new Error("Invalid public key encoding.");
	return {
		value: bytes.slice(offset, offset + length),
		nextOffset: offset + length
	};
};
const bytesToAscii = (bytes) => {
	let out = "";
	for (let i = 0; i < bytes.length; i++) out += String.fromCharCode(bytes[i]);
	return out;
};
const decodeBase64 = (value) => {
	const normalized = value.replace(/[\s\r\n]+/g, "");
	if (!normalized || normalized.length % 4 === 1 || !/^[A-Za-z0-9+/]+=*$/.test(normalized)) throw new Error("Public key base64 is invalid.");
	const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
	const bytes = [];
	let buffer = 0;
	let bits = 0;
	for (const char of normalized.replace(/=+$/, "")) {
		const index = alphabet.indexOf(char);
		if (index === -1) throw new Error("Public key base64 is invalid.");
		buffer = buffer << 6 | index;
		bits += 6;
		if (bits >= 8) {
			bits -= 8;
			bytes.push(buffer >> bits & 255);
		}
	}
	return new Uint8Array(bytes);
};
const validateWire = (wire) => {
	if (wire.length !== ED25519_WIRE_LEN) throw new Error("Invalid Ed25519 public key length.");
	let offset = 0;
	const algo = readSshString(wire, offset);
	offset = algo.nextOffset;
	if (bytesToAscii(algo.value) !== ED25519_ALGO) throw new Error("Public key algorithm must be ssh-ed25519.");
	const key = readSshString(wire, offset);
	if (key.value.length !== ED25519_WIRE_KEY_LEN) throw new Error("Invalid Ed25519 public key length.");
	if (key.nextOffset !== wire.length) throw new Error("Invalid public key encoding.");
};
const parseSshEd25519PublicKey = (input) => {
	const trimmed = input.trim();
	if (!trimmed) throw new Error("Public key is required.");
	const lines = trimmed.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
	if (lines.length > 1) throw new Error("Paste a single public key line only.");
	const parts = (lines[0] ?? "").split(/\s+/).filter(Boolean);
	if (parts.length < 2) throw new Error("Public key must look like: ssh-ed25519 AAAA… comment");
	const algo = parts[0];
	const keyData = parts[1];
	if (algo !== ED25519_ALGO) throw new Error("Only ssh-ed25519 public keys are supported.");
	const wire = decodeBase64(keyData);
	validateWire(wire);
	const comment = parts.slice(2).join(" ");
	return {
		normalized: comment ? `${ED25519_ALGO} ${keyData} ${comment}` : `${ED25519_ALGO} ${keyData}`,
		wire
	};
};

//#endregion
//#region src/lib/handlers/sshKeys/model/validateSshKey.ts
const validateSshKeyRecord = (record, authId) => {
	const log = mkLog(`ssh-keys`);
	let parsed;
	try {
		parsed = parseSshEd25519PublicKey(record.getString("public_key"));
	} catch (error) {
		throw new BadRequestError(`${error}`);
	}
	record.set("public_key", parsed.normalized);
	if (!record.getString("fingerprint").trim().startsWith("SHA256:")) throw new BadRequestError("Empreinte invalide.");
	const allInstances = record.getBool("all_instances");
	const instanceIds = record.getStringSlice("instances") || [];
	if (!allInstances && instanceIds.length === 0) throw new BadRequestError("Sélectionnez au moins une instance ou choisissez toutes les instances.");
	if (!allInstances) for (const instanceId of instanceIds) {
		const instance = $app.findRecordById("instances", instanceId);
		if (instance.getString("uid") !== authId) {
			log({
				instanceId,
				authId,
				uid: instance.getString("uid")
			});
			throw new BadRequestError("Une ou plusieurs instances sélectionnées ne vous appartiennent pas.");
		}
	}
	if (allInstances) record.set("instances", []);
};
const BeforeCreate_ssh_keys = (e) => {
	const record = e.record;
	if (!record) throw new BadRequestError("Missing record.");
	const authRecord = e.auth;
	if (!authRecord) throw new BadRequestError("Authentification requise.");
	record.set("user", authRecord.id);
	validateSshKeyRecord(record, authRecord.id);
};
const BeforeUpdate_ssh_keys = (e) => {
	const record = e.record;
	if (!record) throw new BadRequestError("Missing record.");
	const authRecord = e.auth;
	if (!authRecord) throw new BadRequestError("Authentification requise.");
	if (record.getString("user") !== authRecord.id) throw new ForbiddenError("Vous ne pouvez modifier que vos propres clés SSH.");
	validateSshKeyRecord(record, authRecord.id);
};

//#endregion
//#region src/lib/handlers/stats/lib/refreshPublicStats.ts
const mkPublicStatsPath = () => `${$app.dataDir()}/stats.json`;
const countTableRows = (tableName) => {
	try {
		const result = new DynamicModel({ total: 0 });
		$app.db().newQuery(`SELECT COUNT(*) as total FROM ${tableName}`).one(result);
		return Number(result.total || 0);
	} catch {
		return 0;
	}
};
const refreshPublicStats = () => {
	const log = mkLog("refreshPublicStats");
	const stats = {
		updatedAt: (/* @__PURE__ */ new Date()).toISOString(),
		developers: countTableRows("users"),
		instances: countTableRows("instances")
	};
	$os.writeFile(mkPublicStatsPath(), JSON.stringify(stats), 420);
	log(`Wrote stats.json`, stats);
	return stats;
};

//#endregion
//#region src/lib/handlers/stats/api/HandleStatsRequest.ts
/** Public aggregate platform stats (hourly cron + on-demand refresh). */
const HandleStatsRequest = (e) => {
	const readStats = () => {
		const raw = $os.readFile(mkPublicStatsPath());
		return JSON.parse(typeof raw === "string" ? raw : String(raw));
	};
	try {
		return e.json(200, readStats());
	} catch {
		return e.json(200, refreshPublicStats());
	}
};

//#endregion
//#region src/lib/handlers/stats/boot/HandleStatsRefreshAtBoot.ts
const HandleStatsRefreshAtBoot = (_e) => {
	refreshPublicStats();
};

//#endregion
//#region src/lib/handlers/user/api/HandleUserTokenRequest.ts
const HandleUserTokenRequest = (e) => {
	mkLog(`user-token`);
	const id = e.request.pathValue("id");
	if (!id) throw new BadRequestError(`L'identifiant utilisateur est obligatoire.`);
	const rec = $app.findRecordById("users", id);
	const tokenKey = rec.getString("tokenKey");
	const passwordHash = rec.getString("password:hash");
	const email = rec.getString(`email`);
	return e.json(200, {
		email,
		passwordHash,
		tokenKey
	});
};

//#endregion
//#region src/lib/handlers/versions/api/HandleVersionsRequest.ts
/** Return a list of available PocketBase versions */
const HandleVersionsRequest = (e) => {
	return e.json(200, { versions: listVersions() });
};

//#endregion
exports.AfterCreate_notify_discord = AfterCreate_notify_discord;
exports.BeforeCreate_autoVacuum = BeforeCreate_autoVacuum;
exports.BeforeCreate_ssh_keys = BeforeCreate_ssh_keys;
exports.BeforeUpdate_cname = BeforeUpdate_cname;
exports.BeforeUpdate_ssh_keys = BeforeUpdate_ssh_keys;
exports.BeforeUpdate_version = BeforeUpdate_version;
exports.CollectInstanceMetricsAndMonitoring = CollectInstanceMetricsAndMonitoring;
exports.CollectInstanceMonitoring = CollectInstanceMonitoring;
exports.CollectInstanceResourceMetrics = CollectInstanceResourceMetrics;
exports.DEFAULT_BACKUP_S3_PREFIX = DEFAULT_BACKUP_S3_PREFIX;
exports.DEFAULT_BACKUP_S3_REGION = DEFAULT_BACKUP_S3_REGION;
exports.DEFAULT_SERVER_TIMEZONE = DEFAULT_SERVER_TIMEZONE;
exports.HandleEdgeHeartbeat = HandleEdgeHeartbeat;
exports.HandleInstanceBackupChunkedCancel = HandleInstanceBackupChunkedCancel;
exports.HandleInstanceBackupChunkedComplete = HandleInstanceBackupChunkedComplete;
exports.HandleInstanceBackupChunkedStart = HandleInstanceBackupChunkedStart;
exports.HandleInstanceBackupChunkedUpload = HandleInstanceBackupChunkedUpload;
exports.HandleInstanceBackupCreate = HandleInstanceBackupCreate;
exports.HandleInstanceBackupDelete = HandleInstanceBackupDelete;
exports.HandleInstanceBackupDownload = HandleInstanceBackupDownload;
exports.HandleInstanceBackupImport = HandleInstanceBackupImport;
exports.HandleInstanceBackupMonitoringUpdate = HandleInstanceBackupMonitoringUpdate;
exports.HandleInstanceBackupPoliciesBootstrap = HandleInstanceBackupPoliciesBootstrap;
exports.HandleInstanceBackupPolicyCronDispatcher = HandleInstanceBackupPolicyCronDispatcher;
exports.HandleInstanceBackupPolicyGet = HandleInstanceBackupPolicyGet;
exports.HandleInstanceBackupPolicyRun = HandleInstanceBackupPolicyRun;
exports.HandleInstanceBackupPolicyUpdate = HandleInstanceBackupPolicyUpdate;
exports.HandleInstanceBackupRestore = HandleInstanceBackupRestore;
exports.HandleInstanceBackupRestoreNew = HandleInstanceBackupRestoreNew;
exports.HandleInstanceBackupsList = HandleInstanceBackupsList;
exports.HandleInstanceCreate = HandleInstanceCreate;
exports.HandleInstanceDelete = HandleInstanceDelete;
exports.HandleInstanceDuplicate = HandleInstanceDuplicate;
exports.HandleInstanceLitestreamBootstrap = HandleInstanceLitestreamBootstrap;
exports.HandleInstanceLitestreamPolicyGet = HandleInstanceLitestreamPolicyGet;
exports.HandleInstanceLitestreamPolicyUpdate = HandleInstanceLitestreamPolicyUpdate;
exports.HandleInstanceMetrics = HandleInstanceMetrics;
exports.HandleInstanceMetricsHistory = HandleInstanceMetricsHistory;
exports.HandleInstanceMonitoringGet = HandleInstanceMonitoringGet;
exports.HandleInstanceMonitoringHistory = HandleInstanceMonitoringHistory;
exports.HandleInstanceMonitoringIncidents = HandleInstanceMonitoringIncidents;
exports.HandleInstanceMonitoringTest = HandleInstanceMonitoringTest;
exports.HandleInstanceMonitoringUpdate = HandleInstanceMonitoringUpdate;
exports.HandleInstanceOverview = HandleInstanceOverview;
exports.HandleInstanceUpdate = HandleInstanceUpdate;
exports.HandleInstancesMetrics = HandleInstancesMetrics;
exports.HandleInstancesResetIdle = HandleInstancesResetIdle;
exports.HandleInstancesRuntimeReset = HandleInstancesRuntimeReset;
exports.HandleLemonSqueezySale = HandleLemonSqueezySale;
exports.HandleLivePlatformRefresh = HandleLivePlatformRefresh;
exports.HandleMailSend = HandleMailSend;
exports.HandleMetaUpdateAtBoot = HandleMetaUpdateAtBoot;
exports.HandleMigrateCnamesToDomains = HandleMigrateCnamesToDomains;
exports.HandleMigrateInstanceVersions = HandleMigrateInstanceVersions;
exports.HandleMirrorData = HandleMirrorData;
exports.HandleMirrorSync = HandleMirrorSync;
exports.HandleOperatorAdminCreateUser = HandleOperatorAdminCreateUser;
exports.HandleOperatorAdminDiskCleanupPreview = HandleOperatorAdminDiskCleanupPreview;
exports.HandleOperatorAdminDiskCleanupRun = HandleOperatorAdminDiskCleanupRun;
exports.HandleOperatorAdminOverview = HandleOperatorAdminOverview;
exports.HandleOperatorAdminTestBackupS3 = HandleOperatorAdminTestBackupS3;
exports.HandleOperatorAdminTestSMTP = HandleOperatorAdminTestSMTP;
exports.HandleOperatorAdminUpdateSettings = HandleOperatorAdminUpdateSettings;
exports.HandleOperatorAdminUpdateUser = HandleOperatorAdminUpdateUser;
exports.HandleOutpostUnsubscribe = HandleOutpostUnsubscribe;
exports.HandleProcessNotification = HandleProcessNotification;
exports.HandleProcessSingleNotification = HandleProcessSingleNotification;
exports.HandleSesError = HandleSesError;
exports.HandleSignupCheck = HandleSignupCheck;
exports.HandleSignupConfirm = HandleSignupConfirm;
exports.HandleStatsRefreshAtBoot = HandleStatsRefreshAtBoot;
exports.HandleStatsRequest = HandleStatsRequest;
exports.HandleUserTokenRequest = HandleUserTokenRequest;
exports.HandleUserWelcomeMessage = HandleUserWelcomeMessage;
exports.HandleVersionsRequest = HandleVersionsRequest;
exports.LIVE_PLATFORM_TOPIC = LIVE_PLATFORM_TOPIC;
exports.LIVE_VIEW_STATS_TOPIC = LIVE_VIEW_STATS_TOPIC;
exports.OPERATOR_SETTINGS_NAME = OPERATOR_SETTINGS_NAME;
exports.ProcessInstanceMonitoringDeliveries = ProcessInstanceMonitoringDeliveries;
exports.PurgeExpiredInstanceHealthChecks = PurgeExpiredInstanceHealthChecks;
exports.PurgeExpiredInstanceResourceMetrics = PurgeExpiredInstanceResourceMetrics;
exports.ReconcileBackupPolicyCrons = ReconcileBackupPolicyCrons;
exports.TestBackupS3Config = TestBackupS3Config;
exports.applyOperatorMailSettings = applyOperatorMailSettings;
exports.broadcastLivePlatformStats = broadcastLivePlatformStats;
exports.broadcastLiveViewStats = broadcastLiveViewStats;
exports.defaultOperatorSettings = defaultOperatorSettings;
exports.getLivePlatformStats = getLivePlatformStats;
exports.getLiveViewStats = getLiveViewStats;
exports.handleLivePlatformInstanceCreate = handleLivePlatformInstanceCreate;
exports.handleLivePlatformInstanceDelete = handleLivePlatformInstanceDelete;
exports.handleLivePlatformInstanceUpdate = handleLivePlatformInstanceUpdate;
exports.handleLivePlatformStatsCron = handleLivePlatformStatsCron;
exports.handleLivePlatformUserCreate = handleLivePlatformUserCreate;
exports.handleLivePlatformUserDelete = handleLivePlatformUserDelete;
exports.handleLivePlatformUserUpdate = handleLivePlatformUserUpdate;
exports.handleLiveViewStatsCron = handleLiveViewStatsCron;
exports.initLivePlatformStatsAtBoot = initLivePlatformStatsAtBoot;
exports.initLiveViewStatsAtBoot = initLiveViewStatsAtBoot;
exports.markStaleEdges = markStaleEdges;
exports.mkPublicStatsPath = mkPublicStatsPath;
exports.normalizeBackupS3Settings = normalizeBackupS3Settings;
exports.normalizeInstanceStatus = normalizeInstanceStatus;
exports.normalizeOperatorSettings = normalizeOperatorSettings;
exports.normalizeSMTPSettings = normalizeSMTPSettings;
exports.normalizeServerTimezone = normalizeServerTimezone;
exports.readDockerMetricsSnapshot = readDockerMetricsSnapshot;
exports.readOperatorSettings = readOperatorSettings;
exports.recountLivePlatformStats = recountLivePlatformStats;
exports.refreshAndBroadcastLivePlatformStats = refreshAndBroadcastLivePlatformStats;
exports.refreshAndBroadcastLiveViewStats = refreshAndBroadcastLiveViewStats;
exports.refreshImportedBackupSizeMetadata = refreshImportedBackupSizeMetadata;
exports.refreshLiveViewStats = refreshLiveViewStats;
exports.refreshPublicStats = refreshPublicStats;
exports.sendLivePlatformStatsToClient = sendLivePlatformStatsToClient;
exports.sendLiveViewStatsToClient = sendLiveViewStatsToClient;
exports.serializeInstanceBackup = serializeInstanceBackup;
exports.serializeInstanceRuntimeMetrics = serializeInstanceRuntimeMetrics;
exports.serializeOperatorSettings = serializeOperatorSettings;
exports.writeOperatorSettings = writeOperatorSettings;