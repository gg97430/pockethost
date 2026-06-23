
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
	const record = $app.findFirstRecordByFilter("stats", "id != \"\"");
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
	for (const clientId in clients) if (clients[clientId].hasSubscription(LIVE_VIEW_STATS_TOPIC)) clients[clientId].send(message);
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
const countInstanceStatus = (key) => {
	return $app.countRecords("instances", $dbx.exp(`status = {:status}`, { status: key }));
};
const countVerifiedUsers = () => {
	return $app.countRecords("verified_users");
};
const countUnverifiedUsers = () => {
	return $app.countRecords("unverified_users");
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
		totalUsers: $app.countRecords("users"),
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
	for (const clientId in clients) if (clients[clientId].hasSubscription(LIVE_PLATFORM_TOPIC)) clients[clientId].send(message);
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
	const prev = e.record.original().getBool("verified");
	if (next === prev) return;
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
		const record = $app.findFirstRecordByData("settings", "name", POCKETBASE_VERSIONS_SETTING);
		const value = parsePocketbaseVersionsValue(record.getString("value"));
		if (!value?.versions?.length) return [];
		return value.versions;
	} catch {
		return [];
	}
};
/** Minor wildcard versions (e.g. `0.22.*`) from mothership settings */
const listVersions = () => readPocketbaseVersions().map((entry) => entry.range);

//#endregion
//#region src/lib/handlers/operatorAdmin/operatorSettings.ts
const OPERATOR_SETTINGS_NAME = "operator_settings";
const envBoolean = (name, fallback) => {
	const raw = `${process.env[name] || ""}`.trim().toLowerCase();
	if (!raw) return fallback;
	return [
		"1",
		"true",
		"yes",
		"on"
	].includes(raw);
};
const envNumber = (name, fallback) => {
	const value = Number(process.env[name] || "");
	if (!Number.isFinite(value) || value < 0) return fallback;
	return value;
};
const defaultOperatorSettings = () => {
	const autoVerifyUsers = envBoolean("PH_AUTO_VERIFY_SIGNUPS", true);
	return {
		publicSignupEnabled: envBoolean("PH_PUBLIC_SIGNUP_ENABLED", false),
		autoVerifyUsers,
		defaultUserQuota: envNumber("PH_SIGNUP_SUBSCRIPTION_QUANTITY", autoVerifyUsers ? 250 : 0),
		defaultSubscription: "free",
		defaultInstancePower: true,
		defaultInstanceDevMode: true,
		defaultSyncAdmin: true,
		defaultAutoVacuum: true,
		supportEmail: process.env.PH_SUPPORT_EMAIL || "",
		maintenanceMessage: "",
		notes: ""
	};
};
const parseSettingsValue = (raw) => {
	if (!raw) return {};
	if (typeof raw === "string") try {
		return JSON.parse(raw);
	} catch {
		return {};
	}
	return raw;
};
const readOperatorSettings = (app = $app) => {
	const defaults = defaultOperatorSettings();
	try {
		const record = app.findFirstRecordByData("settings", "name", OPERATOR_SETTINGS_NAME);
		return normalizeOperatorSettings({
			...defaults,
			...parseSettingsValue(record.get("value"))
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
	record.set("value", normalized);
	app.save(record);
	return normalized;
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
//#region src/lib/handlers/instance/api/HandleInstanceBackups.ts
const BACKUP_FORMAT = "gestion-pocketbase-instance-backup-v1";
const BACKUP_DIRS = [
	"pb_data",
	"pb_public",
	"pb_migrations",
	"pb_hooks"
];
const MAX_STOP_WAIT_SECONDS = 120;
const DIR_MODE = 493;
const PRIVATE_DIR_MODE = 448;
const PRIVATE_FILE_MODE = 384;
const dataRoot$1 = () => {
	const envRoot = $os.getenv("DATA_ROOT");
	if (envRoot) return envRoot;
	const appDataDir = `${$app.dataDir()}`;
	const inferred = appDataDir.replace(/\/mothership\/pb_data\/?$/, "");
	if (inferred !== appDataDir) return inferred;
	throw new Error("Impossible de trouver le dossier de donnees des instances.");
};
const backupRoot = () => $os.getenv("INSTANCE_BACKUP_ROOT") || `${dataRoot$1()}/backups/instances`;
const assertSafeInstanceId$1 = (id) => {
	if (!id.match(/^[a-z0-9]+$/)) throw new BadRequestError("Identifiant d'instance invalide.");
};
const assertSafeBackupId = (id) => {
	if (!id.match(/^[a-z0-9]+$/)) throw new BadRequestError("Identifiant de sauvegarde invalide.");
};
const assertSafeBackupFilename = (filename) => {
	if (!filename.match(/^[a-zA-Z0-9._-]+\.tar\.gz$/)) throw new BadRequestError("Nom de sauvegarde invalide.");
};
const instanceRoot$1 = (id) => `${dataRoot$1()}/instances/${id}`;
const backupDir = (instanceId) => `${backupRoot()}/${instanceId}`;
const backupPath = (instanceId, filename) => `${backupDir(instanceId)}/${filename}`;
const pathExists$1 = (path) => {
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
const runCommand = (name, ...args) => toString($os.cmd(name, ...args).combinedOutput()).trim();
const sleepOneSecond = () => {
	$os.cmd("sleep", "1").combinedOutput();
};
const errorMessage = (error$1) => {
	if (error$1 instanceof Error) return error$1.message;
	return `${error$1}`;
};
const slugForFilename = (value) => {
	const clean = value.toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-").replace(/^-+|-+$/g, "");
	return clean.slice(0, 48).replace(/-+$/g, "") || "instance";
};
const timestampForFilename = () => (/* @__PURE__ */ new Date()).toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z");
const createBackupFilename = (instance, kind) => {
	const suffix = kind === "pre-restore" ? "pre-restore" : "manual";
	return `${timestampForFilename()}-${slugForFilename(instance.getString("subdomain"))}-${suffix}-${instance.id}.tar.gz`;
};
const findInstance = (id) => {
	assertSafeInstanceId$1(id);
	const instance = $app.findRecordById("instances", id);
	if (!instance) throw new BadRequestError(`Instance ${id} introuvable.`);
	return instance;
};
const requireAuthRecord = (authRecord) => {
	if (!authRecord) throw new BadRequestError("Session utilisateur attendue.");
	return authRecord;
};
const assertInstanceAccess = (instance, authRecord) => {
	if (instance.getString("uid") !== authRecord.id && !authRecord.getBool("superAdmin")) throw new BadRequestError("Non autorise.");
};
const serializeBackup = (backup) => ({
	id: backup.id,
	user: backup.getString("user"),
	instance: backup.getString("instance"),
	kind: backup.getString("kind"),
	status: backup.getString("status"),
	filename: backup.getString("filename"),
	remoteKey: backup.getString("remoteKey"),
	sizeBytes: Number(backup.get("sizeBytes") || 0),
	compressedBytes: Number(backup.get("compressedBytes") || 0),
	checksum: backup.getString("checksum"),
	error: backup.getString("error"),
	remoteError: backup.getString("remoteError"),
	manifest: backup.get("manifest"),
	created: backup.getString("created"),
	updated: backup.getString("updated")
});
const getBackupRecord = (instance, backupId) => {
	assertSafeBackupId(backupId);
	const backup = $app.findRecordById("instance_backups", backupId);
	if (!backup || backup.getString("instance") !== instance.id) throw new BadRequestError("Sauvegarde introuvable.");
	return backup;
};
const pathValue = (e, name) => {
	if (!e.request) throw new BadRequestError("Requete invalide.");
	return e.request.pathValue(name);
};
const assertNoRunningOperation = (instanceId) => {
	let running = null;
	try {
		running = $app.findFirstRecordByFilter("instance_backups", "instance = {:instance} && status = \"running\"", { instance: instanceId });
	} catch (error$1) {
		running = null;
	}
	if (running) throw new BadRequestError("Une operation de sauvegarde est deja en cours pour cette instance.");
};
const setInstancePower = (instanceId, power) => {
	const record = findInstance(instanceId);
	record.set("power", power);
	$app.save(record);
	return record;
};
const waitUntilIdle = (instanceId) => {
	for (let i = 0; i < MAX_STOP_WAIT_SECONDS; i++) {
		const current = findInstance(instanceId);
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
const createBackupRecord = (instance, authRecord, kind) => {
	const collection = $app.findCollectionByNameOrId("instance_backups");
	const backup = new Record(collection);
	backup.set("user", instance.getString("uid") || authRecord.id);
	backup.set("instance", instance.id);
	backup.set("kind", kind);
	backup.set("status", "running");
	backup.set("filename", "");
	backup.set("sizeBytes", 0);
	backup.set("compressedBytes", 0);
	backup.set("checksum", "");
	backup.set("error", "");
	backup.set("remoteError", "");
	backup.set("manifest", null);
	$app.save(backup);
	return backup;
};
const sourceSizeBytes = (root) => {
	const dirs = BACKUP_DIRS.map((dir) => `${root}/${dir}`);
	const output = runCommand("du", "-sb", ...dirs);
	return output.split("\n").map((line) => Number(line.trim().split(/\s+/)[0] || 0)).filter((value) => Number.isFinite(value)).reduce((sum, value) => sum + value, 0);
};
const sha256 = (path) => {
	const output = runCommand("sha256sum", path);
	return output.split(/\s+/)[0] || "";
};
const s3Config = () => {
	const enabled = ($os.getenv("INSTANCE_BACKUP_S3_ENABLED") || "").toLowerCase() === "true";
	if (!enabled) return null;
	const endpoint = $os.getenv("INSTANCE_BACKUP_S3_ENDPOINT");
	const bucket = $os.getenv("INSTANCE_BACKUP_S3_BUCKET");
	const prefix = ($os.getenv("INSTANCE_BACKUP_S3_PREFIX") || "instances").replace(/^\/+|\/+$/g, "");
	const region = $os.getenv("AWS_DEFAULT_REGION") || "auto";
	if (!endpoint || !bucket) throw new Error("Configuration R2/S3 incomplete: endpoint et bucket requis.");
	return {
		endpoint,
		bucket,
		prefix,
		region
	};
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
	runCommand("aws", "s3", "cp", localPath, `s3://${config.bucket}/${remoteKey}`, "--endpoint-url", config.endpoint, "--region", config.region);
	return remoteKey;
};
const downloadBackupFromS3 = (remoteKey, localPath) => {
	const config = s3Config();
	if (!config) throw new Error("La sauvegarde locale est absente et R2/S3 est desactive.");
	runCommand("aws", "s3", "cp", `s3://${config.bucket}/${remoteKey}`, localPath, "--endpoint-url", config.endpoint, "--region", config.region);
};
const deleteBackupFromS3 = (remoteKey) => {
	const config = s3Config();
	if (!config || !remoteKey) return "";
	return runCommand("aws", "s3", "rm", `s3://${config.bucket}/${remoteKey}`, "--endpoint-url", config.endpoint, "--region", config.region);
};
const ensureInstanceDirs = (root) => {
	$os.mkdirAll(root, DIR_MODE);
	for (const dir of BACKUP_DIRS) $os.mkdirAll(`${root}/${dir}`, DIR_MODE);
};
const createArchive = (instance, backup, kind) => {
	assertSafeInstanceId$1(instance.id);
	const root = instanceRoot$1(instance.id);
	const dir = backupDir(instance.id);
	const filename = createBackupFilename(instance, kind);
	const finalPath = backupPath(instance.id, filename);
	const tmpPath = `${finalPath}.tmp`;
	const stagingDir = `${dir}/.staging-${backup.id}`;
	const manifestPath = `${stagingDir}/manifest.json`;
	assertSafeBackupFilename(filename);
	$os.mkdirAll(dir, DIR_MODE);
	$os.removeAll(stagingDir);
	$os.mkdirAll(stagingDir, PRIVATE_DIR_MODE);
	$os.removeAll(tmpPath);
	ensureInstanceDirs(root);
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
		sourceSizeBytes: sizeBytes
	};
	try {
		$os.writeFile(manifestPath, JSON.stringify(manifest, null, 2), PRIVATE_FILE_MODE);
		runCommand("tar", "-czf", tmpPath, "-C", root, ...BACKUP_DIRS, "-C", stagingDir, "manifest.json");
		$os.rename(tmpPath, finalPath);
	} finally {
		try {
			$os.remove(tmpPath);
		} catch {}
		try {
			$os.removeAll(stagingDir);
		} catch {}
	}
	return {
		filename,
		localPath: finalPath,
		sizeBytes,
		compressedBytes: fileSize(finalPath),
		checksum: sha256(finalPath),
		manifest
	};
};
const markBackupReady = (backup, details) => {
	backup.set("status", "ready");
	backup.set("filename", details.filename);
	backup.set("localPath", details.localPath);
	backup.set("sizeBytes", details.sizeBytes);
	backup.set("compressedBytes", details.compressedBytes);
	backup.set("checksum", details.checksum);
	backup.set("manifest", details.manifest);
	backup.set("error", "");
	try {
		const remoteKey = uploadBackupToS3(backup.getString("instance"), details.filename, details.localPath);
		backup.set("remoteKey", remoteKey);
		backup.set("remoteError", "");
	} catch (error$1) {
		backup.set("remoteError", errorMessage(error$1));
	}
	$app.save(backup);
};
const markBackupFailed = (backup, error$1) => {
	backup.set("status", "failed");
	backup.set("error", errorMessage(error$1));
	$app.save(backup);
};
const createBackupForInstance = (instance, authRecord, kind, managePower, skipRunningCheck = false) => {
	if (!skipRunningCheck) assertNoRunningOperation(instance.id);
	const backup = createBackupRecord(instance, authRecord, kind);
	let power = { shouldRestart: false };
	try {
		if (managePower) power = stopForFilesystemOperation(instance);
		else waitUntilIdle(instance.id);
		const stoppedInstance = findInstance(instance.id);
		const details = createArchive(stoppedInstance, backup, kind);
		markBackupReady(backup, details);
		return backup;
	} catch (error$1) {
		markBackupFailed(backup, error$1);
		throw error$1;
	} finally {
		if (managePower) restartIfNeeded(instance.id, power);
	}
};
const safeTarEntry = (entry) => {
	const normalized = entry.replace(/^\.\/+/, "");
	const parts = normalized.split("/");
	return !!normalized && !normalized.startsWith("/") && !normalized.startsWith("../") && !parts.includes("..");
};
const validateArchiveListing = (archivePath) => {
	const output = runCommand("tar", "-tzf", archivePath);
	const entries = output.split("\n").map((entry) => entry.trim()).filter(Boolean);
	if (!entries.length) throw new BadRequestError("Archive vide.");
	for (const entry of entries) if (!safeTarEntry(entry)) throw new BadRequestError("Archive invalide.");
};
const ensureLocalArchive = (instance, backup) => {
	const filename = backup.getString("filename");
	assertSafeBackupFilename(filename);
	const localPath = backupPath(instance.id, filename);
	if (pathExists$1(localPath)) return localPath;
	const remoteKey = backup.getString("remoteKey");
	if (!remoteKey) throw new BadRequestError("Archive locale introuvable.");
	$os.mkdirAll(backupDir(instance.id), DIR_MODE);
	downloadBackupFromS3(remoteKey, localPath);
	backup.set("localPath", localPath);
	backup.set("remoteError", "");
	$app.save(backup);
	return localPath;
};
const readManifest = (extractDir) => {
	const raw = toString($os.readFile(`${extractDir}/manifest.json`));
	const manifest = JSON.parse(raw);
	if (!manifest || manifest.format !== BACKUP_FORMAT) throw new BadRequestError("Format de sauvegarde non pris en charge.");
	return manifest;
};
const restoreExtractedDirs = (instance, extractDir) => {
	const root = instanceRoot$1(instance.id);
	const rollbackDir = `${root}/.restore-rollback-${Date.now()}-${instance.id}`;
	$os.mkdirAll(root, DIR_MODE);
	$os.mkdirAll(rollbackDir, PRIVATE_DIR_MODE);
	let movedOldDirs = false;
	try {
		for (const dir of BACKUP_DIRS) {
			const source = `${extractDir}/${dir}`;
			if (!pathExists$1(source)) throw new BadRequestError(`Archive incomplete: ${dir} manquant.`);
		}
		for (const dir of BACKUP_DIRS) {
			const current = `${root}/${dir}`;
			if (pathExists$1(current)) $os.rename(current, `${rollbackDir}/${dir}`);
		}
		movedOldDirs = true;
		for (const dir of BACKUP_DIRS) $os.rename(`${extractDir}/${dir}`, `${root}/${dir}`);
		$os.removeAll(rollbackDir);
	} catch (error$1) {
		if (movedOldDirs) for (const dir of BACKUP_DIRS) {
			try {
				$os.removeAll(`${root}/${dir}`);
			} catch {}
			try {
				if (pathExists$1(`${rollbackDir}/${dir}`)) $os.rename(`${rollbackDir}/${dir}`, `${root}/${dir}`);
			} catch {}
		}
		try {
			$os.removeAll(rollbackDir);
		} catch {}
		throw error$1;
	}
};
const restoreArchive = (instance, backup) => {
	const archivePath = ensureLocalArchive(instance, backup);
	validateArchiveListing(archivePath);
	const extractDir = `${instanceRoot$1(instance.id)}/.restore-extract-${backup.id}`;
	$os.mkdirAll(instanceRoot$1(instance.id), DIR_MODE);
	$os.removeAll(extractDir);
	$os.mkdirAll(extractDir, PRIVATE_DIR_MODE);
	try {
		runCommand("tar", "-xzf", archivePath, "-C", extractDir);
		const manifest = readManifest(extractDir);
		restoreExtractedDirs(instance, extractDir);
		if (manifest.instance?.version) {
			const current = findInstance(instance.id);
			current.set("version", manifest.instance.version);
			$app.save(current);
		}
	} finally {
		try {
			$os.removeAll(extractDir);
		} catch {}
	}
};
const HandleInstanceBackupCreate = (e) => {
	const log = mkLog("POST:instance:backup");
	const authRecord = requireAuthRecord(e.auth);
	const instance = findInstance(pathValue(e, "id"));
	assertInstanceAccess(instance, authRecord);
	const backup = createBackupForInstance(instance, authRecord, "manual", true);
	log(`created ${backup.id} for ${instance.id}`);
	return e.json(200, { backup: serializeBackup(backup) });
};
const HandleInstanceBackupsList = (e) => {
	const authRecord = requireAuthRecord(e.auth);
	const instance = findInstance(pathValue(e, "id"));
	assertInstanceAccess(instance, authRecord);
	const backups = $app.findRecordsByFilter("instance_backups", "instance = {:instance}", "-created", 100, 0, { instance: instance.id }).filter((record) => !!record).map(serializeBackup);
	return e.json(200, { backups });
};
const HandleInstanceBackupDownload = (e) => {
	const authRecord = requireAuthRecord(e.auth);
	const instance = findInstance(pathValue(e, "id"));
	assertInstanceAccess(instance, authRecord);
	const backup = getBackupRecord(instance, pathValue(e, "backupId"));
	if (backup.getString("status") !== "ready") throw new BadRequestError("Cette sauvegarde n'est pas prete.");
	const localPath = ensureLocalArchive(instance, backup);
	const filename = backup.getString("filename");
	e.response.header().set("Content-Disposition", `attachment; filename="${filename}"`);
	e.response.header().set("Content-Length", `${fileSize(localPath)}`);
	return e.fileFS($os.dirFS(backupDir(instance.id)), filename);
};
const HandleInstanceBackupDelete = (e) => {
	const authRecord = requireAuthRecord(e.auth);
	const instance = findInstance(pathValue(e, "id"));
	assertInstanceAccess(instance, authRecord);
	const backup = getBackupRecord(instance, pathValue(e, "backupId"));
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
	const authRecord = requireAuthRecord(e.auth);
	const instance = findInstance(pathValue(e, "id"));
	assertInstanceAccess(instance, authRecord);
	assertNoRunningOperation(instance.id);
	const backup = getBackupRecord(instance, pathValue(e, "backupId"));
	if (backup.getString("status") !== "ready") throw new BadRequestError("Cette sauvegarde n'est pas prete.");
	const power = stopForFilesystemOperation(instance);
	let restored = false;
	try {
		createBackupForInstance(findInstance(instance.id), authRecord, "pre-restore", false, true);
		restoreArchive(findInstance(instance.id), backup);
		restored = true;
		log(`restored ${backup.id} into ${instance.id}`);
	} finally {
		if (restored) restartIfNeeded(instance.id, power);
	}
	return e.json(200, { status: "ok" });
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
	return e.json(200, { status: "ok" });
};

//#endregion
//#region src/lib/handlers/instance/api/HandleInstanceDuplicate.ts
const COPY_DIRS = [
	"pb_data",
	"pb_migrations",
	"pb_public",
	"pb_hooks"
];
const dataRoot = () => {
	const envRoot = $os.getenv("DATA_ROOT");
	if (envRoot) return envRoot;
	const appDataDir = `${$app.dataDir()}`;
	const inferred = appDataDir.replace(/\/mothership\/pb_data\/?$/, "");
	if (inferred !== appDataDir) return inferred;
	throw new Error("Impossible de trouver le dossier de donnees des instances.");
};
const assertSafeInstanceId = (id) => {
	if (!id.match(/^[a-z0-9]+$/)) throw new BadRequestError("Identifiant d'instance invalide.");
};
const instanceRoot = (id) => `${dataRoot()}/instances/${id}`;
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
	assertSafeInstanceId(sourceId);
	assertSafeInstanceId(targetId);
	const sourceRoot = instanceRoot(sourceId);
	const targetRoot = instanceRoot(targetId);
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
	const base = clean.match(/^[a-z]/) ? clean : `base-${clean}`;
	return base.slice(0, 34).replace(/-+$/g, "") || "base";
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
	assertSafeInstanceId(sourceId);
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
		target.set("autoVacuum", source.getBool("autoVacuum"));
		$app.save(target);
		copyInstanceFiles(source.id, target.id);
		log(`duplicated ${source.id} to ${target.id}`);
	} catch (error$1) {
		try {
			if (target.id) $app.delete(target);
		} catch {}
		try {
			if (target.id) $os.removeAll(instanceRoot(target.id));
		} catch {}
		throw new ApiError(500, `Impossible de dupliquer la base.`, { error: error$1 });
	}
	return e.json(200, { instance: target });
};

//#endregion
//#region src/lib/handlers/instance/bootstrap/resetInstancesIdle.ts
const resetInstancesIdle = (app) => {
	const records = app.findRecordsByFilter(`instances`, `status != 'idle'`).filter((r) => !!r);
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
	} catch (error$1) {
		if (log) log(`Cloudflare API error:`, error$1);
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
			dev: null,
			cname: null
		}
	});
	e.bindBody(data);
	log(`After bind`);
	data = JSON.parse(JSON.stringify(data));
	const id = e.request.pathValue("id");
	const { fields: { subdomain, power, version, secrets, webhooks, syncAdmin, autoVacuum, dev, cname } } = data;
	log(`vars`, JSON.stringify({
		id,
		subdomain,
		power,
		version,
		secrets,
		webhooks,
		syncAdmin,
		autoVacuum,
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
		const createResponse = createCloudflareCustomHostname(newCname, log);
		if (createResponse) log(`Cloudflare API call completed for "${newCname}" - frontend will poll for health`);
	}
	const recordAutoVacuum = record.getBool("autoVacuum");
	const advancedFieldChanging = subdomain !== null && subdomain !== record.getString("subdomain") || version !== null && version !== record.getString("version") || syncAdmin !== null && syncAdmin !== record.getBool("syncAdmin") || autoVacuum !== null && autoVacuum !== recordAutoVacuum || dev !== null && dev !== record.getBool("dev") || cnameChanged;
	if (advancedFieldChanging) {
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
		const domainsCollection = $app.findCollectionByNameOrId("domains");
		if (!domainsCollection) {
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
					const domainsCollection$1 = $app.findCollectionByNameOrId("domains");
					const domainRecord = new Record(domainsCollection$1);
					domainRecord.set("instance", instanceId);
					domainRecord.set("domain", cname);
					domainRecord.set("active", instance.getBool("cname_active"));
					$app.save(domainRecord);
					log(`Created domain record for ${cname}`);
					cnameMigrated++;
				}
			} catch (error$1) {
				log(`Failed to migrate cname for instance ${instance.id}:`, error$1);
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
			} catch (error$1) {
				log(`Failed to update domains array for instance ${instanceId}:`, error$1);
			}
		});
		log(`Phase 2 complete: updated domains arrays for ${instancesUpdated} instances`);
	} catch (error$1) {
		log(`Error migrating cnames: ${error$1}`);
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
				const newVersion$1 = [
					major,
					minor,
					"*"
				].join(".");
				return newVersion$1;
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
	const log = mkLog(`instances:create:discord:notify`);
	const audit = mkAudit(log, $app);
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
	} catch (e$1) {
		audit(`ERROR`, `Instance creation discord notify failed with ${e$1}`);
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
		const inUse = (() => {
			try {
				$app.db().newQuery(`select id from instances where cname='${newCname}' and id <> '${id}'`).one(result);
			} catch (e$1) {
				return false;
			}
			return true;
		})();
		if (inUse) {
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
			} catch (e$1) {
				throw new Error(`User ${context.user_id} not found`);
			}
		})();
		log(`user record ok`, userRec);
		const event_name_map = {
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
		};
		const event_handler = event_name_map[context.event_name];
		if (!event_handler) throw new Error(`Unsupported event: ${context.event_name}`);
		else log(`event handler ok`, event_handler);
		const product_handler_map = {
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
		};
		const product_handler = product_handler_map[pv_id];
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
		const user = $app.findFirstRecordByData("users", "email", to);
		const skipReason = mailRecipientSkipReason(user);
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
		bcc: [process.env.TEST_EMAIL].filter((e$1) => !!e$1).map((e$1) => ({ address: e$1 })),
		subject,
		html: body
	});
	$app.newMailClient().send(email);
	const msg = `Sent to ${to}`;
	log(msg);
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
	log(`Saved settings`);
};

//#endregion
//#region src/lib/handlers/mirror/lib/buildMirrorDump.ts
const exportRecord = (record) => record.publicExport();
const buildMirrorDump = (app) => {
	const users = app.findRecordsByFilter(`users`, `verified = true`).filter((r) => !!r).map(exportRecord);
	const instances = app.findAllRecords(`instances`, $dbx.exp(`instances.uid in (select id from users where verified = 1)`)).filter((r) => !!r).map(exportRecord);
	return {
		users,
		instances
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
				const res = $http.send(params);
				log(`discord sent`, res);
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
		const messageTemplateRec = notificationRec.expandedOne(`message_template`);
		if (!messageTemplateRec) throw new Error(`Missing message template`);
		processNotification(notificationRec);
	} catch (e$1) {
		audit(`ERROR`, `${e$1}`, { notification: notificationRec.id });
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
	} catch (e$1) {
		audit(`ERROR`, `${e$1}`, { user: newModel.id });
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
	} catch (error$1) {
		throw new BadRequestError(`Impossible d'analyser la requete JSON.`, error$1);
	}
};
const suggestUniqueAuthRecordUsername$1 = (collection, baseUsername) => {
	let username = baseUsername;
	for (let i = 0; i < 10; i++) {
		try {
			const total = $app.countRecords(collection, $dbx.exp("LOWER([[username]])={:username}", { username: username.toLowerCase() }));
			if (total === 0) break;
		} catch {}
		username = baseUsername + $security.randomStringWithAlphabet(3 + i, "123456789");
	}
	return username;
};
const userExists = (email, exceptId = "") => {
	try {
		const user = $app.findFirstRecordByData("users", "email", email);
		return user.id !== exceptId;
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
const listOperatorUsers = () => $app.findRecordsByFilter("users", "id != \"\"", "-created").filter((record) => !!record).map(serializeUser);
const ensureAnotherSuperAdminExists = (currentUserId) => {
	const superAdmins = $app.findRecordsByFilter("users", "superAdmin = true").filter((record) => !!record);
	if (superAdmins.length <= 1 && superAdmins[0]?.id === currentUserId) throw new BadRequestError("Impossible de retirer le dernier superadmin.");
};
const HandleOperatorAdminOverview = (e) => {
	requireOperatorAdmin(e);
	const users = listOperatorUsers();
	const totalInstances = $app.countRecords("instances");
	return e.json(200, {
		settings: readOperatorSettings(),
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
	const current = readOperatorSettings();
	const body = readJsonBody(e);
	const settings = writeOperatorSettings(normalizeOperatorSettings({
		...current,
		...body
	}));
	return e.json(200, { settings });
};

//#endregion
//#region src/lib/handlers/outpost/api/HandleOutpostUnsubscribe.ts
const HandleOutpostUnsubscribe = (e) => {
	const log = mkLog(`unsubscribe`);
	const audit = mkAudit(log, $app);
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
	const { minLength, maxLength,...rest } = options || {};
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
		const r = Math.random();
		return Math.floor(r * lessThan);
	}
	if (options === void 0) return word();
	if (typeof options === "number") options = { exactly: options };
	else if (Object.keys(rest).length === 0) return word();
	if (options.exactly) {
		options.min = options.exactly;
		options.max = options.exactly;
	}
	if (typeof options.wordsPerString !== "number") options.wordsPerString = 1;
	if (typeof options.formatter !== "function") options.formatter = (word$1) => word$1;
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
			const total = $app.countRecords(collection, $dbx.exp("LOWER([[username]])={:username}", { username: username.toLowerCase() }));
			if (total === 0) break;
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
			const parsed$1 = JSON.parse(rawBody);
			return parsed$1;
		} catch (e$1) {
			throw new BadRequestError(`Impossible d'analyser la requête JSON. Corps reçu : ${rawBody}`, e$1);
		}
	})();
	const email = parsed.email?.trim().toLowerCase();
	const password = parsed.password?.trim();
	const desiredInstanceName = parsed.instanceName?.trim();
	const version = parsed.version?.trim() || listVersions()[0];
	if (!email) throw error(`email`, "required", "L'email est obligatoire");
	if (!password) throw error(`password`, `required`, "Le mot de passe est obligatoire");
	if (!desiredInstanceName) throw error(`instanceName`, `required`, `Le nom de l'instance est obligatoire`);
	const userExists$1 = (() => {
		try {
			$app.findFirstRecordByData("users", "email", email);
			return true;
		} catch {
			return false;
		}
	})();
	if (userExists$1) throw error(`email`, `exists`, `Ce compte utilisateur existe déjà. Essayez une réinitialisation du mot de passe.`);
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
		} catch (e$1) {
			throw error(`email`, `fail`, `Impossible de créer l'utilisateur : ${e$1}`);
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
		} catch (e$1) {
			if (`${e$1}`.match(/ UNIQUE /)) throw error(`instanceName`, `exists`, `Ce nom d'instance vient d'être pris. Essayez-en un autre.`);
			throw error(`instanceName`, `fail`, `Impossible de créer l'instance : ${e$1}`);
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
		} catch (e$1) {
			audit("PBOUNCE_ERR", `${e$1}`, extra);
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
		} catch (e$1) {
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
const ED25519_WIRE_LEN = 19 + ED25519_WIRE_KEY_LEN;
const readUint32BE = (bytes, offset) => {
	if (offset + 4 > bytes.length) throw new Error("Invalid public key encoding.");
	return (bytes[offset] << 24 | bytes[offset + 1] << 16 | bytes[offset + 2] << 8 | bytes[offset + 3]) >>> 0;
};
const readSshString = (bytes, offset) => {
	const length = readUint32BE(bytes, offset);
	offset += 4;
	if (length < 0 || offset + length > bytes.length) throw new Error("Invalid public key encoding.");
	const value = bytes.slice(offset, offset + length);
	return {
		value,
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
	const lines = trimmed.split(/\r?\n/).map((line$1) => line$1.trim()).filter(Boolean);
	if (lines.length > 1) throw new Error("Paste a single public key line only.");
	const line = lines[0] ?? "";
	const parts = line.split(/\s+/).filter(Boolean);
	if (parts.length < 2) throw new Error("Public key must look like: ssh-ed25519 AAAA… comment");
	const algo = parts[0];
	const keyData = parts[1];
	if (algo !== ED25519_ALGO) throw new Error("Only ssh-ed25519 public keys are supported.");
	const wire = decodeBase64(keyData);
	validateWire(wire);
	const comment = parts.slice(2).join(" ");
	const normalized = comment ? `${ED25519_ALGO} ${keyData} ${comment}` : `${ED25519_ALGO} ${keyData}`;
	return {
		normalized,
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
	} catch (error$1) {
		throw new BadRequestError(`${error$1}`);
	}
	record.set("public_key", parsed.normalized);
	const fingerprint = record.getString("fingerprint").trim();
	if (!fingerprint.startsWith("SHA256:")) throw new BadRequestError("Empreinte invalide.");
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
const refreshPublicStats = () => {
	const log = mkLog("refreshPublicStats");
	const db = $app.db();
	const users = new DynamicModel({ total: 0 });
	db.newQuery("SELECT COUNT(*) as total FROM users").one(users);
	const instances = new DynamicModel({ total: 0 });
	db.newQuery("SELECT COUNT(*) as total FROM instances").one(instances);
	const stats = {
		updatedAt: (/* @__PURE__ */ new Date()).toISOString(),
		developers: users.total,
		instances: instances.total
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
	const log = mkLog(`user-token`);
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
exports.HandleEdgeHeartbeat = HandleEdgeHeartbeat;
exports.HandleInstanceBackupCreate = HandleInstanceBackupCreate;
exports.HandleInstanceBackupDelete = HandleInstanceBackupDelete;
exports.HandleInstanceBackupDownload = HandleInstanceBackupDownload;
exports.HandleInstanceBackupRestore = HandleInstanceBackupRestore;
exports.HandleInstanceBackupsList = HandleInstanceBackupsList;
exports.HandleInstanceCreate = HandleInstanceCreate;
exports.HandleInstanceDelete = HandleInstanceDelete;
exports.HandleInstanceDuplicate = HandleInstanceDuplicate;
exports.HandleInstanceUpdate = HandleInstanceUpdate;
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
exports.HandleOperatorAdminOverview = HandleOperatorAdminOverview;
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
exports.normalizeInstanceStatus = normalizeInstanceStatus;
exports.normalizeOperatorSettings = normalizeOperatorSettings;
exports.readOperatorSettings = readOperatorSettings;
exports.recountLivePlatformStats = recountLivePlatformStats;
exports.refreshAndBroadcastLivePlatformStats = refreshAndBroadcastLivePlatformStats;
exports.refreshAndBroadcastLiveViewStats = refreshAndBroadcastLiveViewStats;
exports.refreshLiveViewStats = refreshLiveViewStats;
exports.refreshPublicStats = refreshPublicStats;
exports.sendLivePlatformStatsToClient = sendLivePlatformStatsToClient;
exports.sendLiveViewStatsToClient = sendLiveViewStatsToClient;
exports.writeOperatorSettings = writeOperatorSettings;