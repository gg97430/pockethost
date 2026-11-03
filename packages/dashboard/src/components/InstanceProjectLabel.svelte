<script lang="ts">
  import { client } from '$src/pocketbase-client'
  import { globalInstancesStore, patchGlobalInstance } from '$util/stores'
  import type { InstanceFields } from 'pockethost/common'

  export let instance: InstanceFields
  let editing = false
  let value = ''
  let busy = false
  let error = ''
  $: labels = [
    ...new Set(
      Object.values($globalInstancesStore)
        .map((row) => row.projectLabel)
        .filter(Boolean)
    ),
  ].sort()
  const open = () => {
    value = instance.projectLabel || ''
    error = ''
    editing = true
  }
  const save = async (event: SubmitEvent) => {
    event.preventDefault()
    busy = true
    error = ''
    try {
      const projectLabel = value.normalize('NFC').trim().replace(/ +/g, ' ')
      await client().updateInstance({ id: instance.id, fields: { projectLabel } })
      patchGlobalInstance(instance.id, { projectLabel })
      editing = false
    } catch (e) {
      error = client()
        .parseError(e instanceof Error ? e : new Error('Enregistrement impossible.'))
        .join('\n')
    } finally {
      busy = false
    }
  }
</script>

<div
  class="project-label"
  role="presentation"
  onclick={(e) => e.stopPropagation()}
  onkeydown={(e) => e.stopPropagation()}
>
  {#if editing}
    <form onsubmit={save}>
      <input
        aria-label={`Label de ${instance.subdomain}`}
        bind:value
        maxlength="60"
        list={`labels-${instance.id}`}
        placeholder="Ex. 1PRIME"
        disabled={busy}
      />
      <datalist id={`labels-${instance.id}`}>
        {#each labels as label}<option value={label}></option>{/each}
      </datalist>
      <button type="submit" disabled={busy}>{busy ? '…' : 'OK'}</button>
      <button
        type="button"
        disabled={busy}
        onclick={() => (editing = false)}
        aria-label="Annuler la modification du label">×</button
      >
    </form>
    <span class="hint">Laisser vide pour retirer le label.</span>
    {#if error}<span class="error" role="alert">{error}</span>{/if}
  {:else}
    <button
      class="label-button"
      class:assigned={!!instance.projectLabel}
      type="button"
      onclick={open}
      title={`Modifier le label de ${instance.subdomain}`}
    >
      <wa-icon name="tag"></wa-icon>{instance.projectLabel || 'Ajouter un label'}
    </button>
  {/if}
</div>

<style>
  .project-label {
    margin-top: 0.35rem;
    max-width: 100%;
  }
  .label-button {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    max-width: 100%;
    border: 1px dashed var(--app-border);
    background: transparent;
    color: var(--app-text-faint);
    padding: 0.15rem 0.45rem;
    border-radius: 0.3rem;
    font-size: 0.72rem;
    overflow-wrap: anywhere;
    cursor: pointer;
    text-align: left;
  }
  .assigned {
    border-style: solid;
    color: var(--app-accent);
    background: color-mix(in srgb, var(--app-accent) 8%, transparent);
  }
  button:focus-visible,
  input:focus-visible {
    outline: 2px solid var(--app-accent);
    outline-offset: 2px;
  }
  form {
    display: flex;
    gap: 0.25rem;
    flex-wrap: wrap;
  }
  input {
    width: 8rem;
    min-width: 0;
    flex: 1;
  }
  input,
  form button {
    border: 1px solid var(--app-border);
    border-radius: 0.25rem;
    background: var(--app-surface);
    color: var(--app-text-strong);
    padding: 0.3rem;
    font-size: 0.75rem;
  }
  form button {
    cursor: pointer;
  }
  .hint,
  .error {
    display: block;
    font-size: 0.7rem;
    margin-top: 0.25rem;
  }
  .hint {
    color: var(--app-text-muted);
  }
  .error {
    color: var(--app-danger, #ef7272);
  }
</style>
