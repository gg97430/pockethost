<script lang="ts">
  import AlertBar from '$components/AlertBar.svelte'
  import CodeSample from '$components/CodeSample.svelte'
  import FeatureTab from '$components/FeatureTab.svelte'
  import QuickReference from '$components/QuickReference.svelte'
  import { INSTANCE_BARE_HOST } from '$lib/appEnv'
  import { client } from '$src/pocketbase-client'
  import { isUserPaid } from '$util/stores'
  import { dns } from 'svelte-highlight/languages'
  import { instance } from '../store'
  import { onDestroy } from 'svelte'
  import { browser } from '$app/environment'
  import { isInstanceFullyOff } from '$util/instancePower'

  const { updateInstance } = client()

  $: ({ cname, id } = $instance)
  $: isFullyOff = isInstanceFullyOff($instance)

  let healthCheckInterval: ReturnType<typeof setTimeout>
  let domainHealthy: boolean | null = null

  onDestroy(() => {
    clearInterval(healthCheckInterval)
  })

  const checkDomainHealth = async (domain: string): Promise<boolean> => {
    try {
      const url = `https://${domain}/api/firewall/health`
      const response = await fetch(url, {
        method: 'GET',
        signal: AbortSignal.timeout(10000),
      })

      if (response.status === 200) {
        const data = await response.json()
        return data.code === 200
      }

      return false
    } catch {
      return false
    }
  }

  $: cnameToCheck = regex.test(cname.trim()) && !errorMessage ? cname.trim() : null
  $: {
    if (cnameToCheck) {
      pollHealth()
    }
  }

  const pollHealth = async () => {
    clearTimeout(healthCheckInterval)

    if (cnameToCheck) {
      domainHealthy = await checkDomainHealth(cnameToCheck)
    } else {
      domainHealthy = null
    }

    healthCheckInterval = setTimeout(pollHealth, 5000)
  }

  if (browser) {
    pollHealth()
  }

  let formCname = cname
  $: {
    formCname = cname
  }

  let isButtonDisabled = false
  let errorMessage = ''

  const regex = /^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,24}$/

  $: {
    isButtonDisabled = (!!formCname.trim() && !regex.test(formCname)) || (!formCname.trim() && !cname.trim())
  }

  const onRename = (e: Event) => {
    e.preventDefault()

    if (!isFullyOff) return

    const trimmed = formCname.trim()

    if (trimmed && !$isUserPaid) {
      errorMessage = `Vous avez atteint une limite. Cette fonctionnalité est réservée à l'offre Pro. Veuillez <a class='text-primary' href="/account">changer d'offre.</a>`
      return
    }

    isButtonDisabled = true
    errorMessage = ``

    if (trimmed.length > 0 && !regex.test(trimmed)) {
      errorMessage = `Le domaine doit être valide (sous-domaine facultatif)`
      isButtonDisabled = false
      return
    }

    updateInstance({
      id,
      fields: {
        cname: formCname,
      },
    }).catch((error) => {
      if (error.response?.data?.cname?.code === 'validation_not_unique') {
        errorMessage = `Ce domaine est déjà utilisé. Veuillez utiliser un autre domaine.`
      } else {
        errorMessage = error.data.message
      }
    })

    isButtonDisabled = false
  }
</script>

<FeatureTab
  title="Domaine personnalisé (CNAME)"
  documentation="/docs/custom-domain"
  powerOffAction="modifier le domaine personnalisé"
  {errorMessage}
>
  <svelte:fragment slot="summary">
    <p>Faites pointer un CNAME chez votre fournisseur DNS vers le nom d'hôte de votre instance.</p>
  </svelte:fragment>

  <svelte:fragment slot="alerts">
    {#if cnameToCheck}
      {#if domainHealthy}
        <AlertBar message="Votre domaine personnalisé est actif." type="success" />
      {:else if domainHealthy === false}
        <AlertBar
          message="Nous n'arrivons pas à vérifier l'état de votre domaine personnalisé. Vérifiez vos paramètres CNAME puis réessayez."
          type="warning"
        />
      {:else if domainHealthy === null}
        <AlertBar message="Vérification de l'état de votre domaine personnalisé..." type="info" />
      {/if}
    {/if}
  </svelte:fragment>

  <form class="flex rename-instance-form-container-query gap-4" onsubmit={onRename}>
    <div class="relative flex-1">
      <wa-input
        title="Seuls les formats de domaine valides sont autorisés"
        type="text"
        value={formCname}
        oninput={(e: Event) => (formCname = (e.currentTarget as HTMLInputElement).value)}
        class="w-full pr-10"
        disabled={!isFullyOff}
      ></wa-input>
      {#if cnameToCheck}
        <div class="absolute right-3 top-1/2 transform -translate-y-1/2">
          {#if domainHealthy === true}
            <span class="text-green-500 text-lg">✓</span>
          {:else if domainHealthy === false}
            <span class="text-red-500 text-lg">✗</span>
          {:else if domainHealthy === null}
            <span class="inline-block w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
          {/if}
        </div>
      {/if}
    </div>

    <wa-button type="submit" variant="danger" disabled={!isFullyOff || isButtonDisabled}>Mettre à jour le domaine</wa-button
    >
  </form>

  <svelte:fragment slot="reference">
    {#if cname && regex.test(formCname.trim())}
      <QuickReference>
        <CodeSample code={`${formCname} CNAME ${INSTANCE_BARE_HOST($instance)}`} language={dns} className="" embedded />
      </QuickReference>
    {/if}
  </svelte:fragment>
</FeatureTab>

<style>
  .rename-instance-form-container-query {
    flex-direction: column;
  }

  @container (min-width: 400px) {
    .rename-instance-form-container-query {
      flex-direction: row;
    }
  }
</style>
