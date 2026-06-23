<script lang="ts">
  import { goto } from '$app/navigation'
  import { onMount } from 'svelte'

  onMount(() => {
    goto('/dashboard')
  })
</script>

<div class="py-16 text-center text-sm text-neutral-400">Redirection vers le dashboard...</div>
