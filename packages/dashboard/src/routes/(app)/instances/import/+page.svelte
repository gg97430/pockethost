<script lang="ts">
  import { onDestroy, onMount } from 'svelte'
  import InstanceTransferHistory from '$components/InstanceTransferHistory.svelte'
  import { PUBLIC_APEX_DOMAIN, PUBLIC_HTTP_PROTOCOL } from '$lib/appEnv'
  import { client } from '$src/pocketbase-client'
  import { userStore } from '$util/stores'
  import type { InstanceTransfer, TransferMetadata } from 'pockethost/common'

  let link = ''
  let code = ''
  let subdomain = ''
  let metadata: TransferMetadata | null = null
  let transfers: InstanceTransfer[] = []
  let busy = false
  let error = ''
  let success = ''
  let timer: ReturnType<typeof setInterval> | undefined
  let refreshing = false
  const refresh = async () => {
    if (refreshing || !$userStore?.superAdmin) return
    refreshing = true
    try {
      transfers = (await client().listInstanceTransfers()).transfers.filter((item) => item.direction === 'import')
    } catch (e) {
      error = e instanceof Error ? client().parseError(e)[0] || e.message : `${e}`
    } finally {
      refreshing = false
    }
  }
  const inspect = async () => {
    busy = true
    error = ''
    success = ''
    metadata = null
    try {
      metadata = await client().inspectInstanceTransfer({ link: link.trim(), code: code.trim() })
      subdomain = metadata.instance.subdomain
    } catch (e) {
      error = e instanceof Error ? client().parseError(e)[0] || e.message : `${e}`
    } finally {
      busy = false
    }
  }
  const start = async () => {
    busy = true
    error = ''
    try {
      await client().importInstanceTransfer({ link: link.trim(), code: code.trim(), subdomain: subdomain.trim() })
      link = ''
      code = ''
      metadata = null
      success = 'Transfert lancé. Vous pouvez quitter cette page et revenir suivre son avancement.'
      await refresh()
    } catch (e) {
      error = e instanceof Error ? client().parseError(e)[0] || e.message : `${e}`
    } finally {
      busy = false
    }
  }
  const size = (bytes: number) => {
    const units = ['o', 'Kio', 'Mio', 'Gio', 'Tio']
    const unit = bytes > 0 ? Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024))) : 0
    return `${new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 1 }).format(bytes / 1024 ** unit)} ${units[unit]}`
  }
  onMount(() => {
    void refresh()
    timer = setInterval(() => void refresh(), 4000)
  })
  onDestroy(() => {
    if (timer) clearInterval(timer)
  })
</script>

<svelte:head><title>Importer un transfert - Kolab Hosting</title></svelte:head>

<div class="mx-auto grid max-w-3xl gap-6">
  <header>
    <wa-button href="/dashboard" variant="neutral" appearance="plain" size="small">
      <wa-icon slot="start" name="arrow-left"></wa-icon>Dashboard
    </wa-button>
    <h1 class="mt-3 text-2xl font-medium">Importer un transfert</h1>
    <p class="mt-2 text-sm text-muted">
      Récupérez une instance depuis un autre serveur PocketHost, avec une nouvelle adresse.
    </p>
  </header>
  {#if !$userStore?.superAdmin}
    <wa-callout variant="neutral">Cette action est réservée au superadmin.</wa-callout>
  {:else}
    {#if error}<wa-callout variant="danger" role="alert">{error}</wa-callout>{/if}
    {#if success}<wa-callout variant="success" role="status">{success}</wa-callout>{/if}
    <wa-card>
      <form
        class="wa-card-body grid gap-4"
        onsubmit={(event) => {
          event.preventDefault()
          void (metadata ? start() : inspect())
        }}
      >
        <div class="auth-field-group">
          <label class="auth-label" for="transfer-link">Lien du serveur source</label>
          <textarea
            id="transfer-link"
            class="auth-field"
            placeholder="Collez le lien généré dans Sauvegardes → Transfert"
            bind:value={link}
            required
            rows="3"
            disabled={busy || !!metadata}
          ></textarea>
        </div>
        {#if !metadata}
          <wa-details summary="Saisir le code séparément">
            <div class="auth-field-group">
              <label class="auth-label" for="transfer-code">Code de transfert</label>
              <input
                id="transfer-code"
                class="auth-field"
                type="password"
                bind:value={code}
                disabled={busy}
                autocomplete="off"
              />
            </div>
          </wa-details>
          <div>
            <wa-button type="submit" variant="brand" loading={busy} disabled={busy || !link.trim()}
              >Vérifier le transfert</wa-button
            >
          </div>
        {:else}
          <div class="flex flex-wrap items-center justify-between gap-3 text-sm">
            <span
              >{metadata.instance.subdomain} · PocketBase {metadata.instance.version} · {size(
                metadata.archive?.compressedBytes || 0
              )} à transférer</span
            >
            <wa-button
              size="small"
              variant="neutral"
              appearance="plain"
              disabled={busy}
              onclick={() => (metadata = null)}>Modifier le lien</wa-button
            >
          </div>
          <div class="auth-field-group">
            <label class="auth-label" for="transfer-subdomain">Nom de la nouvelle instance</label>
            <input
              id="transfer-subdomain"
              class="auth-field"
              bind:value={subdomain}
              required
              minlength="3"
              maxlength="40"
              pattern={'[a-z][a-z0-9-]{2,39}'}
              disabled={busy}
            />
          </div>
          <p class="text-sm text-muted">
            Nouvelle adresse : {PUBLIC_HTTP_PROTOCOL}//{subdomain || 'nom'}.{PUBLIC_APEX_DOMAIN}
          </p>
          <p class="text-sm text-muted">
            La base, les fichiers, les hooks et la configuration seront copiés directement entre serveurs. L’instance
            cible restera arrêtée jusqu’à votre vérification.
          </p>
          <div>
            <wa-button
              type="submit"
              variant="brand"
              loading={busy}
              disabled={busy || !/^[a-z][a-z0-9-]{2,39}$/.test(subdomain)}
            >
              <wa-icon slot="start" name="arrow-right-arrow-left"></wa-icon>Importer sur ce serveur
            </wa-button>
          </div>
        {/if}
      </form>
    </wa-card>
    <InstanceTransferHistory {transfers} onrefresh={refresh} />
  {/if}
</div>
