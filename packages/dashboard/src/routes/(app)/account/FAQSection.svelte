<script>
  import { DISCORD_URL } from '$lib/appEnv'
  import FAQItem from '$src/routes/(app)/account/FAQItem.svelte'
</script>

<div class="mx-auto max-w-7xl divide-y divide-gray-500 px-6 py-24 sm:py-32 lg:px-8 lg:py-40">
  <h2 class="text-2xl font-bold leading-10 tracking-tight">Questions fréquentes</h2>

  <dl class="mt-10 space-y-8 divide-y divide-gray-500">
    <FAQItem question={`Qu'est-ce que l'offre "Legacy" ?`}>
      <p class="mb-4">
        Les comptes Legacy ont accès aux projets et fonctionnalités existants, mais ne peuvent pas créer de nouveaux
        projets ni utiliser les nouvelles fonctionnalités.
      </p>
      <p>
        Si vous passez à une offre payante puis revenez ensuite en arrière, vous garderez accès à vos projets et
        fonctionnalités Legacy, mais les nouveaux projets et fonctionnalités créés avec une offre payante ne
        fonctionneront plus.
      </p>
    </FAQItem>

    <FAQItem question="Contribution à la communauté open source">
      <p class="mb-4">
        La plateforme s'engage à contribuer à la communauté open source qui aide à faire évoluer PocketBase.
      </p>
      <p class="mb-4">
        10 % du produit net (après dépenses) sont reversés à la communauté. Plus précisément, des dons sont faits au
        projet PocketBase et à ses principaux contributeurs.
      </p>
      <p>De plus, 1 % des frais d'abonnement est collecté par Stripe pour réduire l'empreinte carbone dans le monde.</p>
    </FAQItem>

    <FAQItem question="Politique d'utilisation raisonnable">
      <p class="mb-4">
        Quand nous disons "illimité", nous l'entendons au sens d'une utilisation raisonnable. Évidemment, tout a des
        limites. En étudiant les usages de la plateforme, nous avons constaté que même les instances les plus
        actives sollicitent rarement fortement notre système.
      </p>
      <p class="mb-4">
        Gestion PocketBase est conçu pour les développeurs qui veulent lancer et itérer rapidement sur des idées sans se soucier
        de la facturation à l'usage ni de l'infrastructure.
      </p>
      <p class="mb-4">
        Si votre application grossit et commence à affecter le système, nous discuterons d'une offre entreprise ou d'une
        configuration dédiée.
      </p>
      <p>
        Utilisez vos instances en sachant que vous pouvez consommer autant de stockage, de bande passante et de CPU que
        votre application le nécessite dans des conditions normales. Laissez-nous gérer l'hébergement pour que vous
        puissiez vous remettre au travail.
      </p>
    </FAQItem>

    <FAQItem question="Annulation et remboursements">
      <p class="mb-4">Version courte : nous ne voulons votre argent que si vous êtes satisfait.</p>
      <p class="mb-4">
        Version longue : si vous devez annuler votre abonnement pour une raison quelconque, veuillez <a
          class="text-primary"
          href={DISCORD_URL}>contacter <code>.noaxis</code> sur Discord</a
        >. Si vous annulez dans les 5 premiers jours suivant une inscription ou un renouvellement, nous remboursons le
        montant complet. Sinon, nous remboursons au prorata. Ça vous va ?
      </p>
      <p>
        Si vous créez des instances supplémentaires puis revenez à l'offre gratuite, les instances en trop resteront
        accessibles dans votre dashboard, mais elles ne fonctionneront pas.
      </p>
    </FAQItem>

    <!-- Autres questions... -->
  </dl>
</div>
