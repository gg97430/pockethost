<script lang="ts">
  import { webLabels } from '$lib/webApps'
  export let status = ''
  export let label = ''
  export let online = false
  $: tone =
    online || ['ready', 'active'].includes(status)
      ? 'success'
      : ['failed', 'error'].includes(status)
        ? 'danger'
        : ['running', 'starting', 'queued', 'pending_certificate'].includes(status)
          ? 'working'
          : 'neutral'
</script>

<span class="web-status" data-tone={tone}
  ><span class="web-status-dot" aria-hidden="true"></span>{label ||
    webLabels[status] ||
    status ||
    'Non disponible'}</span
>
