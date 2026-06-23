<script lang="ts">
  import { userStore } from '$util/stores'

  $: maxInstances = $userStore?.subscription_quantity || 0
</script>

<wa-card class="max-w-sm bg-[#111111]/80 border border-white/10 shadow-md">
  <div class="wa-card-body">
    <h2 class="text-xl font-bold mb-2">Limite atteinte</h2>
    <p>
      Ce compte ne peut pas créer d'instance supplémentaire. Modifiez `subscription_quantity` dans l'admin PocketBase
      si vous voulez augmenter le quota.
    </p>
  </div>
</wa-card>
