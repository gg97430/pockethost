<script lang="ts">
  import { defaultWebProjectLimits, type WebProjectLimits } from '../../../../pockethost/src/common/webProjectLimits'
  export let value: WebProjectLimits = defaultWebProjectLimits()
  export let disabled = false
  export let onchange: () => void = () => {}
</script>

<div class="grid gap-4">
  <h4>Limites de débit</h4>
  <label class="web-check">
    <input type="checkbox" bind:checked={value.traffic.enabled} {disabled} {onchange} />
    <span>Activer les limites de débit pour ce projet</span>
  </label>
  <p class="web-help">
    Les sites et domaines du projet partagent les mêmes compteurs par IP, connexions WebSocket comprises. Les
    changements s’appliquent sous quelques secondes.
  </p>
  {#if value.traffic.enabled}
    <div class="grid gap-4 sm:grid-cols-2">
      <label class="web-field"
        ><span>Requêtes par minute et par IP</span><input
          type="number"
          min="1"
          max="1000000"
          step="1"
          bind:value={value.traffic.requestsPerMinute}
          {disabled}
          oninput={onchange}
        /></label
      >
      <label class="web-field"
        ><span>Connexions simultanées par IP</span><input
          type="number"
          min="1"
          max="10000"
          step="1"
          bind:value={value.traffic.concurrentRequests}
          {disabled}
          oninput={onchange}
        /></label
      >
    </div>
  {/if}
  <h4>Quotas de ressources</h4>
  <label class="web-check">
    <input type="checkbox" bind:checked={value.resources.enabled} {disabled} {onchange} />
    <span>Activer les quotas de ressources pour ce projet</span>
  </label>
  <p class="web-help">
    CPU et RAM cumulées des services du projet, vérifiées avant chaque démarrage. Les versions déjà en ligne restent
    actives. Prévoyez de la marge pour deux versions pendant un déploiement. Les limites du serveur restent applicables.
  </p>
  {#if value.resources.enabled}
    <div class="grid gap-4 sm:grid-cols-2">
      <label class="web-field"
        ><span>CPU totale des sites (millicœurs)</span><input
          type="number"
          min="50"
          max="32000"
          step="50"
          bind:value={value.resources.cpuMillicores}
          {disabled}
          oninput={onchange}
        /><span class="web-help">1 000 millicœurs = 1 cœur.</span></label
      >
      <label class="web-field"
        ><span>RAM totale des sites (Mio)</span><input
          type="number"
          min="64"
          max="131072"
          step="1"
          bind:value={value.resources.memoryMiB}
          {disabled}
          oninput={onchange}
        /></label
      >
      <label class="web-field"
        ><span>Durée maximale de construction (minutes)</span><input
          type="number"
          min="1"
          max="30"
          step="1"
          bind:value={value.resources.buildMinutes}
          {disabled}
          oninput={onchange}
        /></label
      >
      <label class="web-field"
        ><span>Déploiements en attente au maximum</span><input
          type="number"
          min="1"
          max="100"
          step="1"
          bind:value={value.resources.queuedBuilds}
          {disabled}
          oninput={onchange}
        /></label
      >
    </div>
  {/if}
</div>
