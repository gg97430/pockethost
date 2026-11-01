<script lang="ts">
  import { onDestroy, onMount } from 'svelte'
  import { client } from '$src/pocketbase-client'
  import type { InstanceTransfer } from 'pockethost/common'
  import InstanceTransferHistory from './InstanceTransferHistory.svelte'

  export let instanceId: string
  let transfers: InstanceTransfer[] = []
  let link = ''
  let expiresAt = ''
  let busy = false
  let confirming = false
  let error = ''
  let copied: '' | 'link' | 'code' = ''
  let timer: ReturnType<typeof setInterval> | undefined
  let refreshing = false
  $: active = transfers.some((transfer) => ['queued', 'preparing', 'ready'].includes(transfer.status))
  $: code = link.split('#')[1]?.replace(/^code=/, '') || ''
  const refresh = async () => {
    if (refreshing) return
    refreshing = true
    try {
      transfers = (await client().listInstanceTransfers(instanceId)).transfers
      if (
        !transfers.some(
          (transfer) => transfer.direction === 'export' && ['queued', 'preparing', 'ready'].includes(transfer.status)
        )
      ) {
        link = ''
        expiresAt = ''
      }
    } catch (e) {
      error = e instanceof Error ? client().parseError(e)[0] || e.message : `${e}`
    } finally {
      refreshing = false
    }
  }
  const prepare = async () => {
    busy = true
    confirming = false
    error = ''
    try {
      const result = await client().createInstanceTransfer(instanceId)
      link = result.link
      expiresAt = result.transfer.expiresAt
      copied = ''
      await refresh()
    } catch (e) {
      error = e instanceof Error ? client().parseError(e)[0] || e.message : `${e}`
    } finally {
      busy = false
    }
  }
  const copy = async (kind: 'link' | 'code') => {
    try {
      await navigator.clipboard.writeText(kind === 'link' ? link : code)
      copied = kind
    } catch {
      error = 'Copie impossible. Sélectionnez et copiez le lien ci-dessous.'
    }
  }
  onMount(() => {
    void refresh()
    timer = setInterval(() => void refresh(), 4000)
  })
  onDestroy(() => {
    if (timer) clearInterval(timer)
  })
</script>

<div class="grid gap-5">
  <div>
    <p class="mb-3 text-sm text-muted">
      Déplacez cette instance vers une autre installation PocketHost. La source est conservée et reste arrêtée pendant
      le transfert.
    </p>
    <wa-button variant="brand" disabled={busy || active} loading={busy} onclick={() => (confirming = true)}>
      <wa-icon slot="start" name="arrow-right-arrow-left"></wa-icon>Préparer un transfert
    </wa-button>
  </div>
  {#if error}<wa-callout variant="danger" role="alert">{error}</wa-callout>{/if}
  {#if link}
    <wa-card>
      <div class="wa-card-body grid gap-3">
        <div class="auth-field-group">
          <label class="auth-label" for="transfer-export-link">Lien à coller sur le serveur cible</label>
          <textarea id="transfer-export-link" class="auth-field" value={link} readonly rows="3"></textarea>
        </div>
        <div class="flex flex-wrap items-center gap-3">
          <wa-button variant="neutral" onclick={() => copy('link')}>
            <wa-icon slot="start" name={copied === 'link' ? 'check' : 'copy'}></wa-icon>{copied === 'link'
              ? 'Lien copié'
              : 'Copier le lien complet'}
          </wa-button>
          <span class="text-sm text-muted">Valable jusqu’au {new Date(expiresAt).toLocaleString('fr-FR')}.</span>
        </div>
        <div class="auth-field-group">
          <label class="auth-label" for="transfer-export-code">Code de transfert</label>
          <input id="transfer-export-code" class="auth-field font-mono" value={code} readonly spellcheck="false" />
        </div>
        <div>
          <wa-button variant="neutral" onclick={() => copy('code')}>
            <wa-icon slot="start" name={copied === 'code' ? 'check' : 'copy'}></wa-icon>{copied === 'code'
              ? 'Code copié'
              : 'Copier le code'}
          </wa-button>
        </div>
        <p class="text-sm text-muted">
          Sur le serveur cible : Dashboard → Importer un transfert. Le lien complet contient déjà ce code. Si vous
          utilisez un lien sans code, collez le code dans le champ séparé. Il autorise une seule destination.
        </p>
      </div>
    </wa-card>
  {/if}
  <InstanceTransferHistory
    {transfers}
    onrefresh={refresh}
    onlink={(value, expiry) => {
      link = value
      expiresAt = expiry
      copied = ''
    }}
  />
</div>

<wa-dialog label="Transférer cette instance" open={confirming} onwa-hide={() => (confirming = false)}>
  <p>
    L’instance sera arrêtée pour préparer une copie cohérente. Elle restera arrêtée jusqu’à la fin du transfert ou son
    annulation.
  </p>
  <p class="mt-3 text-sm text-muted">
    Sur le serveur cible, une nouvelle instance sera créée avec une nouvelle adresse.
  </p>
  <wa-button slot="footer" variant="neutral" onclick={() => (confirming = false)}>Annuler</wa-button>
  <wa-button slot="footer" variant="brand" onclick={prepare}>Arrêter et préparer le transfert</wa-button>
</wa-dialog>
