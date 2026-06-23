<script lang="ts">
  import { onDestroy, onMount } from 'svelte'
  import Testimonials from '$src/components/Testimonials.svelte'
  import { daysUntilFlounderSunset, flounderDaysLeftLabel, FLOUNDER_SALES_END_LABEL } from '$util/flounderSunset'
  import Features from './Features.svelte'
  import FlounderCountdown from './FlounderCountdown.svelte'
  import SignupBox from './SignupBox.svelte'

  let flounderDaysLeft = daysUntilFlounderSunset()

  let interval: ReturnType<typeof setInterval> | undefined

  onMount(() => {
    interval = setInterval(() => {
      flounderDaysLeft = daysUntilFlounderSunset()
    }, 60_000)
  })

  onDestroy(() => {
    if (interval) clearInterval(interval)
  })

  $: flounderBadge = flounderDaysLeft > 0 ? flounderDaysLeftLabel(flounderDaysLeft).toUpperCase() : undefined
</script>

<div class="pricing-page-header">
  <h2 class="pricing-page-title">Hébergement abordable</h2>
  <p class="pricing-page-subtitle">Performances premium</p>
  <FlounderCountdown />
</div>

<div class="pricing-page-grid">
  <SignupBox
    price="$5 / month"
    priceDetail="par instance"
    title="Starter"
    cta="Payez 5 $ par instance, jusqu'à un maximum de 5 instances."
    features={[
      'Accès à toutes les fonctionnalités',
      'Essai sans risque de 7 jours',
      'Entrée globale Fly, routage réseau privé',
      'Bande passante, stockage et CPU illimités',
      'Accès FTP',
      "Flexible - payez seulement ce dont vous avez besoin",
    ]}
  />
  <SignupBox
    selected
    bestDeal
    price="$25 / month"
    title="Unlimited"
    cta="Payez seulement 25 $ par mois pour accéder à toutes les fonctionnalités avec des instances illimitées !"
    features={["Tout ce qui est inclus dans l'offre Starter", 'Instances illimitées']}
  />
  <SignupBox
    buttonText="Devenir Flounder"
    price="359 $ une fois"
    title="Flounder - Lifetime"
    badgeText={flounderBadge}
    badgeUrgent
    cta="Payez une fois pour un hébergement Pro à vie. Les ventes se terminent le {FLOUNDER_SALES_END_LABEL}. Aucun nouvel achat après cette date."
    features={[
      "Tout ce qui est inclus dans l'offre Unlimited",
      'Accès à vie',
      'Aucun frais récurrent',
      'T-shirt',
      'Discord privé #onlyflounders',
      '-Petite amie',
    ]}
  />
</div>

<div class="relative my-20 w-full max-w-3xl mx-auto rounded-3xl overflow-hidden shadow-lg aspect-video">
  <iframe
    src="https://www.youtube.com/embed/Xe0FrGzlcVM"
    title="Démo PocketHost"
    class="w-full h-full"
    allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
    allowfullscreen
  ></iframe>
</div>

<div class="flex flex-col items-center gap-10 mb-20 px-4">
  <h2 class="text-4xl font-semibold text-center text-white">Fonctionnalités puissantes</h2>
  <Features />
</div>

<Testimonials />
