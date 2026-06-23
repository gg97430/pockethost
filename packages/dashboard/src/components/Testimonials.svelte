<script lang="ts">
  const testimonials = [
    {
      name: 'Riddge Mussington',
      title: '',
      quote:
        "J'utilise PocketHost pour plusieurs projets et je pense qu'il serait difficile de trouver un meilleur hébergeur pour des projets PocketBase. C'est très simple à utiliser et à configurer, sécurisé par défaut grâce à PocketBase, et les mises à jour PocketBase arrivent vite avec une bonne disponibilité.",
    },
    {
      name: 'H.Mohamed',
      title: '',
      quote: 'Excellente option BaaS.',
    },
    {
      name: 'Damian Kennedy',
      title: 'Créateur de AIAutomations',
      quote: `Très pratique pour mettre en place rapidement un stockage serverless avec Vercel. Pas besoin d'un setup Postgres ou Upstash coûteux.`,
    },
    {
      name: 'Olivier Mills',
      title: "Développeur web, créateur d'idées",
      quote: 'Outil incroyable pour déployer vite !',
    },
    {
      name: 'Stephane Roux',
      title: 'Fondateur de Altar JS',
      quote: 'Absolument incroyable. Construire des applications web vient de devenir plus agréable.',
    },
    {
      name: 'Giovanni Cruz',
      title: 'Développeur logiciel',
      quote: "J'ai adoré dès la première utilisation. Excellente offre.",
    },
    {
      name: 'Anele Mbanga',
      title: 'Ingénieure logiciel, exploratrice, inventrice',
      quote: 'Très simple, efficace et généreux !',
    },
    {
      name: 'Saif-Alislam Dekna',
      title: 'Rêveur, jeune pilote et entrepreneur',
      quote: '',
    },
  ]
</script>

<div class="flex flex-col items-center gap-12 mb-20 px-4 py-12">
  <h2 class="text-4xl font-bold text-center text-white tracking-tight">Ce que disent nos utilisateurs</h2>
  <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6 max-w-6xl w-full">
    {#each testimonials as testimonial}
      <div
        class="rounded-xl shadow-md p-6 transition-all duration-300 hover:shadow-xl hover:-translate-y-1 border border-white/10 bg-white/5 text-white"
      >
        <div class="flex items-center gap-1 mb-4">
          {#each Array(5) as _}
            <wa-icon name="star" class="text-yellow-400 w-5 h-5"></wa-icon>
          {/each}
        </div>
        {#if testimonial.quote}
          <p class="text-sm mb-4 italic text-gray-300 leading-relaxed">"{testimonial.quote}"</p>
        {/if}
        <div>
          <p class="font-semibold text-lg">{testimonial.name}</p>
          {#if testimonial.title}
            <p class="text-xs text-gray-400 mt-1">{testimonial.title}</p>
          {/if}
        </div>
      </div>
    {/each}
  </div>
</div>

<style>
  .grid > div {
    min-height: 160px;
  }
</style>
