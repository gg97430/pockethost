<script lang="ts">
  import { assertExists } from 'pockethost/common'
  import CodeSample from '$components/CodeSample.svelte'
  import FeatureTab from '$components/FeatureTab.svelte'
  import QuickReference from '$components/QuickReference.svelte'
  import { instance } from '../store'
  import SecretsInner from './Inner.svelte'
  import { items } from './stores'

  assertExists($instance, `Expected instance here`)
  const { subdomain } = $instance

  $: {
    const { secrets } = $instance
    items.clear()

    for (const [name, value] of Object.entries(secrets || {})) {
      items.upsert({ name, value })
    }
  }

  $: code =
    `// pb_hooks/env-test.pb.js\n\n` +
    ($items.length > 0
      ? $items
          .map(({ name, value }) => `const ${name} = process.env.${name}\nconsole.log("${name}: ", ${name})`)
          .join('\n')
      : `const YOUR_KEY = process.env.YOUR_KEY`)
</script>

<svelte:head>
  <title>Secrets de {subdomain} - Gestion PocketBase</title>
</svelte:head>

<FeatureTab title="Secrets" documentation="/docs/secrets">
  <svelte:fragment slot="summary">
    <p>
      Ces secrets sont transmis à votre processus <code>pocketbase</code> comme variables d'environnement. Ils sont
      aussi accessibles depuis tous les <code>pb_hooks</code> que vous avez créés.
    </p>
  </svelte:fragment>

  <svelte:fragment slot="cta">
    {#if $items.length === 0}
      <wa-callout variant="brand" class="wa-callout-padded wa-callout-brand-accent">
        <wa-icon slot="icon" name="user-secret"></wa-icon>
        <span>Aucun secret pour le moment. Créez votre premier secret pour commencer.</span>
      </wa-callout>
    {/if}
  </svelte:fragment>

  <SecretsInner />

  <svelte:fragment slot="reference">
    {#if $items.length > 0}
      <QuickReference>
        <CodeSample {code} className="" embedded />
      </QuickReference>
    {/if}
  </svelte:fragment>
</FeatureTab>
