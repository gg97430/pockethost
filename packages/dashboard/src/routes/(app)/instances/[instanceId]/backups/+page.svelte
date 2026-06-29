<script lang="ts">
  import { goto } from '$app/navigation'
  import { onDestroy, onMount } from 'svelte'
  import CronSchedulePicker from '$components/CronSchedulePicker.svelte'
  import FeatureTab from '$components/FeatureTab.svelte'
  import { validateCronExpression } from '$lib/cronExpression'
  import { NATURAL_CRON_EXAMPLES, parseNaturalCron } from '$lib/naturalCron'
  import {
    client,
    type InstanceBackup,
    type InstanceBackupPolicy,
    type InstanceLitestreamPolicy,
    type InstanceLitestreamPolicyResponse,
    type UpdateInstanceBackupPolicyInput,
    type UpdateInstanceLitestreamPolicyInput,
    type UploadProgress,
  } from '$src/pocketbase-client'
  import { instance } from '../store'

  type BackupOperation = {
    kind: 'backup' | 'restore'
    phase: string
    label: string
    percent: number
    startedAt: number
    updatedAt: number
    sourceSizeBytes: number
    compressedBytes: number
    mode?: 'in-place' | 'new-instance'
    targetInstanceId?: string
    targetSubdomain?: string
    error?: string
  }

  const RESTORE_OPERATION_STALE_MS = 36 * 60 * 60 * 1000
  const defaultPolicyDraft = (): UpdateInstanceBackupPolicyInput => ({
    enabled: false,
    cron: '0 2 * * *',
    localEnabled: true,
    remoteEnabled: false,
    localRetentionCount: 7,
    localRetentionDays: 14,
    remoteRetentionCount: 30,
    remoteRetentionDays: 90,
    activeBehavior: 'stop-restart',
  })
  const defaultLitestreamDraft = (): UpdateInstanceLitestreamPolicyInput => ({
    enabled: false,
    s3Endpoint: '',
    s3Bucket: '',
    s3Prefix: '',
    s3Region: 'auto',
    s3AccessKeyId: '',
    s3SecretAccessKey: '',
    s3ForcePathStyle: false,
    syncInterval: '10s',
    monitorInterval: '10s',
    checkpointInterval: '1m',
    snapshotInterval: '1h',
    snapshotRetention: '72h',
    validationInterval: '6h',
  })

  let backups: InstanceBackup[] = []
  let backupPolicy: InstanceBackupPolicy | null = null
  let backupPolicyDraft = defaultPolicyDraft()
  let backupPolicyS3Enabled = false
  let backupPolicyServerTimezone = 'Indian/Reunion'
  let backupPolicyNaturalSchedule = 'tous les jours à 2h'
  let litestreamPolicy: InstanceLitestreamPolicy | null = null
  let litestreamDraft = defaultLitestreamDraft()
  let litestreamCapabilities: InstanceLitestreamPolicyResponse['capabilities'] = {
    s3PerInstance: true,
    litestreamInstalled: false,
    pm2Installed: false,
    serviceName: '',
    configPath: '',
  }
  let backupPageTab: 'archive' | 'schedule' | 'litestream' = 'archive'
  let backupPolicyCustomCron = false
  let isPolicyLoading = true
  let isLitestreamLoading = true
  let policyAction = ''
  let litestreamAction = ''
  let policyErrorMessage = ''
  let litestreamErrorMessage = ''
  let isLoading = true
  let action = ''
  let errorMessage = ''
  let successMessage = ''
  let archiveFile: File | null = null
  let serverPath = ''
  let fileInput: HTMLInputElement | undefined
  let uploadProgress: UploadProgress | null = null
  let uploadPhase = ''
  let now = Date.now()
  let operationStartedAt = 0
  let pollTimer: ReturnType<typeof setInterval> | undefined
  let clockTimer: ReturnType<typeof setInterval> | undefined
  let lastSilentRefreshAt = 0

  $: ({ id, subdomain, cname, power } = $instance)
  $: displayName = cname || subdomain
  $: runningBackups = backups.filter((backup) => backup.status === 'running')
  $: hasRunningBackup = runningBackups.length > 0
  $: activeRunningBackup = runningBackups[0] || null
  $: activeRunningOperation = activeRunningBackup ? backupOperation(activeRunningBackup) : null
  $: activeRestoreBackup =
    backups.find((backup) => restoreActionMatchesBackup(backup) || isRestoreOperationActive(backup)) || null
  $: activeRestoreOperation = activeRestoreBackup ? restoreOperation(activeRestoreBackup) : null
  $: hasActiveRestore = backups.some(isRestoreOperationActive)
  $: isRestoreAction =
    action === 'restore:pending' || action.startsWith('restore:') || action.startsWith('restore-new:')
  $: isBusy = !!action || hasRunningBackup || hasActiveRestore
  $: liveOperationKind = activeRestoreOperation || isRestoreAction ? 'restore' : 'backup'
  $: liveOperation = liveOperationKind === 'restore' ? activeRestoreOperation : activeRunningOperation
  $: liveOperationVisible =
    action === 'create' || action === 'policy-run' || hasRunningBackup || isRestoreAction || hasActiveRestore
  $: liveOperationLabel =
    liveOperation?.label ||
    (liveOperationKind === 'restore'
      ? 'Restauration demandée'
      : action === 'policy-run'
        ? 'Sauvegarde automatique demandée'
        : 'Demande envoyée au serveur')
  $: liveOperationPercent =
    liveOperation?.percent ||
    (liveOperationKind === 'restore'
      ? isRestoreAction
        ? 8
        : 0
      : action === 'create' || action === 'policy-run'
        ? 6
        : 0)
  $: liveOperationStartedAt = liveOperation?.startedAt || operationStartedAt || now
  $: liveOperationElapsed = formatDuration(Math.max(0, now - liveOperationStartedAt))
  $: liveOperationSource = liveOperation?.sourceSizeBytes || 0
  $: liveOperationCompressed = liveOperation?.compressedBytes || 0
  $: liveOperationText =
    liveOperationKind === 'restore'
      ? 'La restauration travaille en arrière-plan. Vous pouvez laisser cette page ouverte, elle se rafraîchit automatiquement.'
      : 'La sauvegarde travaille en arrière-plan. Vous pouvez laisser cette page ouverte, elle se rafraîchit automatiquement.'
  $: liveOperationCount =
    liveOperationKind === 'restore' ? (hasActiveRestore || isRestoreAction ? 1 : 0) : runningBackups.length || 1
  $: policyStatusText = backupPolicy ? policyStatusLabel(backupPolicy.lastStatus) : 'Non configurée'
  $: backupPolicyNaturalResult = parseNaturalCron(backupPolicyNaturalSchedule, {
    timezoneLabel: backupPolicyServerTimezone,
  })
  $: litestreamStatusText = litestreamPolicy ? litestreamStatusLabel(litestreamPolicy.status) : 'Non configurée'
  $: litestreamRuntimeReady = litestreamCapabilities.litestreamInstalled && litestreamCapabilities.pm2Installed
  $: litestreamS3Ready = litestreamS3DraftValid(litestreamDraft)
  $: litestreamReady = litestreamRuntimeReady && litestreamS3Ready
  $: litestreamMissingText = litestreamMissingPrerequisites().join(', ')
  $: litestreamReplicaPreview = litestreamRemotePreview(litestreamDraft)
  $: policyDestinationText = [
    backupPolicyDraft.localEnabled ? 'Interne' : '',
    backupPolicyDraft.remoteEnabled ? 'S3/R2' : '',
  ]
    .filter(Boolean)
    .join(' + ')
  $: policyCanSave =
    !policyAction &&
    !!backupPolicyDraft.cron.trim() &&
    validateCronExpression(backupPolicyDraft.cron) &&
    (backupPolicyDraft.localEnabled || backupPolicyDraft.remoteEnabled) &&
    (!backupPolicyDraft.remoteEnabled || backupPolicyS3Enabled)
  $: litestreamCanSave =
    !isBusy &&
    !litestreamAction &&
    (!litestreamDraft.enabled || litestreamReady) &&
    litestreamDurationsValid(litestreamDraft)
  $: selectedArchiveLabel = archiveFile
    ? `${archiveFile.name} - ${formatBytes(archiveFile.size)}`
    : '1. Choisir un ZIP, TGZ ou TAR.GZ'
  $: uploadProgressLabel = uploadProgress
    ? uploadProgress.total > 0
      ? `${formatBytes(uploadProgress.loaded)} / ${formatBytes(uploadProgress.total)}`
      : `${formatBytes(uploadProgress.loaded)} envoyés`
    : ''
  $: uploadChunksLabel =
    uploadProgress?.totalChunks && uploadProgress.totalChunks > 1
      ? `${uploadProgress.uploadedChunks || 0}/${uploadProgress.totalChunks} morceaux`
      : ''
  $: uploadPhaseLabel =
    uploadPhase === 'starting'
      ? 'Préparation de l’upload'
      : uploadPhase === 'assembling'
        ? 'Upload terminé, assemblage serveur en cours...'
        : uploadPhase === 'processing'
          ? 'Vérification serveur en cours...'
          : uploadPhase === 'uploading'
            ? uploadChunksLabel
              ? 'Upload par morceaux en cours'
              : 'Upload en cours'
            : ''

  const formatBytes = (bytes: number) => {
    if (!bytes) return '0 o'
    const units = ['o', 'Ko', 'Mo', 'Go', 'To']
    const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
    const value = bytes / 1024 ** index
    return `${new Intl.NumberFormat('fr-FR', { maximumFractionDigits: value >= 10 ? 0 : 1 }).format(value)} ${units[index]}`
  }

  function formatDuration(durationMs: number) {
    const totalSeconds = Math.max(0, Math.floor(durationMs / 1000))
    const hours = Math.floor(totalSeconds / 3600)
    const minutes = Math.floor((totalSeconds % 3600) / 60)
    const seconds = totalSeconds % 60

    if (hours > 0) return `${hours} h ${String(minutes).padStart(2, '0')} min`
    if (minutes > 0) return `${minutes} min ${String(seconds).padStart(2, '0')} s`
    return `${seconds} s`
  }

  const formatDate = (value: string) => {
    if (!value) return '-'
    return new Intl.DateTimeFormat('fr-FR', {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(value))
  }

  function statusLabel(status: InstanceBackup['status']) {
    if (status === 'ready') return 'Prête'
    if (status === 'running') return 'En cours'
    return 'Échec'
  }

  function kindLabel(kind: InstanceBackup['kind']) {
    if (kind === 'pre-restore') return 'Avant restauration'
    if (kind === 'import') return 'Importée'
    if (kind === 'scheduled') return 'Automatique'
    return 'Manuelle'
  }

  function policyStatusLabel(status: InstanceBackupPolicy['lastStatus']) {
    if (status === 'ready') return 'Dernière sauvegarde OK'
    if (status === 'running') return 'Sauvegarde en cours'
    if (status === 'failed') return 'Dernière sauvegarde en échec'
    if (status === 'skipped') return 'Dernière exécution ignorée'
    return 'Jamais exécutée'
  }

  function policyStatusClass(status: InstanceBackupPolicy['lastStatus'] | undefined) {
    if (status === 'ready') return 'backup-policy-status--ready'
    if (status === 'running') return 'backup-policy-status--running'
    if (status === 'failed') return 'backup-policy-status--failed'
    if (status === 'skipped') return 'backup-policy-status--skipped'
    return 'backup-policy-status--never'
  }

  function litestreamStatusLabel(status: InstanceLitestreamPolicy['status']) {
    if (status === 'running') return 'Réplication active'
    if (status === 'configured') return 'Configurée'
    if (status === 'failed') return 'Erreur'
    if (status === 'unavailable') return 'Pré-requis manquant'
    return 'Désactivée'
  }

  function litestreamStatusClass(status: InstanceLitestreamPolicy['status'] | undefined) {
    if (status === 'running') return 'backup-policy-status--ready'
    if (status === 'configured') return 'backup-policy-status--running'
    if (status === 'failed') return 'backup-policy-status--failed'
    if (status === 'unavailable') return 'backup-policy-status--skipped'
    return 'backup-policy-status--never'
  }

  function litestreamDurationValid(value: string) {
    return /^[1-9]\d*(s|m|h)$/i.test(`${value || ''}`.trim())
  }

  function litestreamDurationsValid(draft: UpdateInstanceLitestreamPolicyInput) {
    return [
      draft.syncInterval,
      draft.monitorInterval,
      draft.checkpointInterval,
      draft.snapshotInterval,
      draft.snapshotRetention,
      draft.validationInterval,
    ].every((value) => litestreamDurationValid(`${value || ''}`))
  }

  function normalizedLitestreamPrefix(prefix: string) {
    return `${prefix || ''}`.trim().replace(/^\/+|\/+$/g, '').replace(/\/{2,}/g, '/')
  }

  function litestreamHasSecret(draft: UpdateInstanceLitestreamPolicyInput) {
    return !!`${draft.s3SecretAccessKey || ''}`.trim() || !!litestreamPolicy?.hasS3SecretAccessKey
  }

  function litestreamS3DraftValid(draft: UpdateInstanceLitestreamPolicyInput) {
    return (
      !!draft.s3Endpoint.trim() &&
      !!draft.s3Bucket.trim() &&
      !!draft.s3AccessKeyId.trim() &&
      litestreamHasSecret(draft)
    )
  }

  function litestreamRemotePreview(draft: UpdateInstanceLitestreamPolicyInput) {
    const prefix = normalizedLitestreamPrefix(draft.s3Prefix)
    const path = [prefix, 'litestream', 'instances', id, 'data.db'].filter(Boolean).join('/')
    const bucket = draft.s3Bucket.trim()
    return bucket ? `s3://${bucket}/${path}` : path || litestreamPolicy?.replicaPath || 'Non configurée'
  }

  function litestreamMissingPrerequisites() {
    const missing = []
    if (!litestreamCapabilities.litestreamInstalled) missing.push('Litestream')
    if (!litestreamCapabilities.pm2Installed) missing.push('PM2')
    if (!litestreamS3DraftValid(litestreamDraft)) missing.push('paramètres S3/R2')
    return missing
  }

  function syncPolicyDraft(policy: InstanceBackupPolicy) {
    backupPolicyDraft = {
      enabled: policy.enabled,
      cron: policy.cron || '0 2 * * *',
      localEnabled: policy.localEnabled,
      remoteEnabled: policy.remoteEnabled,
      localRetentionCount: policy.localRetentionCount,
      localRetentionDays: policy.localRetentionDays,
      remoteRetentionCount: policy.remoteRetentionCount,
      remoteRetentionDays: policy.remoteRetentionDays,
      activeBehavior: policy.activeBehavior,
    }
  }

  function applyNaturalBackupSchedule() {
    const result = parseNaturalCron(backupPolicyNaturalSchedule, {
      timezoneLabel: backupPolicyServerTimezone,
    })
    if (!result.ok) return

    backupPolicyDraft.cron = result.cron
  }

  function useNaturalBackupScheduleExample(example: string) {
    backupPolicyNaturalSchedule = example
    const result = parseNaturalCron(example, {
      timezoneLabel: backupPolicyServerTimezone,
    })
    if (result.ok) backupPolicyDraft.cron = result.cron
  }

  function syncLitestreamDraft(policy: InstanceLitestreamPolicy) {
    litestreamDraft = {
      enabled: policy.enabled,
      s3Endpoint: policy.s3Endpoint || '',
      s3Bucket: policy.s3Bucket || '',
      s3Prefix: policy.s3Prefix || '',
      s3Region: policy.s3Region || 'auto',
      s3AccessKeyId: policy.s3AccessKeyId || '',
      s3SecretAccessKey: '',
      s3ForcePathStyle: policy.s3ForcePathStyle,
      syncInterval: policy.syncInterval || '10s',
      monitorInterval: policy.monitorInterval || '10s',
      checkpointInterval: policy.checkpointInterval || '1m',
      snapshotInterval: policy.snapshotInterval || '1h',
      snapshotRetention: policy.snapshotRetention || '72h',
      validationInterval: policy.validationInterval || '6h',
    }
  }

  function manifestObject(manifest: unknown) {
    if (!manifest || typeof manifest !== 'object' || Array.isArray(manifest)) return {}
    return manifest as Record<string, unknown>
  }

  function backupOperation(backup: InstanceBackup): BackupOperation {
    const manifest = manifestObject(backup.manifest)
    const rawOperation = manifest.operation
    const operation =
      rawOperation && typeof rawOperation === 'object' && !Array.isArray(rawOperation)
        ? (rawOperation as Record<string, unknown>)
        : {}

    const startedAt = Date.parse(`${operation.startedAt || backup.created || ''}`)
    const updatedAt = Date.parse(`${operation.updatedAt || backup.updated || backup.created || ''}`)
    const sourceSizeBytes = Number(operation.sourceSizeBytes || backup.sizeBytes || manifest.sourceSizeBytes || 0)
    const compressedBytes = Number(operation.compressedBytes || backup.compressedBytes || 0)
    const rawPercent = Number(operation.percent || 0)

    return {
      kind: 'backup',
      phase: typeof operation.phase === 'string' ? operation.phase : backup.status,
      label:
        typeof operation.label === 'string' && operation.label.trim()
          ? operation.label
          : backup.status === 'running'
            ? 'Sauvegarde en cours'
            : statusLabel(backup.status),
      percent:
        Number.isFinite(rawPercent) && rawPercent > 0
          ? Math.max(0, Math.min(99, Math.round(rawPercent)))
          : backup.status === 'running'
            ? 12
            : backup.status === 'ready'
              ? 100
              : 0,
      startedAt: Number.isFinite(startedAt) ? startedAt : Date.now(),
      updatedAt: Number.isFinite(updatedAt) ? updatedAt : Date.now(),
      sourceSizeBytes: Number.isFinite(sourceSizeBytes) ? sourceSizeBytes : 0,
      compressedBytes: Number.isFinite(compressedBytes) ? compressedBytes : 0,
    }
  }

  function restoreActionMatchesBackup(backup: InstanceBackup) {
    return action === `restore:${backup.id}` || action === `restore-new:${backup.id}`
  }

  function restoreOperation(backup: InstanceBackup): BackupOperation {
    const manifest = manifestObject(backup.manifest)
    const rawOperation = manifest.restoreOperation
    const operation =
      rawOperation && typeof rawOperation === 'object' && !Array.isArray(rawOperation)
        ? (rawOperation as Record<string, unknown>)
        : {}

    const hasPendingAction = restoreActionMatchesBackup(backup)
    const startedAt = Date.parse(`${operation.startedAt || ''}`)
    const updatedAt = Date.parse(`${operation.updatedAt || backup.updated || backup.created || ''}`)
    const sourceSizeBytes = Number(operation.sourceSizeBytes || backup.sizeBytes || manifest.sourceSizeBytes || 0)
    const compressedBytes = Number(operation.compressedBytes || backup.compressedBytes || 0)
    const rawPercent = Number(operation.percent || 0)
    const phase =
      typeof operation.phase === 'string' && operation.phase ? operation.phase : hasPendingAction ? 'queued' : ''
    const mode = operation.mode === 'new-instance' ? 'new-instance' : 'in-place'

    return {
      kind: 'restore',
      phase,
      label:
        typeof operation.label === 'string' && operation.label.trim()
          ? operation.label
          : hasPendingAction
            ? mode === 'new-instance'
              ? 'Restauration vers une nouvelle instance'
              : 'Restauration en cours'
            : phase === 'ready'
              ? 'Restauration terminée'
              : phase === 'failed'
                ? 'Restauration échouée'
                : 'Restauration en cours',
      percent:
        Number.isFinite(rawPercent) && rawPercent > 0
          ? Math.max(0, Math.min(100, Math.round(rawPercent)))
          : hasPendingAction
            ? 8
            : phase === 'ready'
              ? 100
              : 0,
      startedAt: Number.isFinite(startedAt) ? startedAt : operationStartedAt || Date.now(),
      updatedAt: Number.isFinite(updatedAt) ? updatedAt : Date.now(),
      sourceSizeBytes: Number.isFinite(sourceSizeBytes) ? sourceSizeBytes : 0,
      compressedBytes: Number.isFinite(compressedBytes) ? compressedBytes : 0,
      mode,
      targetInstanceId: typeof operation.targetInstanceId === 'string' ? operation.targetInstanceId : '',
      targetSubdomain: typeof operation.targetSubdomain === 'string' ? operation.targetSubdomain : '',
      error: typeof operation.error === 'string' ? operation.error : '',
    }
  }

  function isRestoreOperationActive(backup: InstanceBackup) {
    const operation = restoreOperation(backup)
    if (!operation.phase || operation.phase === 'ready' || operation.phase === 'failed') return false
    return now - operation.updatedAt < RESTORE_OPERATION_STALE_MS
  }

  function shouldShowRestoreProgress(backup: InstanceBackup) {
    return restoreActionMatchesBackup(backup) || isRestoreOperationActive(backup)
  }

  function visibleOperationForBackup(backup: InstanceBackup) {
    return shouldShowRestoreProgress(backup) ? restoreOperation(backup) : backupOperation(backup)
  }

  function completedRestoreOperation(backup: InstanceBackup) {
    const operation = restoreOperation(backup)
    return operation.phase === 'ready' ? operation : null
  }

  function restoreTagLabel(backup: InstanceBackup) {
    const operation = completedRestoreOperation(backup)
    if (!operation) return ''
    if (operation.mode === 'new-instance') {
      return `Restaurée vers ${operation.targetSubdomain || 'nouvelle instance'}`
    }
    return 'Restaurée sur cette instance'
  }

  function restoreTagTitle(backup: InstanceBackup) {
    const operation = completedRestoreOperation(backup)
    if (!operation) return ''
    return `Restauration terminée le ${formatDate(new Date(operation.updatedAt).toISOString())}`
  }

  const suggestedRestoreSubdomain = () => {
    const base = (subdomain || displayName || 'instance')
      .toLowerCase()
      .replace(/[^a-z0-9-]/g, '-')
      .replace(/-+/g, '-')
      .replace(/^-+|-+$/g, '')
    const normalized = base.match(/^[a-z]/) ? base : `base-${base}`
    return `${normalized.slice(0, 31).replace(/-+$/g, '')}-restore`
  }

  const loadBackups = async ({ silent = false }: { silent?: boolean } = {}) => {
    if (!silent) {
      isLoading = true
      errorMessage = ''
    }
    try {
      backups = (await client().listInstanceBackups(id)).backups
    } catch (error) {
      if (!silent) {
        errorMessage = error instanceof Error ? client().parseError(error)[0] || error.message : `${error}`
      }
    } finally {
      if (!silent) isLoading = false
    }
  }

  const loadBackupPolicy = async ({ silent = false }: { silent?: boolean } = {}) => {
    if (!silent) {
      isPolicyLoading = true
      policyErrorMessage = ''
    }
    try {
      const result = await client().getInstanceBackupPolicy(id)
      backupPolicy = result.policy
      backupPolicyS3Enabled = result.capabilities.s3Enabled
      backupPolicyServerTimezone = result.capabilities.serverTimezone || backupPolicyServerTimezone
      syncPolicyDraft(result.policy)
    } catch (error) {
      if (!silent) {
        policyErrorMessage = error instanceof Error ? client().parseError(error)[0] || error.message : `${error}`
      }
    } finally {
      if (!silent) isPolicyLoading = false
    }
  }

  const loadLitestreamPolicy = async ({ silent = false }: { silent?: boolean } = {}) => {
    if (!silent) {
      isLitestreamLoading = true
      litestreamErrorMessage = ''
    }
    try {
      const result = await client().getInstanceLitestreamPolicy(id)
      litestreamPolicy = result.policy
      litestreamCapabilities = result.capabilities
      syncLitestreamDraft(result.policy)
    } catch (error) {
      if (!silent) {
        litestreamErrorMessage = error instanceof Error ? client().parseError(error)[0] || error.message : `${error}`
      }
    } finally {
      if (!silent) isLitestreamLoading = false
    }
  }

  const refreshBackupsSilently = async () => {
    const current = Date.now()
    if (current - lastSilentRefreshAt < 1200) return

    lastSilentRefreshAt = current
    await loadBackups({ silent: true })
  }

  onMount(() => {
    void loadBackups()
    void loadBackupPolicy()
    void loadLitestreamPolicy()
    clockTimer = setInterval(() => {
      now = Date.now()
    }, 1000)
    pollTimer = setInterval(() => {
      if (action || backups.some((backup) => backup.status === 'running') || backups.some(isRestoreOperationActive)) {
        void refreshBackupsSilently()
      }
    }, 3000)
  })

  onDestroy(() => {
    if (clockTimer) clearInterval(clockTimer)
    if (pollTimer) clearInterval(pollTimer)
  })

  const createBackup = async () => {
    if (isBusy) return

    const confirmed = window.confirm(
      `Créer une sauvegarde complète de ${displayName} ?${power ? "\n\nL'instance sera arrêtée puis redémarrée automatiquement." : ''}`
    )
    if (!confirmed) return

    action = 'create'
    operationStartedAt = Date.now()
    errorMessage = ''
    successMessage = ''
    try {
      setTimeout(() => void refreshBackupsSilently(), 1200)
      const result = await client().createInstanceBackup(id)
      backups = [result.backup, ...backups.filter((backup) => backup.id !== result.backup.id)]
      successMessage = 'Sauvegarde créée'
    } catch (error) {
      errorMessage = error instanceof Error ? client().parseError(error)[0] || error.message : `${error}`
      await loadBackups()
    } finally {
      action = ''
      operationStartedAt = 0
    }
  }

  const saveBackupPolicy = async () => {
    if (!policyCanSave) return

    policyAction = 'save'
    policyErrorMessage = ''
    successMessage = ''
    try {
      const result = await client().updateInstanceBackupPolicy(id, backupPolicyDraft)
      backupPolicy = result.policy
      backupPolicyS3Enabled = result.capabilities.s3Enabled
      backupPolicyServerTimezone = result.capabilities.serverTimezone || backupPolicyServerTimezone
      syncPolicyDraft(result.policy)
      successMessage = backupPolicyDraft.enabled ? 'Planification enregistrée' : 'Planification désactivée'
    } catch (error) {
      policyErrorMessage = error instanceof Error ? client().parseError(error)[0] || error.message : `${error}`
    } finally {
      policyAction = ''
    }
  }

  const saveLitestreamPolicy = async () => {
    if (!litestreamCanSave) return

    litestreamAction = 'save'
    litestreamErrorMessage = ''
    successMessage = ''
    try {
      const result = await client().updateInstanceLitestreamPolicy(id, litestreamDraft)
      litestreamPolicy = result.policy
      litestreamCapabilities = result.capabilities
      syncLitestreamDraft(result.policy)
      successMessage = litestreamDraft.enabled ? 'Réplication Litestream activée' : 'Réplication Litestream désactivée'
    } catch (error) {
      litestreamErrorMessage = error instanceof Error ? client().parseError(error)[0] || error.message : `${error}`
      await loadLitestreamPolicy({ silent: true })
    } finally {
      litestreamAction = ''
    }
  }

  const runBackupPolicyNow = async () => {
    if (isBusy || policyAction || !backupPolicyDraft.enabled) return

    const confirmed = window.confirm(
      `Lancer maintenant la sauvegarde automatique de ${displayName} ?${power && backupPolicyDraft.activeBehavior === 'stop-restart' ? "\n\nL'instance sera arrêtée puis redémarrée automatiquement." : ''}`
    )
    if (!confirmed) return

    backupPageTab = 'archive'
    action = 'policy-run'
    policyAction = 'run'
    operationStartedAt = Date.now()
    errorMessage = ''
    policyErrorMessage = ''
    successMessage = ''
    try {
      setTimeout(() => void refreshBackupsSilently(), 1200)
      const result = await client().runInstanceBackupPolicy(id)
      backupPolicy = result.policy
      syncPolicyDraft(result.policy)
      if (result.backup) {
        backups = [result.backup, ...backups.filter((backup) => backup.id !== result.backup?.id)]
      }
      await loadBackups({ silent: true })
      successMessage = result.backup ? 'Sauvegarde automatique créée' : 'Exécution automatique terminée'
    } catch (error) {
      policyErrorMessage = error instanceof Error ? client().parseError(error)[0] || error.message : `${error}`
      await loadBackupPolicy({ silent: true })
      await loadBackups({ silent: true })
    } finally {
      action = ''
      policyAction = ''
      operationStartedAt = 0
    }
  }

  const importArchive = async () => {
    if (isBusy || !archiveFile) return

    const confirmed = window.confirm(
      `Importer ${archiveFile.name} comme sauvegarde de ${displayName} ?\n\nFormats acceptes: dossier pb_data complet, ou fichiers PocketBase a la racine du ZIP comme data.db.`
    )
    if (!confirmed) return

    action = 'import:file'
    errorMessage = ''
    successMessage = ''
    uploadProgress = { loaded: 0, total: archiveFile.size, percent: 0 }
    uploadPhase = 'uploading'
    try {
      const result = await client().importInstanceBackup(id, {
        file: archiveFile,
        onProgress: (progress) => {
          uploadProgress = progress
          uploadPhase = progress.phase || (progress.percent >= 100 ? 'processing' : 'uploading')
        },
      })
      backups = [result.backup, ...backups.filter((backup) => backup.id !== result.backup.id)]
      archiveFile = null
      if (fileInput) fileInput.value = ''
      successMessage = 'Archive importée'
    } catch (error) {
      errorMessage = error instanceof Error ? client().parseError(error)[0] || error.message : `${error}`
      await loadBackups()
    } finally {
      action = ''
      uploadProgress = null
      uploadPhase = ''
    }
  }

  const importServerArchive = async () => {
    const path = serverPath.trim()
    if (isBusy || !path) return

    const confirmed = window.confirm(
      `Importer l'archive serveur suivante pour ${displayName} ?\n\n${path}\n\nLe fichier doit être dans le dossier serveur autorisé.`
    )
    if (!confirmed) return

    action = 'import:server'
    errorMessage = ''
    successMessage = ''
    try {
      const result = await client().importInstanceBackup(id, { serverPath: path })
      backups = [result.backup, ...backups.filter((backup) => backup.id !== result.backup.id)]
      serverPath = ''
      successMessage = 'Archive serveur importée'
    } catch (error) {
      errorMessage = error instanceof Error ? client().parseError(error)[0] || error.message : `${error}`
      await loadBackups()
    } finally {
      action = ''
    }
  }

  const downloadBackup = async (backup: InstanceBackup) => {
    if (isBusy || backup.status !== 'ready') return

    action = `download:${backup.id}`
    errorMessage = ''
    successMessage = ''
    try {
      await client().downloadInstanceBackup(id, backup)
    } catch (error) {
      errorMessage = error instanceof Error ? client().parseError(error)[0] || error.message : `${error}`
    } finally {
      action = ''
    }
  }

  const restoreBackup = async (backup: InstanceBackup) => {
    if (isBusy || backup.status !== 'ready') return

    const confirmed = window.confirm(
      `Restaurer ${displayName} depuis cette sauvegarde ?\n\nUne sauvegarde de sécurité sera créée avant restauration. Si l'instance est active, elle sera arrêtée puis redémarrée après succès.`
    )
    if (!confirmed) return

    action = `restore:${backup.id}`
    operationStartedAt = Date.now()
    errorMessage = ''
    successMessage = ''
    try {
      setTimeout(() => void refreshBackupsSilently(), 1200)
      await client().restoreInstanceBackup(id, backup.id)
      successMessage = 'Instance restaurée'
      await loadBackups()
    } catch (error) {
      errorMessage = error instanceof Error ? client().parseError(error)[0] || error.message : `${error}`
      await loadBackups()
    } finally {
      action = ''
      operationStartedAt = 0
    }
  }

  const restoreBackupToNewInstance = async (backup: InstanceBackup) => {
    if (isBusy || backup.status !== 'ready') return

    const subdomain = window.prompt(
      `Nom de la nouvelle instance à créer depuis ${backup.filename || backup.id} ?\n\nElle sera créée éteinte, sans écraser ${displayName}.`,
      suggestedRestoreSubdomain()
    )
    if (subdomain === null) return

    action = `restore-new:${backup.id}`
    operationStartedAt = Date.now()
    errorMessage = ''
    successMessage = ''
    try {
      setTimeout(() => void refreshBackupsSilently(), 1200)
      const result = await client().restoreInstanceBackupToNewInstance(id, backup.id, { subdomain: subdomain.trim() })
      successMessage = 'Nouvelle instance restaurée'
      await goto(`/instances/${result.instance.id}`)
    } catch (error) {
      errorMessage = error instanceof Error ? client().parseError(error)[0] || error.message : `${error}`
      await loadBackups()
    } finally {
      action = ''
      operationStartedAt = 0
    }
  }

  const deleteBackup = async (backup: InstanceBackup) => {
    if (isBusy) return

    const confirmed = window.confirm(`Supprimer définitivement la sauvegarde ${backup.filename || backup.id} ?`)
    if (!confirmed) return

    action = `delete:${backup.id}`
    errorMessage = ''
    successMessage = ''
    try {
      await client().deleteInstanceBackup(id, backup.id)
      backups = backups.filter((item) => item.id !== backup.id)
      successMessage = 'Sauvegarde supprimée'
    } catch (error) {
      errorMessage = error instanceof Error ? client().parseError(error)[0] || error.message : `${error}`
    } finally {
      action = ''
    }
  }
</script>

<FeatureTab title="Sauvegardes" bind:errorMessage {successMessage} successFlash>
  <svelte:fragment slot="summary">
    <p>
      Les sauvegardes compressées incluent <code>pb_data</code>, <code>pb_public</code>, <code>pb_migrations</code> et
      <code>pb_hooks</code>. Les logs sont exclus.
    </p>
  </svelte:fragment>

  <svelte:fragment slot="cta">
    {#if backupPageTab === 'archive'}
      <div class="backup-cta">
        <button type="button" class="backup-import-zip-cta" disabled={isBusy} onclick={() => fileInput?.click()}>
          <wa-icon name="file-zipper"></wa-icon>
          Importer ZIP à restaurer
        </button>
        <button type="button" class="backup-create-btn" disabled={isBusy} onclick={createBackup}>
          <wa-icon name={action === 'create' ? 'rotate' : 'floppy-disk'}></wa-icon>
          {action === 'create' ? 'Sauvegarde...' : 'Créer une sauvegarde'}
        </button>
      </div>
    {/if}
  </svelte:fragment>

  <div class="backup-page-tabs">
    <div class="backup-page-tabs__nav" role="tablist" aria-label="Gestion des sauvegardes">
      <button
        type="button"
        role="tab"
        id="backup-tab-archive-tab"
        aria-selected={backupPageTab === 'archive'}
        aria-controls="backup-tab-archive"
        class:active={backupPageTab === 'archive'}
        onclick={() => (backupPageTab = 'archive')}
      >
        <wa-icon name="box-archive"></wa-icon>
        <span>Sauvegarde / restauration</span>
        <small>{backups.length} archive{backups.length > 1 ? 's' : ''}</small>
      </button>
      <button
        type="button"
        role="tab"
        id="backup-tab-schedule-tab"
        aria-selected={backupPageTab === 'schedule'}
        aria-controls="backup-tab-schedule"
        class:active={backupPageTab === 'schedule'}
        onclick={() => (backupPageTab = 'schedule')}
      >
        <wa-icon name="clock"></wa-icon>
        <span>Planification</span>
        <small>{policyStatusText}</small>
      </button>
      <button
        type="button"
        role="tab"
        id="backup-tab-litestream-tab"
        aria-selected={backupPageTab === 'litestream'}
        aria-controls="backup-tab-litestream"
        class:active={backupPageTab === 'litestream'}
        onclick={() => (backupPageTab = 'litestream')}
      >
        <wa-icon name="database"></wa-icon>
        <span>Litestream</span>
        <small>{litestreamStatusText}</small>
      </button>
    </div>

    {#if backupPageTab === 'archive'}
      <div
        id="backup-tab-archive"
        class="backup-tab-panel"
        role="tabpanel"
        aria-labelledby="backup-tab-archive-tab"
      >
  <section class="backup-import">
    <div class="backup-import-copy">
      <strong>Importer une sauvegarde ZIP à restaurer</strong>
      <span>
        Réservé superadmin. Étape 1 : importez le ZIP. Étape 2 : cliquez <strong>Restaurer</strong> sur la ligne créée.
        Le ZIP peut contenir <code>pb_data</code>, ou directement les fichiers SQLite comme <code>data.db</code>.
      </span>
    </div>

    <div class="backup-import-grid">
      <label class="backup-file">
        <input
          bind:this={fileInput}
          type="file"
          accept=".zip,.tgz,.tar.gz,application/zip,application/gzip"
          disabled={isBusy}
          onchange={(event) => {
            archiveFile = event.currentTarget.files?.[0] || null
          }}
        />
        <span>{selectedArchiveLabel}</span>
      </label>
      <button type="button" class="backup-import-btn" disabled={isBusy || !archiveFile} onclick={importArchive}>
        <wa-icon name={action === 'import:file' ? 'rotate' : 'upload'}></wa-icon>
        {action === 'import:file' ? 'Import...' : '2. Importer le ZIP'}
      </button>
    </div>

    {#if uploadProgress}
      <div class="backup-upload-progress" aria-live="polite">
        <div class="backup-upload-progress__row">
          <strong>{uploadPhaseLabel}</strong>
          <span
            >{uploadProgress.percent}% - {uploadProgressLabel}{uploadChunksLabel ? ` - ${uploadChunksLabel}` : ''}</span
          >
        </div>
        <div
          class="backup-upload-progress__track"
          role="progressbar"
          aria-valuemin="0"
          aria-valuemax="100"
          aria-valuenow={uploadProgress.percent}
        >
          <span style={`width: ${uploadProgress.percent}%`}></span>
        </div>
      </div>
    {/if}

    <div class="backup-import-grid">
      <input
        class="backup-server-path"
        bind:value={serverPath}
        disabled={isBusy}
        placeholder="Chemin serveur, ex. /home/ubuntu/.local/share/pockethost/data/imports/backup.zip"
      />
      <button
        type="button"
        class="backup-import-btn backup-import-btn--server"
        disabled={isBusy || !serverPath.trim()}
        onclick={importServerArchive}
      >
        <wa-icon name={action === 'import:server' ? 'rotate' : 'server'}></wa-icon>
        {action === 'import:server' ? 'Import...' : 'Importer ZIP serveur'}
      </button>
    </div>
  </section>
      </div>
    {/if}

    {#if backupPageTab === 'schedule'}
      <div
        id="backup-tab-schedule"
        class="backup-tab-panel backup-policy"
        role="tabpanel"
        aria-labelledby="backup-tab-schedule-tab"
      >
    <div class="backup-policy__header">
      <div>
        <strong>Planification automatique</strong>
        <span>Créer et purger les sauvegardes selon une politique par instance.</span>
      </div>
      <span class="backup-policy-status {policyStatusClass(backupPolicy?.lastStatus)}">{policyStatusText}</span>
    </div>

    {#if policyErrorMessage}
      <p class="backup-policy-error">{policyErrorMessage}</p>
    {/if}

    {#if isPolicyLoading}
      <div class="backup-policy-loading">Chargement de la planification...</div>
    {:else}
      <div class="backup-policy-grid">
        <label class="backup-policy-toggle">
          <input type="checkbox" bind:checked={backupPolicyDraft.enabled} disabled={!!policyAction} />
          <span>
            <strong>Activer</strong>
            <small>{backupPolicyDraft.enabled ? 'Planification active' : 'Planification inactive'}</small>
          </span>
        </label>

        <div class="backup-policy-field backup-policy-field--wide">
          <label for="backup-policy-cron">Fréquence</label>
          <div class="backup-cron-wizard">
            <div class="backup-cron-wizard__main">
              <input
                id="backup-policy-natural-schedule"
                class="backup-policy-input"
                bind:value={backupPolicyNaturalSchedule}
                disabled={!!policyAction}
                placeholder="Ex. tous les jours à 2h"
                aria-label="Planification en langage naturel"
              />
              <button
                type="button"
                class="backup-policy-secondary"
                disabled={!!policyAction || !backupPolicyNaturalResult.ok}
                onclick={applyNaturalBackupSchedule}
              >
                <wa-icon name="wand-magic-sparkles"></wa-icon>
                Convertir
              </button>
            </div>
            <div class="backup-cron-wizard__examples">
              {#each NATURAL_CRON_EXAMPLES as example}
                <button
                  type="button"
                  class="backup-cron-chip"
                  disabled={!!policyAction}
                  onclick={() => useNaturalBackupScheduleExample(example)}
                >
                  {example}
                </button>
              {/each}
            </div>
            <div class="backup-cron-timezone">
              Fuseau serveur : <strong>{backupPolicyServerTimezone}</strong>
            </div>
            {#if backupPolicyNaturalResult.ok}
              <div class="backup-cron-result backup-cron-result--ok">
                <strong>{backupPolicyNaturalResult.cron}</strong>
                <span>{backupPolicyNaturalResult.localDescription}</span>
                <span>{backupPolicyNaturalResult.serverDescription}</span>
              </div>
            {:else}
              <div class="backup-cron-result backup-cron-result--error">
                {backupPolicyNaturalResult.message}
              </div>
            {/if}
          </div>
          <CronSchedulePicker
            id="backup-policy-cron"
            bind:value={backupPolicyDraft.cron}
            bind:customMode={backupPolicyCustomCron}
            timezoneLabel={backupPolicyServerTimezone}
          />
          {#if backupPolicyCustomCron}
            <input
              class="backup-policy-input"
              bind:value={backupPolicyDraft.cron}
              placeholder="0 2 * * *"
              aria-label="Expression cron personnalisée"
            />
          {/if}
          {#if backupPolicyDraft.cron && !validateCronExpression(backupPolicyDraft.cron)}
            <span class="backup-policy-help backup-policy-help--error">Expression cron invalide.</span>
          {:else}
            <span class="backup-policy-help">
              Heure serveur ({backupPolicyServerTimezone}). Exemple : <code>0 2 * * *</code> tous les jours à 02:00.
            </span>
          {/if}
        </div>

        <div class="backup-policy-field">
          <span class="backup-policy-field-label">Destinations</span>
          <label class="backup-policy-check">
            <input type="checkbox" bind:checked={backupPolicyDraft.localEnabled} disabled={!!policyAction} />
            <span>Interne serveur</span>
          </label>
          <label class="backup-policy-check">
            <input
              type="checkbox"
              bind:checked={backupPolicyDraft.remoteEnabled}
              disabled={!!policyAction || !backupPolicyS3Enabled}
            />
            <span>S3/R2 {backupPolicyS3Enabled ? '' : 'non configuré'}</span>
          </label>
          <span class="backup-policy-help">Actif : {policyDestinationText || 'aucune destination'}</span>
        </div>

        <div class="backup-policy-field">
          <label for="backup-policy-active">Instance active</label>
          <select
            id="backup-policy-active"
            class="backup-policy-input"
            bind:value={backupPolicyDraft.activeBehavior}
            disabled={!!policyAction}
          >
            <option value="stop-restart">Arrêter puis redémarrer</option>
            <option value="skip-active">Ignorer si active</option>
          </select>
          <span class="backup-policy-help">Le mode fiable arrête l'instance avant la copie.</span>
        </div>

        <div class="backup-policy-field">
          <span class="backup-policy-field-label">Rétention interne</span>
          <div class="backup-policy-retention">
            <input
              class="backup-policy-input"
              type="number"
              min="0"
              max="3650"
              bind:value={backupPolicyDraft.localRetentionCount}
              disabled={!!policyAction || !backupPolicyDraft.localEnabled}
              aria-label="Nombre de sauvegardes internes à garder"
            />
            <span>sauvegardes</span>
            <input
              class="backup-policy-input"
              type="number"
              min="0"
              max="3650"
              bind:value={backupPolicyDraft.localRetentionDays}
              disabled={!!policyAction || !backupPolicyDraft.localEnabled}
              aria-label="Nombre de jours de sauvegardes internes à garder"
            />
            <span>jours</span>
          </div>
          <span class="backup-policy-help">0 = illimité. Les sauvegardes manuelles ne sont pas purgées.</span>
        </div>

        <div class="backup-policy-field">
          <span class="backup-policy-field-label">Rétention S3/R2</span>
          <div class="backup-policy-retention">
            <input
              class="backup-policy-input"
              type="number"
              min="0"
              max="3650"
              bind:value={backupPolicyDraft.remoteRetentionCount}
              disabled={!!policyAction || !backupPolicyDraft.remoteEnabled}
              aria-label="Nombre de sauvegardes S3 à garder"
            />
            <span>sauvegardes</span>
            <input
              class="backup-policy-input"
              type="number"
              min="0"
              max="3650"
              bind:value={backupPolicyDraft.remoteRetentionDays}
              disabled={!!policyAction || !backupPolicyDraft.remoteEnabled}
              aria-label="Nombre de jours de sauvegardes S3 à garder"
            />
            <span>jours</span>
          </div>
          <span class="backup-policy-help">La purge S3 ne touche que les sauvegardes automatiques.</span>
        </div>
      </div>

      <div class="backup-policy-footer">
        <div class="backup-policy-last">
          <span>Dernière exécution : {backupPolicy?.lastRunAt ? formatDate(backupPolicy.lastRunAt) : '-'}</span>
          <span>Dernier succès : {backupPolicy?.lastSuccessAt ? formatDate(backupPolicy.lastSuccessAt) : '-'}</span>
          {#if backupPolicy?.lastDurationSeconds}
            <span>Durée : {formatDuration(backupPolicy.lastDurationSeconds * 1000)}</span>
          {/if}
          {#if backupPolicy?.lastError}
            <span class="backup-policy-last-error">{backupPolicy.lastError}</span>
          {/if}
        </div>
        <div class="backup-policy-actions">
          <button
            type="button"
            class="backup-policy-secondary"
            disabled={isBusy || !!policyAction}
            onclick={runBackupPolicyNow}
          >
            <wa-icon name={policyAction === 'run' ? 'rotate' : 'play'}></wa-icon>
            {policyAction === 'run' ? 'Exécution...' : 'Lancer maintenant'}
          </button>
          <button type="button" class="backup-policy-primary" disabled={!policyCanSave} onclick={saveBackupPolicy}>
            <wa-icon name={policyAction === 'save' ? 'rotate' : 'floppy-disk'}></wa-icon>
            {policyAction === 'save' ? 'Enregistrement...' : 'Enregistrer'}
          </button>
        </div>
      </div>
    {/if}
      </div>
    {/if}

    {#if backupPageTab === 'litestream'}
      <div
        id="backup-tab-litestream"
        class="backup-tab-panel backup-policy backup-policy--litestream"
        role="tabpanel"
        aria-labelledby="backup-tab-litestream-tab"
      >
    <div class="backup-policy__header">
      <div>
        <strong>Litestream à la demande</strong>
        <span>Répliquer <code>data.db</code> en continu vers S3/R2 uniquement quand cette option est activée.</span>
      </div>
      <span class="backup-policy-status {litestreamStatusClass(litestreamPolicy?.status)}">{litestreamStatusText}</span>
    </div>

    {#if litestreamErrorMessage}
      <p class="backup-policy-error">{litestreamErrorMessage}</p>
    {/if}

    {#if isLitestreamLoading}
      <div class="backup-policy-loading">Chargement de Litestream...</div>
    {:else}
      {#if !litestreamReady}
        <div class="backup-policy-warning">
          Pré-requis manquant : {litestreamMissingText || 'configuration serveur'}.
        </div>
      {/if}

      <div class="backup-policy-note">
        Litestream accélère la reprise de <code>data.db</code>. Les fichiers uploadés, hooks et migrations restent couverts
        par les sauvegardes complètes.
      </div>

      <div class="backup-policy-grid">
        <label class="backup-policy-toggle">
          <input
            type="checkbox"
            bind:checked={litestreamDraft.enabled}
            disabled={!!litestreamAction || (!litestreamRuntimeReady && !litestreamDraft.enabled)}
          />
          <span>
            <strong>Activer</strong>
            <small>{litestreamDraft.enabled ? 'Réplication active demandée' : 'Configuration prête à enregistrer'}</small>
          </span>
        </label>

        <div class="backup-policy-section-title backup-policy-field--wide">
          <strong>Paramètres S3/R2 de cette instance</strong>
          <span>Chaque instance peut utiliser son propre endpoint, bucket et jeu de clés.</span>
        </div>

        <div class="backup-policy-field">
          <label for="litestream-s3-endpoint">Endpoint</label>
          <input
            id="litestream-s3-endpoint"
            class="backup-policy-input"
            bind:value={litestreamDraft.s3Endpoint}
            disabled={!!litestreamAction}
            placeholder="https://xxxxxxxx.r2.cloudflarestorage.com"
            autocomplete="off"
          />
          <span class="backup-policy-help">URL de l’endpoint S3 compatible.</span>
        </div>

        <div class="backup-policy-field">
          <label for="litestream-s3-bucket">Bucket</label>
          <input
            id="litestream-s3-bucket"
            class="backup-policy-input"
            bind:value={litestreamDraft.s3Bucket}
            disabled={!!litestreamAction}
            placeholder="mon-bucket"
            autocomplete="off"
          />
          <span class="backup-policy-help">Bucket cible pour cette instance.</span>
        </div>

        <div class="backup-policy-field">
          <label for="litestream-s3-prefix">Préfixe</label>
          <input
            id="litestream-s3-prefix"
            class="backup-policy-input"
            bind:value={litestreamDraft.s3Prefix}
            disabled={!!litestreamAction}
            placeholder="client-a"
            autocomplete="off"
          />
          <span class="backup-policy-help">Optionnel. Un chemin Litestream sera ajouté automatiquement.</span>
        </div>

        <div class="backup-policy-field">
          <label for="litestream-s3-region">Région</label>
          <input
            id="litestream-s3-region"
            class="backup-policy-input"
            bind:value={litestreamDraft.s3Region}
            disabled={!!litestreamAction}
            placeholder="auto"
            autocomplete="off"
          />
          <span class="backup-policy-help">Pour R2, gardez généralement <code>auto</code>.</span>
        </div>

        <div class="backup-policy-field">
          <label for="litestream-s3-access-key">Access key ID</label>
          <input
            id="litestream-s3-access-key"
            class="backup-policy-input"
            bind:value={litestreamDraft.s3AccessKeyId}
            disabled={!!litestreamAction}
            placeholder="Access key ID"
            autocomplete="off"
          />
          <span class="backup-policy-help">Identifiant de clé autorisé sur ce bucket.</span>
        </div>

        <div class="backup-policy-field">
          <label for="litestream-s3-secret-key">Secret access key</label>
          <input
            id="litestream-s3-secret-key"
            class="backup-policy-input"
            type="password"
            bind:value={litestreamDraft.s3SecretAccessKey}
            disabled={!!litestreamAction}
            placeholder={litestreamPolicy?.hasS3SecretAccessKey ? 'Déjà enregistrée' : 'Secret access key'}
            autocomplete="new-password"
          />
          <span class="backup-policy-help">
            {litestreamPolicy?.hasS3SecretAccessKey ? 'Laissez vide pour conserver la clé existante.' : 'Requise pour activer Litestream.'}
          </span>
        </div>

        <label class="backup-policy-check backup-policy-field backup-policy-field--wide">
          <input type="checkbox" bind:checked={litestreamDraft.s3ForcePathStyle} disabled={!!litestreamAction} />
          <span>Forcer le path-style S3</span>
        </label>

        <div class="backup-policy-field">
          <label for="litestream-sync">Synchronisation</label>
          <input
            id="litestream-sync"
            class="backup-policy-input"
            bind:value={litestreamDraft.syncInterval}
            disabled={!!litestreamAction}
            placeholder="10s"
          />
          <span class="backup-policy-help">Intervalle d’envoi vers S3/R2.</span>
        </div>

        <div class="backup-policy-field">
          <label for="litestream-snapshot">Snapshot complet</label>
          <input
            id="litestream-snapshot"
            class="backup-policy-input"
            bind:value={litestreamDraft.snapshotInterval}
            disabled={!!litestreamAction}
            placeholder="1h"
          />
          <span class="backup-policy-help">Point complet périodique.</span>
        </div>

        <div class="backup-policy-field">
          <label for="litestream-retention">Rétention</label>
          <input
            id="litestream-retention"
            class="backup-policy-input"
            bind:value={litestreamDraft.snapshotRetention}
            disabled={!!litestreamAction}
            placeholder="72h"
          />
          <span class="backup-policy-help">Fenêtre restaurable Litestream.</span>
        </div>

        <div class="backup-policy-field">
          <label for="litestream-validation">Validation</label>
          <input
            id="litestream-validation"
            class="backup-policy-input"
            bind:value={litestreamDraft.validationInterval}
            disabled={!!litestreamAction}
            placeholder="6h"
          />
          <span class="backup-policy-help">Contrôle périodique de continuité.</span>
        </div>

        <div class="backup-policy-field backup-policy-field--wide">
          <span class="backup-policy-field-label">Destination distante</span>
          <code class="backup-policy-code">{litestreamReplicaPreview}</code>
          <span class="backup-policy-help">
            Service PM2 : {litestreamCapabilities.serviceName || 'pockethost-litestream'}
          </span>
        </div>
      </div>

      {#if !litestreamDurationsValid(litestreamDraft)}
        <p class="backup-policy-error">Les durées doivent utiliser le format <code>10s</code>, <code>5m</code> ou <code>1h</code>.</p>
      {/if}

      <div class="backup-policy-footer">
        <div class="backup-policy-last">
          <span>Démarré : {litestreamPolicy?.lastStartedAt ? formatDate(litestreamPolicy.lastStartedAt) : '-'}</span>
          <span>Contrôlé : {litestreamPolicy?.lastCheckedAt ? formatDate(litestreamPolicy.lastCheckedAt) : '-'}</span>
          {#if litestreamPolicy?.lastError}
            <span class="backup-policy-last-error">{litestreamPolicy.lastError}</span>
          {/if}
        </div>
        <div class="backup-policy-actions">
          <button type="button" class="backup-policy-primary" disabled={!litestreamCanSave} onclick={saveLitestreamPolicy}>
            <wa-icon name={litestreamAction === 'save' ? 'rotate' : 'floppy-disk'}></wa-icon>
            {litestreamAction === 'save' ? 'Enregistrement...' : 'Enregistrer Litestream'}
          </button>
        </div>
      </div>
    {/if}
  </div>
    {/if}
  </div>

  {#if backupPageTab === 'archive'}
    {#if liveOperationVisible}
      <section class="backup-live" aria-live="polite">
      <div class="backup-live__icon">
        <wa-icon name="rotate"></wa-icon>
      </div>
      <div class="backup-live__body">
        <div class="backup-live__header">
          <strong>{liveOperationLabel}</strong>
          <span>Depuis {liveOperationElapsed}</span>
        </div>
        <p>{liveOperationText}</p>
        <div
          class="backup-live__track"
          role="progressbar"
          aria-valuemin="0"
          aria-valuemax="100"
          aria-valuenow={liveOperationPercent}
        >
          <span style={`width: ${liveOperationPercent}%`}></span>
        </div>
        <div class="backup-live__stats">
          <span>Source : {liveOperationSource ? formatBytes(liveOperationSource) : 'calcul en cours'}</span>
          <span>Archive : {liveOperationCompressed ? formatBytes(liveOperationCompressed) : 'préparation'}</span>
          <span>{liveOperationCount} opération en cours</span>
        </div>
      </div>
      </section>
    {/if}

    {#if isLoading}
      <div class="backup-empty">Chargement des sauvegardes...</div>
    {:else if backups.length === 0}
      <div class="backup-empty">
        <wa-icon name="box-archive"></wa-icon>
        <span>Aucune sauvegarde pour cette instance.</span>
      </div>
    {:else}
      <div class="backup-list">
        {#each backups as backup (backup.id)}
          <article
            class="backup-row"
            class:backup-row--running={backup.status === 'running'}
            class:backup-row--restore={shouldShowRestoreProgress(backup)}
            class:backup-row--failed={backup.status === 'failed'}
          >
          <div class="backup-main">
            <div class="backup-title-row">
              <span class="backup-title">{backup.filename || backup.id}</span>
              <span class="backup-status backup-status--{backup.status}">{statusLabel(backup.status)}</span>
              {#if restoreTagLabel(backup)}
                <span class="backup-restore-tag" title={restoreTagTitle(backup)}>
                  <wa-icon name="rotate-left"></wa-icon>
                  {restoreTagLabel(backup)}
                </span>
              {/if}
            </div>
            <div class="backup-meta">
              <span>{formatDate(backup.created)}</span>
              <span>{kindLabel(backup.kind)}</span>
              <span>{formatBytes(backup.compressedBytes)} compressés</span>
              <span>{formatBytes(backup.sizeBytes)} source</span>
            </div>
            {#if backup.status === 'running' || shouldShowRestoreProgress(backup)}
              {@const operation = visibleOperationForBackup(backup)}
              <div class="backup-row-progress" aria-live="polite">
                <div class="backup-row-progress__meta">
                  <strong>{operation.label}</strong>
                  <span>{formatDuration(Math.max(0, now - operation.startedAt))}</span>
                </div>
                <div
                  class="backup-row-progress__track"
                  role="progressbar"
                  aria-valuemin="0"
                  aria-valuemax="100"
                  aria-valuenow={operation.percent}
                >
                  <span style={`width: ${operation.percent}%`}></span>
                </div>
              </div>
            {/if}
            {#if backup.checksum}
              <code class="backup-checksum">sha256:{backup.checksum.slice(0, 16)}...</code>
            {/if}
            {#if backup.remoteKey}
              <p class="backup-remote">Copie R2/S3 : {backup.remoteKey}</p>
            {:else if backup.remoteError}
              <p class="backup-warning">Copie R2/S3 non faite : {backup.remoteError}</p>
            {/if}
            {#if backup.error}
              <p class="backup-error">{backup.error}</p>
            {/if}
          </div>

          <div class="backup-actions">
            <button
              type="button"
              class="backup-action"
              disabled={isBusy || backup.status !== 'ready'}
              onclick={() => downloadBackup(backup)}
              title="Télécharger"
              aria-label="Télécharger la sauvegarde"
            >
              <wa-icon name={action === `download:${backup.id}` ? 'rotate' : 'download'}></wa-icon>
              <span>Télécharger</span>
            </button>
            <button
              type="button"
              class="backup-action backup-action--restore"
              disabled={isBusy || backup.status !== 'ready'}
              onclick={() => restoreBackup(backup)}
              title="Restaurer"
              aria-label="Restaurer cette sauvegarde"
            >
              <wa-icon name={action === `restore:${backup.id}` ? 'rotate' : 'rotate-left'}></wa-icon>
              <span>Restaurer</span>
            </button>
            <button
              type="button"
              class="backup-action backup-action--restore-new"
              disabled={isBusy || backup.status !== 'ready'}
              onclick={() => restoreBackupToNewInstance(backup)}
              title="Restaurer dans une nouvelle instance"
              aria-label="Restaurer cette sauvegarde dans une nouvelle instance"
            >
              <wa-icon name={action === `restore-new:${backup.id}` ? 'rotate' : 'copy'}></wa-icon>
              <span>Nouvelle instance</span>
            </button>
            <button
              type="button"
              class="backup-action backup-action--danger"
              disabled={isBusy}
              onclick={() => deleteBackup(backup)}
              title="Supprimer"
              aria-label="Supprimer cette sauvegarde"
            >
              <wa-icon name={action === `delete:${backup.id}` ? 'rotate' : 'trash'}></wa-icon>
              <span>Supprimer</span>
            </button>
          </div>
          </article>
        {/each}
      </div>
    {/if}
  {/if}
</FeatureTab>

<style>
  .backup-cta {
    display: flex;
    flex-wrap: wrap;
    gap: 0.6rem;
    justify-content: flex-end;
  }

  .backup-import-zip-cta {
    display: inline-flex;
    min-height: 2.5rem;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    border: 1px solid rgb(37 99 235 / 0.5);
    border-radius: 0.5rem;
    background: rgb(37 99 235 / 0.13);
    padding: 0 1rem;
    color: #2563eb;
    font-size: 0.9rem;
    font-weight: 900;
    line-height: 1;
    cursor: pointer;
    transition:
      background-color 120ms ease,
      border-color 120ms ease,
      box-shadow 120ms ease;
  }

  .backup-import-zip-cta:hover:not(:disabled) {
    border-color: rgb(37 99 235 / 0.7);
    background: rgb(37 99 235 / 0.19);
    box-shadow: 0 10px 22px rgb(37 99 235 / 0.14);
  }

  .backup-import-zip-cta:disabled {
    opacity: 0.6;
    cursor: wait;
  }

  .backup-import {
    display: grid;
    gap: 0.75rem;
    margin-bottom: 1rem;
    border: 1px solid rgb(14 165 233 / 0.22);
    border-radius: 0.65rem;
    background: linear-gradient(135deg, rgb(14 165 233 / 0.1), transparent 55%), var(--app-surface);
    padding: 1rem;
    box-shadow: var(--app-shadow-sm);
  }

  .backup-import-copy {
    display: grid;
    gap: 0.25rem;
  }

  .backup-import-copy strong {
    color: var(--app-text-strong);
    font-size: 0.95rem;
    font-weight: 900;
  }

  .backup-import-copy span {
    color: var(--app-text-muted);
    font-size: 0.82rem;
    line-height: 1.45;
  }

  .backup-import-grid {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 0.65rem;
  }

  .backup-file {
    display: flex;
    min-height: 2.5rem;
    align-items: center;
    border: 1px dashed rgb(14 165 233 / 0.35);
    border-radius: 0.5rem;
    background: rgb(14 165 233 / 0.06);
    padding: 0 0.75rem;
    color: var(--app-text);
    font-size: 0.86rem;
    font-weight: 700;
    cursor: pointer;
  }

  .backup-file input {
    display: none;
  }

  .backup-server-path {
    min-height: 2.5rem;
    min-width: 0;
    border: 1px solid var(--app-border);
    border-radius: 0.5rem;
    background: var(--app-surface);
    padding: 0 0.75rem;
    color: var(--app-text);
    font: inherit;
    font-size: 0.86rem;
  }

  .backup-server-path:focus-visible {
    outline: none;
    border-color: rgb(14 165 233 / 0.55);
    box-shadow: 0 0 0 3px rgb(14 165 233 / 0.16);
  }

  .backup-import-btn {
    display: inline-flex;
    min-height: 2.5rem;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    border: 1px solid rgb(14 165 233 / 0.45);
    border-radius: 0.5rem;
    background: rgb(14 165 233 / 0.12);
    padding: 0 0.85rem;
    color: #0284c7;
    font-size: 0.84rem;
    font-weight: 900;
    cursor: pointer;
    transition:
      background-color 120ms ease,
      border-color 120ms ease;
  }

  .backup-import-btn:hover:not(:disabled) {
    border-color: rgb(14 165 233 / 0.68);
    background: rgb(14 165 233 / 0.18);
  }

  .backup-import-btn--server {
    border-color: rgb(99 102 241 / 0.38);
    background: rgb(99 102 241 / 0.1);
    color: #4f46e5;
  }

  .backup-import-btn:disabled,
  .backup-file:has(input:disabled),
  .backup-server-path:disabled {
    opacity: 0.58;
    cursor: not-allowed;
  }

  .backup-upload-progress {
    display: grid;
    gap: 0.45rem;
    border: 1px solid rgb(14 165 233 / 0.24);
    border-radius: 0.55rem;
    background: rgb(14 165 233 / 0.07);
    padding: 0.75rem;
  }

  .backup-upload-progress__row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 0.45rem;
    color: var(--app-text);
    font-size: 0.8rem;
    font-weight: 750;
  }

  .backup-upload-progress__row strong {
    color: var(--app-text-strong);
    font-weight: 900;
  }

  .backup-upload-progress__row span {
    color: var(--app-text-muted);
  }

  .backup-upload-progress__track {
    position: relative;
    height: 0.65rem;
    overflow: hidden;
    border-radius: 999px;
    background: rgb(14 165 233 / 0.13);
  }

  .backup-upload-progress__track span {
    display: block;
    height: 100%;
    border-radius: inherit;
    background: linear-gradient(90deg, #0ea5e9, #22c55e);
    box-shadow: 0 0 18px rgb(14 165 233 / 0.35);
    transition: width 160ms ease;
  }

  .backup-page-tabs {
    display: grid;
    gap: 0.75rem;
    margin-bottom: 1rem;
  }

  .backup-page-tabs__nav {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 0.5rem;
    border: 1px solid var(--app-border);
    border-radius: 0.65rem;
    background: var(--app-surface);
    padding: 0.45rem;
    box-shadow: var(--app-shadow-sm);
  }

  .backup-page-tabs__nav button {
    display: grid;
    min-width: 0;
    min-height: 3.35rem;
    grid-template-columns: auto minmax(0, 1fr);
    grid-template-areas:
      'icon label'
      'icon meta';
    gap: 0.12rem 0.65rem;
    align-items: center;
    border: 1px solid transparent;
    border-radius: 0.5rem;
    background: transparent;
    padding: 0.65rem 0.75rem;
    color: var(--app-text-muted);
    text-align: left;
    cursor: pointer;
  }

  .backup-page-tabs__nav button:hover {
    border-color: rgb(30 184 84 / 0.28);
    background: rgb(30 184 84 / 0.07);
    color: var(--app-text);
  }

  .backup-page-tabs__nav button.active {
    border-color: rgb(30 184 84 / 0.38);
    background: linear-gradient(135deg, rgb(30 184 84 / 0.14), rgb(59 130 246 / 0.07));
    color: var(--app-text-strong);
  }

  .backup-page-tabs__nav wa-icon {
    grid-area: icon;
    color: #1eb854;
    font-size: 1.1rem;
  }

  .backup-page-tabs__nav span {
    grid-area: label;
    overflow: hidden;
    color: currentColor;
    font-size: 0.85rem;
    font-weight: 950;
    line-height: 1.2;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .backup-page-tabs__nav small {
    grid-area: meta;
    overflow: hidden;
    color: var(--app-text-muted);
    font-size: 0.72rem;
    font-weight: 780;
    line-height: 1.25;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .backup-tab-panel {
    display: grid;
    gap: 1rem;
  }

  .backup-page-tabs .backup-policy {
    margin-bottom: 0;
  }

  .backup-policy {
    display: grid;
    gap: 0.95rem;
    margin-bottom: 1rem;
    border: 1px solid rgb(30 184 84 / 0.24);
    border-radius: 0.65rem;
    background: linear-gradient(135deg, rgb(30 184 84 / 0.09), transparent 54%), var(--app-surface);
    padding: 1rem;
    box-shadow: var(--app-shadow-sm);
  }

  .backup-policy--litestream {
    border-color: rgb(37 99 235 / 0.24);
    background: linear-gradient(135deg, rgb(37 99 235 / 0.08), transparent 54%), var(--app-surface);
  }

  .backup-policy__header,
  .backup-policy-footer {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-start;
    justify-content: space-between;
    gap: 0.75rem;
  }

  .backup-policy__header > div {
    display: grid;
    gap: 0.25rem;
  }

  .backup-policy__header strong {
    color: var(--app-text-strong);
    font-size: 0.95rem;
    font-weight: 950;
  }

  .backup-policy__header span,
  .backup-policy-help,
  .backup-policy-loading,
  .backup-policy-last {
    color: var(--app-text-muted);
    font-size: 0.78rem;
    line-height: 1.45;
  }

  .backup-policy-status {
    display: inline-flex;
    align-items: center;
    border: 1px solid var(--app-border);
    border-radius: 999px;
    padding: 0.22rem 0.65rem;
    font-size: 0.72rem;
    font-weight: 900;
    line-height: 1.2;
  }

  .backup-policy-status--ready {
    border-color: rgb(30 184 84 / 0.36);
    background: rgb(30 184 84 / 0.1);
    color: #16a34a;
  }

  .backup-policy-status--running {
    border-color: rgb(245 158 11 / 0.38);
    background: rgb(245 158 11 / 0.12);
    color: #d97706;
  }

  .backup-policy-status--failed {
    border-color: rgb(239 68 68 / 0.36);
    background: rgb(239 68 68 / 0.1);
    color: #ef4444;
  }

  .backup-policy-status--skipped,
  .backup-policy-status--never {
    border-color: rgb(148 163 184 / 0.36);
    background: rgb(148 163 184 / 0.1);
    color: var(--app-text-muted);
  }

  .backup-policy-error {
    margin: 0;
    border: 1px solid rgb(239 68 68 / 0.28);
    border-radius: 0.5rem;
    background: rgb(239 68 68 / 0.08);
    padding: 0.65rem 0.75rem;
    color: #ef4444;
    font-size: 0.82rem;
    font-weight: 800;
  }

  .backup-policy-warning,
  .backup-policy-note {
    margin: 0;
    border-radius: 0.5rem;
    padding: 0.65rem 0.75rem;
    font-size: 0.8rem;
    font-weight: 780;
    line-height: 1.45;
  }

  .backup-policy-warning {
    border: 1px solid rgb(245 158 11 / 0.3);
    background: rgb(245 158 11 / 0.09);
    color: #d97706;
  }

  .backup-policy-note {
    border: 1px solid rgb(37 99 235 / 0.2);
    background: rgb(37 99 235 / 0.07);
    color: var(--app-text-muted);
  }

  .backup-policy-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0.75rem;
  }

  .backup-policy-toggle,
  .backup-policy-field {
    display: grid;
    gap: 0.45rem;
    min-width: 0;
    border: 1px solid var(--app-border);
    border-radius: 0.55rem;
    background: var(--app-surface-soft);
    padding: 0.8rem;
  }

  .backup-policy-toggle {
    grid-template-columns: auto minmax(0, 1fr);
    align-items: center;
  }

  .backup-policy-toggle input,
  .backup-policy-check input {
    width: 1rem;
    height: 1rem;
    accent-color: #1eb854;
  }

  .backup-policy-toggle span {
    display: grid;
    gap: 0.15rem;
  }

  .backup-policy-toggle strong,
  .backup-policy-field > label,
  .backup-policy-field-label,
  .backup-policy-check {
    color: var(--app-text-strong);
    font-size: 0.8rem;
    font-weight: 900;
  }

  .backup-policy-toggle small {
    color: var(--app-text-muted);
    font-size: 0.74rem;
    font-weight: 700;
  }

  .backup-policy-field--wide {
    grid-column: span 2;
  }

  .backup-policy-section-title {
    display: grid;
    gap: 0.2rem;
    border-top: 1px solid var(--app-border);
    padding-top: 0.25rem;
  }

  .backup-policy-section-title strong {
    color: var(--app-text-strong);
    font-size: 0.82rem;
    font-weight: 950;
  }

  .backup-policy-section-title span {
    color: var(--app-text-muted);
    font-size: 0.76rem;
    font-weight: 720;
    line-height: 1.4;
  }

  .backup-policy-check {
    display: flex;
    align-items: center;
    gap: 0.45rem;
  }

  .backup-policy-input {
    min-height: 2.35rem;
    min-width: 0;
    border: 1px solid var(--app-border);
    border-radius: 0.48rem;
    background: var(--app-surface);
    padding: 0 0.65rem;
    color: var(--app-text);
    font: inherit;
    font-size: 0.82rem;
    font-weight: 750;
  }

  .backup-policy-input:focus-visible {
    outline: none;
    border-color: rgb(30 184 84 / 0.55);
    box-shadow: 0 0 0 3px rgb(30 184 84 / 0.14);
  }

  .backup-policy-code {
    display: block;
    overflow-wrap: anywhere;
    border: 1px solid var(--app-border);
    border-radius: 0.48rem;
    background: color-mix(in srgb, var(--app-surface) 82%, transparent);
    padding: 0.65rem;
    color: var(--app-text-strong);
    font-size: 0.78rem;
    line-height: 1.45;
  }

  .backup-cron-wizard {
    display: grid;
    gap: 0.6rem;
    border: 1px solid rgb(59 130 246 / 0.22);
    border-radius: 0.55rem;
    background: linear-gradient(135deg, rgb(59 130 246 / 0.08), transparent 64%), var(--app-surface);
    padding: 0.75rem;
  }

  .backup-cron-wizard__main {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 0.5rem;
    align-items: center;
  }

  .backup-cron-wizard__examples {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
  }

  .backup-cron-chip {
    border: 1px solid var(--app-border);
    border-radius: 999px;
    background: var(--app-surface-soft);
    padding: 0.34rem 0.6rem;
    color: var(--app-text);
    font-size: 0.72rem;
    font-weight: 820;
    cursor: pointer;
  }

  .backup-cron-chip:hover:not(:disabled) {
    border-color: rgb(30 184 84 / 0.45);
    color: #16a34a;
  }

  .backup-cron-chip:disabled {
    opacity: 0.55;
    cursor: not-allowed;
  }

  .backup-cron-timezone {
    color: var(--app-text-muted);
    font-size: 0.72rem;
    font-weight: 760;
  }

  .backup-cron-timezone strong {
    color: var(--app-text-strong);
  }

  .backup-cron-result {
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem 0.7rem;
    align-items: center;
    border-radius: 0.45rem;
    padding: 0.55rem 0.65rem;
    font-size: 0.76rem;
    font-weight: 760;
    line-height: 1.45;
  }

  .backup-cron-result strong {
    border-radius: 0.34rem;
    background: rgb(15 23 42 / 0.1);
    padding: 0.16rem 0.38rem;
    color: var(--app-text-strong);
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    font-weight: 900;
  }

  .backup-cron-result--ok {
    border: 1px solid rgb(30 184 84 / 0.26);
    background: rgb(30 184 84 / 0.08);
    color: var(--app-text-muted);
  }

  .backup-cron-result--error {
    border: 1px solid rgb(239 68 68 / 0.26);
    background: rgb(239 68 68 / 0.08);
    color: #ef4444;
  }

  .backup-policy-input:disabled,
  .backup-policy-check input:disabled,
  .backup-policy-toggle input:disabled {
    opacity: 0.55;
    cursor: not-allowed;
  }

  .backup-policy-help--error,
  .backup-policy-last-error {
    color: #ef4444;
  }

  .backup-policy-retention {
    display: grid;
    grid-template-columns: minmax(4.6rem, 6rem) auto minmax(4.6rem, 6rem) auto;
    gap: 0.45rem;
    align-items: center;
    color: var(--app-text-muted);
    font-size: 0.78rem;
    font-weight: 750;
  }

  .backup-policy-last {
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem 0.85rem;
    max-width: 46rem;
  }

  .backup-policy-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    justify-content: flex-end;
  }

  .backup-policy-primary,
  .backup-policy-secondary {
    display: inline-flex;
    min-height: 2.35rem;
    align-items: center;
    justify-content: center;
    gap: 0.45rem;
    border-radius: 0.5rem;
    padding: 0 0.9rem;
    font-size: 0.82rem;
    font-weight: 900;
    cursor: pointer;
  }

  .backup-policy-primary {
    border: 1px solid #1eb854;
    background: #1eb854;
    color: white;
  }

  .backup-policy-secondary {
    border: 1px solid rgb(59 130 246 / 0.42);
    background: rgb(59 130 246 / 0.1);
    color: #2563eb;
  }

  .backup-policy-primary:disabled,
  .backup-policy-secondary:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .backup-live {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    gap: 0.85rem;
    align-items: start;
    margin-bottom: 1rem;
    border: 1px solid rgb(245 158 11 / 0.34);
    border-radius: 0.65rem;
    background: linear-gradient(135deg, rgb(245 158 11 / 0.12), transparent 58%), var(--app-surface);
    padding: 0.95rem;
    box-shadow: var(--app-shadow-sm);
  }

  .backup-live__icon {
    display: grid;
    width: 2.35rem;
    height: 2.35rem;
    place-items: center;
    border: 1px solid rgb(245 158 11 / 0.36);
    border-radius: 0.5rem;
    background: rgb(245 158 11 / 0.13);
    color: #d97706;
  }

  .backup-live__icon wa-icon {
    animation: backup-spin 1.1s linear infinite;
  }

  .backup-live__body {
    display: grid;
    gap: 0.5rem;
    min-width: 0;
  }

  .backup-live__header {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    justify-content: space-between;
    gap: 0.4rem 0.75rem;
  }

  .backup-live__header strong {
    color: var(--app-text-strong);
    font-size: 0.95rem;
    font-weight: 950;
  }

  .backup-live__header span,
  .backup-live__stats,
  .backup-live p {
    color: var(--app-text-muted);
    font-size: 0.8rem;
    font-weight: 700;
  }

  .backup-live p {
    margin: 0;
    line-height: 1.45;
  }

  .backup-live__track,
  .backup-row-progress__track {
    position: relative;
    height: 0.65rem;
    overflow: hidden;
    border-radius: 999px;
    background: rgb(245 158 11 / 0.14);
  }

  .backup-live__track::before,
  .backup-row-progress__track::before {
    position: absolute;
    inset: 0;
    content: '';
    background: linear-gradient(90deg, transparent, rgb(255 255 255 / 0.16), transparent);
    animation: backup-sweep 1.8s ease-in-out infinite;
  }

  .backup-live__track span,
  .backup-row-progress__track span {
    position: relative;
    display: block;
    height: 100%;
    min-width: 0.45rem;
    border-radius: inherit;
    background: linear-gradient(90deg, #f59e0b, #22c55e);
    box-shadow: 0 0 18px rgb(245 158 11 / 0.28);
    transition: width 240ms ease;
  }

  .backup-live__stats {
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem 0.85rem;
  }

  .backup-list {
    display: grid;
    gap: 0.75rem;
  }

  .backup-create-btn {
    display: inline-flex;
    min-height: 2.5rem;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    border: 1px solid #1eb854;
    border-radius: 0.5rem;
    background: #1eb854;
    padding: 0 1rem;
    color: white;
    font-size: 0.9rem;
    font-weight: 800;
    line-height: 1;
    box-shadow: 0 10px 22px rgb(30 184 84 / 0.18);
    cursor: pointer;
    transition:
      background-color 120ms ease,
      border-color 120ms ease,
      box-shadow 120ms ease;
  }

  .backup-create-btn:hover:not(:disabled) {
    border-color: #16a34a;
    background: #16a34a;
    box-shadow: 0 12px 26px rgb(30 184 84 / 0.24);
  }

  .backup-create-btn:focus-visible {
    outline: none;
    box-shadow: 0 0 0 3px rgb(30 184 84 / 0.24);
  }

  .backup-create-btn:disabled {
    opacity: 0.6;
    cursor: wait;
  }

  .backup-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 1rem;
    align-items: center;
    border: 1px solid var(--app-border);
    border-radius: 0.5rem;
    background: var(--app-surface);
    padding: 1rem;
    box-shadow: var(--app-shadow-sm);
  }

  .backup-row--failed {
    border-color: rgb(239 68 68 / 0.35);
  }

  .backup-row--running {
    border-color: rgb(245 158 11 / 0.38);
    background: linear-gradient(135deg, rgb(245 158 11 / 0.08), transparent 45%), var(--app-surface);
  }

  .backup-row--restore {
    border-color: rgb(59 130 246 / 0.4);
    background: linear-gradient(135deg, rgb(59 130 246 / 0.08), transparent 45%), var(--app-surface);
  }

  .backup-main {
    min-width: 0;
  }

  .backup-title-row {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    align-items: center;
    min-width: 0;
  }

  .backup-title {
    min-width: 0;
    overflow-wrap: anywhere;
    color: var(--app-text-strong);
    font-size: 0.94rem;
    font-weight: 800;
  }

  .backup-status {
    flex-shrink: 0;
    border: 1px solid var(--app-border);
    border-radius: 999px;
    padding: 0.15rem 0.55rem;
    font-size: 0.7rem;
    font-weight: 800;
    line-height: 1.2;
  }

  .backup-status--ready {
    border-color: rgb(30 184 84 / 0.35);
    color: #1eb854;
    background: rgb(30 184 84 / 0.1);
  }

  .backup-status--running {
    border-color: rgb(245 158 11 / 0.38);
    color: #d97706;
    background: rgb(245 158 11 / 0.12);
  }

  .backup-status--failed {
    border-color: rgb(239 68 68 / 0.35);
    color: #ef4444;
    background: rgb(239 68 68 / 0.1);
  }

  .backup-restore-tag {
    display: inline-flex;
    flex-shrink: 0;
    align-items: center;
    gap: 0.32rem;
    border: 1px solid rgb(59 130 246 / 0.35);
    border-radius: 999px;
    background: rgb(59 130 246 / 0.1);
    padding: 0.15rem 0.55rem;
    color: #2563eb;
    font-size: 0.7rem;
    font-weight: 850;
    line-height: 1.2;
  }

  .backup-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem 0.75rem;
    margin-top: 0.35rem;
    color: var(--app-text-muted);
    font-size: 0.78rem;
    font-weight: 600;
  }

  .backup-row-progress {
    display: grid;
    gap: 0.4rem;
    margin-top: 0.7rem;
    max-width: 34rem;
  }

  .backup-row-progress__meta {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 0.35rem 0.75rem;
    color: var(--app-text-muted);
    font-size: 0.75rem;
    font-weight: 750;
  }

  .backup-row-progress__meta strong {
    color: var(--app-text-strong);
    font-weight: 900;
  }

  .backup-checksum {
    display: inline-block;
    margin-top: 0.55rem;
    color: var(--app-text-faint);
    font-size: 0.75rem;
  }

  .backup-remote,
  .backup-warning,
  .backup-error {
    margin-top: 0.55rem;
    overflow-wrap: anywhere;
    font-size: 0.78rem;
    line-height: 1.4;
  }

  .backup-remote {
    color: var(--app-text-muted);
  }

  .backup-warning {
    color: #d97706;
  }

  .backup-error {
    color: #ef4444;
  }

  .backup-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.4rem;
    justify-content: flex-end;
  }

  .backup-action {
    display: inline-flex;
    min-width: 7.5rem;
    height: 2.25rem;
    align-items: center;
    justify-content: center;
    gap: 0.45rem;
    border: 1px solid var(--app-border);
    border-radius: 0.5rem;
    background: var(--app-surface-soft);
    padding: 0 0.75rem;
    color: var(--app-text-strong);
    font-size: 0.78rem;
    font-weight: 850;
    cursor: pointer;
    transition:
      border-color 120ms ease,
      background-color 120ms ease,
      color 120ms ease;
  }

  .backup-action:hover:not(:disabled) {
    border-color: #1eb854;
    background: rgb(30 184 84 / 0.1);
    color: #15803d;
  }

  .backup-action--restore:hover:not(:disabled) {
    border-color: rgb(59 130 246 / 0.5);
    background: rgb(59 130 246 / 0.1);
    color: #2563eb;
  }

  .backup-action--restore {
    border-color: rgb(59 130 246 / 0.42);
    background: rgb(59 130 246 / 0.1);
    color: #2563eb;
  }

  .backup-action--restore-new {
    border-color: rgb(14 165 233 / 0.42);
    background: rgb(14 165 233 / 0.1);
    color: #0284c7;
  }

  .backup-action--restore-new:hover:not(:disabled) {
    border-color: rgb(14 165 233 / 0.58);
    background: rgb(14 165 233 / 0.16);
    color: #0369a1;
  }

  .backup-action--danger:hover:not(:disabled) {
    border-color: rgb(239 68 68 / 0.45);
    background: rgb(239 68 68 / 0.1);
    color: #ef4444;
  }

  .backup-action--danger {
    min-width: 6.75rem;
  }

  .backup-action:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }

  .backup-empty {
    display: flex;
    min-height: 8rem;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    border: 1px dashed var(--app-border);
    border-radius: 0.5rem;
    color: var(--app-text-muted);
    font-size: 0.9rem;
    font-weight: 700;
  }

  @keyframes backup-spin {
    to {
      transform: rotate(360deg);
    }
  }

  @keyframes backup-sweep {
    from {
      transform: translateX(-100%);
    }

    to {
      transform: translateX(100%);
    }
  }

  @media (max-width: 720px) {
    .backup-page-tabs__nav {
      grid-template-columns: 1fr;
    }

    .backup-import-grid {
      grid-template-columns: 1fr;
    }

    .backup-policy-grid,
    .backup-policy-field--wide {
      grid-template-columns: 1fr;
      grid-column: auto;
    }

    .backup-cron-wizard__main {
      grid-template-columns: 1fr;
    }

    .backup-policy-retention {
      grid-template-columns: minmax(0, 1fr) auto;
    }

    .backup-policy-actions {
      width: 100%;
      justify-content: stretch;
    }

    .backup-policy-primary,
    .backup-policy-secondary {
      flex: 1 1 10rem;
    }

    .backup-live {
      grid-template-columns: 1fr;
    }

    .backup-row {
      grid-template-columns: 1fr;
    }

    .backup-actions {
      justify-content: flex-start;
    }

    .backup-action {
      min-width: 0;
      flex: 1 1 9rem;
    }
  }
</style>
