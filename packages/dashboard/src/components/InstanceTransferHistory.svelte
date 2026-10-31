<script lang="ts">
  import { client } from '$src/pocketbase-client'
  import type { InstanceTransfer, InstanceTransferStatus } from 'pockethost/common'

  export let transfers: InstanceTransfer[] = []
  export let onrefresh: () => Promise<void>
  export let onlink: ((link: string, expiresAt: string) => void) | undefined = undefined
  let action = ''
  let error = ''
  const labels: Record<InstanceTransferStatus, string> = {
    queued: 'En attente',
    preparing: 'Préparation de l’archive',
    ready: 'Prêt à importer',
    downloading: 'Copie entre serveurs',
    restoring: 'Restauration',
    complete: 'Terminé',
    failed: 'Échec',
    revoked: 'Annulé',
  }
  const cancel = async (transfer: InstanceTransfer) => {
    action = transfer.id
    error = ''
    try {
      await client().cancelInstanceTransfer(transfer.id)
      await onrefresh()
    } catch (e) {
      error = e instanceof Error ? client().parseError(e)[0] || e.message : `${e}`
    } finally {
      action = ''
    }
  }
  const retry = async (transfer: InstanceTransfer) => {
    action = transfer.id
    error = ''
    try {
      await client().retryInstanceTransfer(transfer.id)
      await onrefresh()
    } catch (e) {
      error = e instanceof Error ? client().parseError(e)[0] || e.message : `${e}`
    } finally {
      action = ''
    }
  }
  const renew = async (transfer: InstanceTransfer) => {
    action = transfer.id
    error = ''
    try {
      const result = await client().renewInstanceTransferLink(transfer.id)
      onlink?.(result.link, result.transfer.expiresAt)
      await onrefresh()
    } catch (e) {
      error = e instanceof Error ? client().parseError(e)[0] || e.message : `${e}`
    } finally {
      action = ''
    }
  }
  const progress = (transfer: InstanceTransfer) =>
    transfer.totalBytes > 0 ? Math.min(100, Math.floor((100 * transfer.downloadedBytes) / transfer.totalBytes)) : 0
  const phase = (transfer: InstanceTransfer) => transfer.phase || transfer.status
  const phaseLabel = (transfer: InstanceTransfer) =>
    phase(transfer) === 'verifying' ? 'Vérification de l’archive' : labels[phase(transfer) as InstanceTransferStatus]
  const bytes = (value: number) => {
    const units = ['o', 'Kio', 'Mio', 'Gio', 'Tio']
    const unit = Math.min(4, Math.floor(Math.log2(Math.max(1, value)) / 10))
    return `${(value / 1024 ** unit).toLocaleString('fr-FR', { maximumFractionDigits: unit ? 1 : 0 })} ${units[unit]}`
  }
  const step = (transfer: InstanceTransfer) =>
    ['queued', 'preparing', 'ready'].includes(phase(transfer)) ? 1 : phase(transfer) === 'restoring' ? 3 : 2
  const moving = (transfer: InstanceTransfer) =>
    ['queued', 'preparing', 'downloading', 'verifying', 'restoring'].includes(phase(transfer))
</script>

{#if error}<wa-callout variant="danger" role="alert">{error}</wa-callout>{/if}
<div class="grid gap-4" aria-live="polite">
  {#each transfers as transfer (transfer.id)}
    <wa-card>
      <div class="wa-card-body grid gap-3">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div>
            <strong>{transfer.subdomain}</strong>
            <p class="text-sm text-muted">
              {transfer.direction === 'export' ? 'Vers un autre serveur' : 'Depuis un autre serveur'}
            </p>
          </div>
          <wa-badge
            variant={phase(transfer) === 'failed' ? 'danger' : transfer.status === 'complete' ? 'success' : 'neutral'}
          >
            {phaseLabel(transfer)}
          </wa-badge>
        </div>
        {#if moving(transfer) || phase(transfer) === 'ready' || phase(transfer) === 'complete'}
          <div class="grid gap-2">
            <p class="text-sm font-medium">
              {#if phase(transfer) === 'complete'}Transfert terminé
              {:else}Étape {step(transfer)} sur 3 · {phaseLabel(transfer)}{/if}
            </p>
            <wa-progress-bar
              value={['ready', 'complete'].includes(phase(transfer)) ? 100 : progress(transfer)}
              indeterminate={moving(transfer) && (phase(transfer) !== 'downloading' || transfer.totalBytes <= 0)}
              label={phaseLabel(transfer)}
            ></wa-progress-bar>
            <p class="text-sm text-muted">
              {#if phase(transfer) === 'downloading'}
                {progress(transfer)} % · {bytes(transfer.downloadedBytes)} sur {bytes(transfer.totalBytes)} copiés
              {:else if phase(transfer) === 'preparing'}
                {['checksum', 'finalizing'].includes(transfer.preparationPhase || '')
                  ? 'Calcul de l’empreinte et finalisation'
                  : 'Compression en cours'}{transfer.sourceBytes ? ` · ${bytes(transfer.sourceBytes)} à préparer` : ''}.
                {#if transfer.preparedBytes}{bytes(transfer.preparedBytes)} écrits dans l’archive.{/if}
              {:else if phase(transfer) === 'verifying'}
                {bytes(transfer.downloadedBytes)} reçus. Vérification de l’intégrité avant restauration.
              {:else if phase(transfer) === 'restoring'}
                Copie reçue : {bytes(transfer.downloadedBytes)}. Extraction et installation sur le serveur cible.
              {:else if phase(transfer) === 'ready'}
                Archive prête : {bytes(transfer.totalBytes)}. En attente du serveur cible.
              {:else if phase(transfer) === 'queued'}
                Démarrage automatique dans moins d’une minute.
              {:else if phase(transfer) === 'complete'}
                {bytes(transfer.totalBytes)} transférés. La source est conservée.
              {/if}
            </p>
          </div>
        {/if}
        {#if phase(transfer) === 'failed' && transfer.direction === 'export' && transfer.status === 'ready'}
          <p class="text-sm text-muted">La cible a interrompu le transfert. Reprenez-le depuis le serveur cible.</p>
        {/if}
        {#if transfer.error}<p class="text-sm text-error" role="alert">{transfer.error}</p>{/if}
        <div class="flex flex-wrap gap-2">
          {#if onlink && transfer.direction === 'export' && !transfer.claimed && ['queued', 'preparing', 'ready'].includes(transfer.status)}
            <wa-button
              size="small"
              variant="neutral"
              disabled={!!action}
              loading={action === transfer.id}
              onclick={() => renew(transfer)}>Régénérer le lien</wa-button
            >
          {/if}
          {#if transfer.direction === 'export' && ['queued', 'ready', 'failed'].includes(transfer.status)}
            <wa-button
              size="small"
              variant="neutral"
              appearance="outlined"
              disabled={!!action}
              loading={action === transfer.id}
              onclick={() => cancel(transfer)}
            >
              Annuler et rétablir la source
            </wa-button>
          {/if}
          {#if transfer.direction === 'import' && transfer.status === 'failed' && Date.parse(transfer.expiresAt) > Date.now()}
            <wa-button
              size="small"
              variant="brand"
              disabled={!!action}
              loading={action === transfer.id}
              onclick={() => retry(transfer)}>Reprendre le transfert</wa-button
            >
          {/if}
          {#if transfer.direction === 'import' && transfer.status === 'complete'}
            <wa-button size="small" variant="brand" href={`/instances/${transfer.instance}`}
              >Ouvrir l’instance</wa-button
            >
            <span class="text-sm text-muted">Prête à démarrer sur ce serveur.</span>
          {/if}
        </div>
      </div>
    </wa-card>
  {/each}
</div>
