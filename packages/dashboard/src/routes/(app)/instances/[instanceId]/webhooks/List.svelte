<script lang="ts">
  import { fade } from 'svelte/transition'
  import { items } from './stores'
  import { client } from '$src/pocketbase-client'
  import { instance } from '../store'
  import { logger } from 'pockethost/common'

  let expandedWebhooks: Set<string> = new Set()

  const toggleExpanded = (endpoint: string) => {
    if (expandedWebhooks.has(endpoint)) {
      expandedWebhooks.delete(endpoint)
    } else {
      expandedWebhooks.add(endpoint)
    }
    expandedWebhooks = expandedWebhooks
  }

  const getStatusColor = (status: number | undefined) => {
    if (!status) return 'text-gray-400'
    if (status >= 200 && status < 300) return 'text-success'
    if (status >= 400) return 'text-error'
    return 'text-warning'
  }

  const formatTimestamp = (timestamp: number) => {
    return new Date(timestamp).toLocaleString()
  }

  const handleDelete = (name: string) => async (e: Event) => {
    e.preventDefault()
    logger().debug(`Deleting ${name}`)
    items.delete(name)
    await client().updateInstance({
      id: $instance.id,
      fields: {
        webhooks: $items,
      },
    })
  }
</script>

<table class="table mb-8">
  <thead>
    <tr>
      <th class="w-1/3 border-b-2 border-neutral">Route API</th>
      <th class="w-1/3 border-b-2 border-neutral">Planification</th>
      <th class="w-1/6 border-b-2 border-neutral">État</th>
      <th class="w-1/6 border-b-2 border-neutral text-right">Action</th>
    </tr>
  </thead>

  <tbody>
    {#each $items as item}
      <tr transition:fade>
        <th>{item.endpoint}</th>
        <td>{item.value}</td>
        <td>
          {#if item.lastFired}
            <wa-button
              variant="neutral"
              size="s"
              appearance="plain"
              class="p-1 {getStatusColor(item.lastFired.response.status)}"
              onclick={() => toggleExpanded(item.endpoint)}
              title="Cliquer pour afficher les détails de la réponse"
            >
              <wa-icon slot="start" name={expandedWebhooks.has(item.endpoint) ? 'chevron-down' : 'chevron-right'}
              ></wa-icon>
              <span class="font-mono font-bold">{item.lastFired.response.status}</span>
            </wa-button>
          {:else}
            <span class="text-gray-400">Aucune exécution</span>
          {/if}
        </td>
        <td class="text-right">
          <wa-button
            aria-label="Supprimer"
            onclick={handleDelete(item.endpoint)}
            type="button"
            variant="warning"
            size="s"
            appearance="outline"
          >
            <wa-icon name="trash"></wa-icon>
          </wa-button>
        </td>
      </tr>

      {#if item.lastFired && expandedWebhooks.has(item.endpoint)}
        <tr transition:fade>
          <td colspan="4" class="bg-neutral-800 p-4">
            <div class="space-y-2">
              <div class="flex justify-between items-center">
                <h4 class="font-semibold">Détails de la dernière exécution</h4>
                <span class="text-sm text-gray-500">{formatTimestamp(item.lastFired.timestamp)}</span>
              </div>

              <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label class="font-medium text-sm">Code de statut :</label>
                  <div class="mt-1">
                    <wa-badge
                      variant={item.lastFired.response.status >= 200 && item.lastFired.response.status < 300
                        ? 'success'
                        : 'danger'}
                    >
                      {item.lastFired.response.status}
                    </wa-badge>
                  </div>
                </div>

                <div class="md:col-span-1">
                  <label class="font-medium text-sm">Corps de la réponse :</label>
                  <div class="mt-1">
                    <pre class="text-xs bg-neutral-800 p-2 rounded max-h-32 overflow-y-auto">{item.lastFired.response
                        .body || '(vide)'}</pre>
                  </div>
                </div>
              </div>
            </div>
          </td>
        </tr>
      {/if}
    {/each}
  </tbody>
</table>
