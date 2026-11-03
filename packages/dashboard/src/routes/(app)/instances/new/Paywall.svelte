<script lang="ts">
  import { userStore } from '$util/stores'

  $: maxInstances = $userStore?.subscription_quantity || 0
</script>

<wa-card class="max-w-sm bg-[#111111]/80 border border-white/10 shadow-md">
  <div class="wa-card-body">
    <h2 class="text-xl font-bold mb-2">Limite atteinte</h2>
    <p>
      Le quota de {maxInstances} base{maxInstances > 1 ? 's' : ''} de ce compte est atteint. Demandez à un administrateur
      d’ajuster votre quota dans Administration.
    </p>
  </div>
</wa-card>
