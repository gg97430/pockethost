<script lang="ts">
  import CodeSample from '$components/CodeSample.svelte'
  import { INSTANCE_URL } from '$lib/appEnv'
  import { plaintext } from 'svelte-highlight/languages'
  import type { InstanceFields } from 'pockethost/common'

  export let instance: InstanceFields

  $: url = INSTANCE_URL(instance)
  $: installSnippet = `npm i pocketbase`
  $: connectionSnippet = `import PocketBase from 'pocketbase';\n\nconst url = '${url}'\nconst client = new PocketBase(url)`
  $: firstQuerySnippet = `const records = await client.collection('posts').getFullList({\n  sort: '-created',\n});`
</script>

<details class="developer-snippet">
  <summary>
    <span>
      <wa-icon name="code"></wa-icon>
      Connexion développeur
    </span>
    <wa-icon name="chevron-down" class="developer-snippet-chevron"></wa-icon>
  </summary>

  <div class="developer-snippet-grid">
    <div class="developer-snippet-block">
      <p>URL PocketBase</p>
      <CodeSample code={url} language={plaintext} embedded />
    </div>
    <div class="developer-snippet-block">
      <p>Installer le SDK</p>
      <CodeSample code={installSnippet} embedded />
    </div>
    <div class="developer-snippet-block developer-snippet-block--wide">
      <p>Connexion</p>
      <CodeSample code={connectionSnippet} embedded />
    </div>
    <div class="developer-snippet-block developer-snippet-block--wide">
      <p>Première requête</p>
      <CodeSample code={firstQuerySnippet} embedded />
    </div>
  </div>
</details>

<style>
  .developer-snippet {
    margin-top: 1rem;
    border: 1px solid var(--app-border);
    border-radius: 0.75rem;
    background: var(--app-surface);
    color: var(--app-text);
    overflow: hidden;
  }

  .developer-snippet summary {
    display: flex;
    min-height: 3rem;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    padding: 0 1rem;
    color: var(--app-text-strong);
    cursor: pointer;
    user-select: none;
  }

  .developer-snippet summary::-webkit-details-marker {
    display: none;
  }

  .developer-snippet summary span {
    display: inline-flex;
    align-items: center;
    gap: 0.55rem;
    font-size: 0.875rem;
    font-weight: 800;
  }

  .developer-snippet-chevron {
    color: var(--app-text-muted);
    transition: transform 160ms ease;
  }

  .developer-snippet[open] .developer-snippet-chevron {
    transform: rotate(180deg);
  }

  .developer-snippet-grid {
    display: grid;
    gap: 0.875rem;
    padding: 0 1rem 1rem;
  }

  .developer-snippet-block p {
    margin: 0 0 0.45rem;
    color: var(--app-text-muted);
    font-size: 0.75rem;
    font-weight: 800;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }

  @media (min-width: 900px) {
    .developer-snippet-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .developer-snippet-block--wide {
      grid-column: 1 / -1;
    }
  }
</style>
