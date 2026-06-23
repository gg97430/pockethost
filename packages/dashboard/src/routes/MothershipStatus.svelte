<script lang="ts">
  import { isMothershipReachable } from '$util/stores'
</script>

{#if !$isMothershipReachable}
  <div class="bg-error text-error-content p-4 rounded-none">
    <div class="text-center">
      <h2 class="text-xl font-bold mb-2">Connexion perdue</h2>
      <p>
        Le serveur central est temporairement inaccessible. Vos instances sont en sécurité et restent joignables.
        Réessayez dans quelques instants.
      </p>
    </div>
  </div>
{/if}
