<script lang="ts">
  import { onMount } from 'svelte'
  import { page } from '$app/stores'
  import { client } from '$src/pocketbase-client'

  let error = ''
  let pending = true
  let nativeUrl = ''
  const open = async () => {
    pending = true
    error = ''
    const id = $page.params.instanceId
    const security = $page.url.searchParams.get('security') === '1'
    const fallback = (url: string) => `${url}?manual=1${security ? '&security=1' : ''}`
    try {
      const pb = client().client
      const info = await pb.send<{ url: string }>(`/api/instance/${id}/console`, { method: 'GET' })
      nativeUrl = fallback(info.url)
      if (!pb.authStore.isValid) {
        window.location.replace(nativeUrl)
        return
      }
      try {
        const result = await pb.send<{ url: string; ticket: string }>(`/api/instance/${id}/console`, { method: 'POST' })
        // The dashboard bearer token never leaves this origin. Only the one-use instance capability crosses it.
        window.location.replace(`${result.url}${security ? '?security=1' : ''}#ticket=${result.ticket}`)
      } catch (e) {
        if ([401, 403].includes(Number((e as { status?: number }).status))) {
          window.location.replace(nativeUrl)
          return
        }
        throw e
      }
    } catch (e) {
      error = e instanceof Error ? client().parseError(e)[0] || e.message : `${e}`
      pending = false
    }
  }
  onMount(() => {
    void open()
  })
</script>

<svelte:head>
  <title>Ouverture de la console - Kolab Hosting</title>
  <meta name="referrer" content="no-referrer" />
</svelte:head>

<div class="mx-auto grid max-w-form gap-5 px-4 py-16">
  <h1 class="text-2xl font-medium">Console PocketBase</h1>
  {#if pending}
    <p role="status">Ouverture de votre console…</p>
    <wa-progress-bar indeterminate label="Ouverture de la console"></wa-progress-bar>
  {:else}
    <wa-callout variant="danger" role="alert">{error}</wa-callout>
    <div class="flex flex-wrap gap-3">
      <wa-button variant="brand" onclick={open}>Réessayer</wa-button>
      {#if nativeUrl}<wa-button variant="neutral" href={nativeUrl}>Connexion PocketBase</wa-button>{/if}
      <wa-button variant="neutral" href="/dashboard">Dashboard</wa-button>
    </div>
  {/if}
</div>
