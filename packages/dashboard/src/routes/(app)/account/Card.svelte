<script lang="ts">
  import { DISCORD_URL } from '$lib/appEnv'
  import Check from './Check.svelte'

  export let name = "Nom de l'offre"
  export let active = false
  export let features: string[] = []
  export let upgradable = false
  export let startLimit = 0
  export let limit = 0
  export let prices: { title: string; link: string }[] = []
</script>

<div class="m-10 inline-block">
  <wa-card class="w-96 {active ? 'wa-card-active' : 'wa-card-muted'} shadow-xl">
    <div class="wa-card-body">
      <h2 class="text-xl font-bold mb-4">
        {name}
      </h2>
      {#if startLimit > 0}
        {#if limit > 0}
          {#if limit != startLimit}
            <div class="text-center text-primary text-2xl">
              <span class="line-through">{startLimit}</span>
              <span class="text-error">{limit} restantes</span>
            </div>
          {:else}
            <div class="text-center text-primary text-2xl">
              {limit} restantes
            </div>
          {/if}
        {:else}
          <div class="text-center text-error text-2xl">ÉPUISÉ</div>
        {/if}
      {/if}
      <ul>
        {#each features as feature}
          <li><Check /> {feature}</li>
        {/each}
      </ul>

      <slot />

      <div class="mt-10">
        {#if active}
          <div class="text-success text-center text-2xl">C'est votre offre actuelle.</div>
          <p class="mt-10 text-white/70">
            Pour changer d'offre, contactez <a class="text-primary" href={`"${DISCORD_URL}"`}
              ><code>.noaxis</code> sur Discord</a
            >
          </p>
        {:else if prices.length > 0}
          <div class="flex justify-center gap-2 flex-wrap">
            {#each prices as price}
              {#if (startLimit > 0 && limit === 0) || !upgradable}
                <wa-button variant="brand" disabled>{price.title}</wa-button>
              {:else}
                <wa-button href={price.link} variant="brand" target="_blank">{price.title}</wa-button>
              {/if}
            {/each}
          </div>
          {#if !upgradable}
            Cette offre n'est pas modifiable depuis l'interface interne.
          {/if}
        {/if}
      </div>
    </div>
  </wa-card>
</div>
