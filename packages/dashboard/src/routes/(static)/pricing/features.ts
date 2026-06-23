import { DISCORD_URL } from '$lib/appEnv'
import regions from './regions.png'

export const features = [
  {
    title: 'Entrée globale',
    description:
      'Les edge locations Fly.io terminent le trafic au plus près de vos utilisateurs, puis routent via un réseau privé vers PocketHost pour un accès faible latence partout.',
    img: regions,
  },
  {
    title: 'Essai sans risque',
    description: `Testez PocketHost jusqu'à 7 jours. Carte bancaire requise.`,
  },
  {
    title: 'Tarifs pensés pour les développeurs',
    description: `Commencez pour seulement 5 $ par instance. Après 5 instances, c'est gratuit.`,
  },
  {
    title: 'Non mesuré',
    description:
      'Profitez d’une bande passante, d’un stockage et de ressources de calcul non mesurés dans le cadre de notre <a href="/docs/pricing-ethos" class="text-primary">politique d’utilisation raisonnable</a>.',
  },

  {
    title: 'Accès FTP',
    description: "Accédez facilement à vos données depuis n'importe quel client FTP.",
  },
  {
    title: 'Versions PocketBase',
    description: `Nous prenons en charge le dernier correctif de chaque version mineure de PocketBase.`,
  },
  {
    title: 'Sécurisé',
    description: 'Infrastructure sécurisée avec chiffrement RSA-2048 et protocoles de sécurité standards.',
  },

  {
    title: 'Community',
    description: `Accès au <a href="${DISCORD_URL}" class="text-primary">Discord</a> avec plus de 1 500 développeurs et ressources techniques.`,
  },
  {
    title: 'Support prioritaire',
    description: `Accès à des canaux de support privés sur <a href="${DISCORD_URL}" class="text-primary">Discord</a>.`,
  },
  {
    title: 'Domaine personnalisé',
    description: 'Intégration fluide de domaines personnalisés pour vos instances PocketHost.',
  },
  {
    title: 'Option à vie',
    description: "Offre limitée pour un accès Pro à vie (jusqu'à 250 instances) en un seul paiement.",
  },
  {
    title: 'Fiable',
    description: 'Une disponibilité garantie de 99,95 % assure une disponibilité constante de vos applications.',
  },
  {
    title: 'Géré',
    description: "Gestion complète de l'infrastructure, incluant scaling, sauvegardes et maintenance.",
  },
  {
    title: 'Abordable',
    description:
      'Pourquoi perdre du temps à gérer des serveurs ? Mettez vos ressources dans la construction de votre prochaine grande idée. Votre budget vous dira merci.',
  },
  {
    title: 'Open source',
    description:
      'Votre abonnement soutient directement le développement des projets open source PocketBase et PocketHost.',
  },
]
