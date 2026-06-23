<script lang="ts">
  import { INSTANCE_ADMIN_URL, INSTANCE_URL } from '$lib/appEnv'
  import { isInstanceFullyOff } from '$util/instancePower'
  import type { InstanceFields } from 'pockethost/common'

  export let instance: InstanceFields
  export let busyAction = ''
  export let copiedKey = ''
  export let onCopy: (key: string, text: string) => void = () => {}
  export let onBackup: () => void = () => {}
  export let onDuplicate: () => void = () => {}

  $: instanceUrl = INSTANCE_URL(instance)
  $: adminUrl = INSTANCE_ADMIN_URL(instance)
  $: canDuplicate = isInstanceFullyOff(instance)
  $: isBusy = !!busyAction
</script>

<div class="instance-actions" aria-label="Actions rapides de l'instance">
  <a class="instance-action instance-action--primary" href={instanceUrl} target="_blank" rel="noreferrer">
    <wa-icon name="arrow-up-right-from-square"></wa-icon>
    Ouvrir
  </a>

  <a class="instance-action" href={adminUrl} target="_blank" rel="noreferrer">
    <img src="/images/pocketbase-logo.svg" alt="" />
    Admin
  </a>

  <button type="button" class="instance-action" onclick={() => onCopy('url', instanceUrl)}>
    <wa-icon name={copiedKey === 'url' ? 'check' : 'copy'}></wa-icon>
    {copiedKey === 'url' ? 'Copié' : 'Copier URL'}
  </button>

  <button type="button" class="instance-action" disabled={isBusy} onclick={onBackup}>
    <wa-icon name={busyAction === 'backup' ? 'rotate' : 'floppy-disk'}></wa-icon>
    {busyAction === 'backup' ? 'Sauvegarde...' : 'Sauvegarder'}
  </button>

  <button
    type="button"
    class="instance-action"
    disabled={isBusy || !canDuplicate}
    onclick={onDuplicate}
    title={canDuplicate ? 'Dupliquer la base' : "Éteignez l'instance avant duplication"}
  >
    <wa-icon name={busyAction === 'duplicate' ? 'rotate' : 'clone'}></wa-icon>
    {busyAction === 'duplicate' ? 'Duplication...' : 'Dupliquer'}
  </button>

  <a class="instance-action" href={`/instances/${instance.id}/logs`}>
    <wa-icon name="scroll"></wa-icon>
    Logs
  </a>
</div>

<style>
  .instance-actions {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(8.5rem, 1fr));
    gap: 0.75rem;
  }

  .instance-action {
    display: inline-flex;
    min-height: 2.75rem;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    border: 1px solid var(--app-border);
    border-radius: 0.75rem;
    background: var(--app-surface);
    color: var(--app-text-strong);
    font-size: 0.875rem;
    font-weight: 800;
    line-height: 1;
    text-decoration: none;
    cursor: pointer;
    transition:
      transform 120ms ease,
      border-color 120ms ease,
      background-color 120ms ease,
      color 120ms ease;
  }

  .instance-action:hover:not(:disabled) {
    transform: translateY(-1px);
    border-color: rgb(59 130 246 / 0.45);
    background: rgb(59 130 246 / 0.1);
    color: #60a5fa;
  }

  .instance-action--primary {
    border-color: rgb(30 184 84 / 0.45);
    background: linear-gradient(135deg, rgb(30 184 84 / 0.2), rgb(59 130 246 / 0.12));
    color: #4ade80;
  }

  .instance-action:disabled {
    opacity: 0.48;
    cursor: not-allowed;
  }

  .instance-action img {
    width: 1rem;
    height: 1rem;
  }

  :global(html[data-theme='light']) .instance-action--primary {
    color: #15803d;
  }
</style>
