export type BlogTocEntry = {
  title: string
  path: string
  description: string
  date?: string
  author?: string
}

export const toc: BlogTocEntry[] = [
  {
    title: 'Aperçu de PocketHost 3.0 : Flounder s’arrête le 1 juillet',
    path: '/blog/pockethost-30-preview',
    description:
      "À savoir avant le 1 juillet : fin des ventes Flounder à vie, nouvelles formules Free/Pro/Agency uniquement pour les nouvelles inscriptions, comptes existants conservés.",
    date: '20 juin 2026',
    author: 'capn',
  },
  {
    title: 'Dashboard Live Mothership (extensions UI admin)',
    path: '/blog/mothership-live-admin-plugin',
    description:
      "Premier plugin admin mothership : page Live de la flotte. Un /_/extensions.js vide venait d'une réponse pré-plugin mise en cache par Cloudflare ; règle de bypass et leçons init/realtime après la tempête de 429.",
    date: '17 juin 2026',
    author: 'capn',
  },
  {
    title: 'Redémarrages edge progressifs (moins de 500 pendant la maintenance)',
    path: '/blog/graceful-edge-restarts',
    description:
      "Le délai de grâce du daemon firewall garde le trafic d'instance jusqu'à 60 s pendant le redémarrage de l'edge. Les conteneurs restent lancés et se réattachent au boot au lieu de redémarrer à froid.",
    date: '17 juin 2026',
    author: 'capn',
  },
  {
    title: 'Mothership est sur PocketBase 0.39',
    path: '/blog/mothership-pocketbase-v039',
    description:
      "Le plan de contrôle a enfin franchi la bascule JSVM v0.22 -> v0.23. Point de non-retour, fichiers SQLite plus petits et déploiement surveillé tant que les instances continuent de tourner.",
    date: '14 juin 2026',
    author: 'capn',
  },
  {
    title: 'Préparation plateforme pré-v0.39 : tests, CI et dépendances',
    path: '/blog/pre-39-platform-deps-refresh',
    description:
      "Avant la bascule Mothership v0.39 : suite Vitest, garde-fous CI, mise à jour progressive des dépendances, SDK PocketBase 0.27, double auth admin pour Mothership 0.22.",
    date: '14 juin 2026',
    author: 'capn',
  },
  {
    title: 'Préparer en amont la bascule Mothership v0.39',
    path: '/blog/mothership-v039-prestage',
    description:
      "Mise à niveau en deux phases : double auth admin et dépendances sur main d'abord, aucun changement de lockfile sur la branche v39, vues SQL supprimées tôt. Le jour J ne bascule que Mothership.",
    date: '14 juin 2026',
    author: 'capn',
  },
  {
    title: 'phio 0.4 : déployer via SFTP',
    path: '/blog/phio-sftp-deploy',
    description:
      "phio dev et deploy synchronisent maintenant via SFTP sur le port 2222 avec une clé de déploiement créée automatiquement. Le lien projet passe à .phioconfig.",
    date: '14 juin 2026',
    author: 'capn',
  },
  {
    title: 'Clés d’accès de compte : accès SFTP limité',
    path: '/blog/account-access-keys',
    description:
      "Enregistrez des clés Ed25519 dans Compte -> Clés. Accordez l'accès SFTP à toutes les instances ou à un sous-ensemble précis. Première étape vers un accès partagé au compte avec permissions limitées.",
    date: '14 juin 2026',
    author: 'capn',
  },
  {
    title: "Dernier appel pour l’accès à vie Flounder",
    path: '/blog/flounder-lifetime-sunset',
    description:
      "Les ventes Flounder à vie se terminent le 1 juillet 2026. Les comptes existants auront 30 jours après la fin pour acheter. Email envoyé à tous les utilisateurs.",
    date: '13 juin 2026',
    author: 'capn',
  },
  {
    title: 'FTPS disparaît : passez à SFTP',
    path: '/blog/ftps-sunset',
    description:
      "FTPS sur le port 21 est en fin de vie. SFTP sur 2222 avec clés SSH est la voie à suivre. phio deploy et dev utilisent déjà SFTP.",
    date: '13 juin 2026',
    author: 'capn',
  },
  {
    title: "Récupérer 300 Go de données d'instances orphelines",
    path: '/blog/edge-orphan-cleanup',
    description:
      "Le nettoyage edge a supprimé les dossiers d'instances sans enregistrement Mothership. Premier passage en production : environ 300 Go libérés, presque la moitié de l'espace utilisé sur ce noeud.",
    date: '13 juin 2026',
    author: 'capn',
  },
  {
    title: "Comment nous vacuumons des milliers d'instances sans course au démarrage",
    path: '/blog/vacuum-at-scale',
    description:
      "Locks vacuum possédés par l'edge, portes de démarrage et passages incrémentaux --hours-back gardent la compaction SQLite nocturne sûre à l'échelle de la flotte.",
    date: '13 juin 2026',
    author: 'capn',
  },
  {
    title: 'Vacuum Now : compaction SQLite à la demande',
    path: '/blog/vacuum-now',
    description:
      "Auto Vacuum tourne la nuit sur les instances inactives. Vacuum Now permet de compacter les instances chaudes depuis le dashboard quand vous devez récupérer de l'espace immédiatement.",
    date: '13 juin 2026',
    author: 'capn',
  },
  {
    title: 'Limites de débit plus intelligentes entre API et fichiers',
    path: '/blog/weighted-rate-limiting',
    description:
      "Les limites firewall pondèrent maintenant les routes /api/files 10x moins cher que les appels REST API. Plus de marge pour uploads et downloads sans relâcher la protection contre les abus API.",
    date: '13 juin 2026',
    author: 'capn',
  },
  {
    title: 'Auto Vacuum pour les instances inactives',
    path: '/blog/auto-vacuum',
    description:
      "VACUUM SQLite nocturne sur les instances en hibernation. Activé par défaut, configurable par instance, et les conteneurs actifs sont ignorés jusqu'à leur sommeil.",
    date: '13 juin 2026',
    author: 'capn',
  },
  {
    title: 'Accès fichiers SFTP',
    path: '/blog/sftp-file-access',
    description:
      "SFTP remplace FTPS pour les fichiers d'instance. Clés SSH Ed25519, enregistrement façon GitHub et docs pour Mac, Windows, Linux et les clients populaires.",
    date: '13 juin 2026',
    author: 'capn',
  },
  {
    title: 'Votre logs.db peut contenir des gigaoctets de vide',
    path: '/blog/pocketbase-sqlite-vacuum',
    description:
      "La rétention des logs PocketBase supprime des lignes, mais SQLite garde l'espace disque. Notre logs.db Mothership faisait 6,5 Go pour environ 1 800 enregistrements. Vacuum a corrigé ça en quelques secondes.",
    date: '13 juin 2026',
    author: 'capn',
  },
  {
    title: 'Un statut d’instance qui survit aux redémarrages',
    path: '/blog/runtime-status-sync',
    description:
      "Le statut d'instance du dashboard reste fiable à travers les redémarrages Mothership et edge. Une poignée de main de synchronisation miroir au lieu de suppositions obsolètes.",
    date: '13 juin 2026',
    author: 'capn',
  },
  {
    title: 'Un runtime plus léger sur Node 24',
    path: '/blog/node-24-leaner-runtime',
    description:
      "Nous avons retiré plus de 100 packages du lockfile en passant aux API natives de Node 24. Même comportement d'hébergement, stack plus légère et préparation délibérée à un futur essai Bun.",
    date: '12 juin 2026',
    author: 'capn',
  },
  {
    title: 'En cours, en veille et éteint',
    path: '/blog/instance-power-status',
    description:
      "L'extinction arrête maintenant réellement votre conteneur. Le dashboard distingue En cours et En veille, et les actions destructives attendent l'arrêt.",
    date: '12 juin 2026',
    author: 'capn',
  },
  {
    title: 'Interface dashboard : migration Web Awesome',
    path: '/blog/web-awesome-migration',
    description:
      "Le dashboard tourne maintenant sur Web Awesome : composants plus propres, docs lisibles et stack frontend plus simple.",
    date: '12 juin 2026',
    author: 'capn',
  },
  {
    title: 'Synchronisation directe des versions PocketBase',
    path: '/blog/pocketbase-version-sync',
    description:
      "Les versions PocketBase se synchronisent maintenant directement depuis GitHub. Plus d'intermédiaire Gobot, mises à jour plus rapides, aucun token requis.",
    date: '12 juin 2026',
    author: 'capn',
  },
  {
    title: 'PocketHost 2.3.0',
    path: '/blog/pockethost-2-3-0-release',
    description:
      "Notre plus grosse version à ce jour : webhooks, domaines personnalisés automatisés, paywall strict et améliorations majeures d'infrastructure.",
    date: '22 juillet 2025',
    author: 'capn',
  },
  {
    title: 'Les webhooks sont disponibles',
    path: '/blog/webhooks-launch',
    description: 'Automatisez vos workflows PocketBase avec notre nouvelle fonctionnalité de webhooks, plus fiable que les tâches cron.',
    date: '22 juillet 2025',
    author: 'capn',
  },
  {
    title: 'Synchronisation de données améliorée avec MothershipMirrorService',
    path: '/blog/mothership-mirror-service',
    description: 'Infrastructure améliorée pour une meilleure synchronisation des données et de meilleures performances du dashboard.',
    date: '21 juillet 2025',
    author: 'capn',
  },
  {
    title: 'Domaines personnalisés automatisés avec Cloudflare',
    path: '/blog/custom-domains-automation',
    description: 'Configuration entièrement automatisée des domaines personnalisés avec intégration Cloudflare et certificats SSL.',
    date: '19 juillet 2025',
    author: 'capn',
  },
  {
    title: 'Construire un jeu realtime : Kingdom',
    path: '/blog/kingdom',
    description: 'Suivez la construction d’un jeu multijoueur realtime avec PocketBase.',
    date: '8 janvier 2025',
    author: 'capn',
  },
  {
    title: 'Le paywall strict est actif',
    path: '/blog/hard-paywall-is-live',
    description: 'Le paywall strict est maintenant actif pour les nouveaux utilisateurs, avec maintien des droits pour les utilisateurs existants.',
    date: '10 janvier 2025',
    author: 'capn',
  },
  {
    title: 'La chaîne YouTube Dev est en ligne',
    path: '/blog/announcing-dev-channel',
    description: 'Notre nouvelle chaîne YouTube pour les tutoriels PocketBase et le contenu écosystème.',
    date: '8 janvier 2025',
    author: 'capn',
  },
  {
    title: 'Passage à un paywall strict',
    path: '/blog/hard-paywall',
    description: 'Pourquoi nous passons à un modèle de paywall strict et ce que cela signifie pour la communauté.',
    date: '6 janvier 2025',
    author: 'capn',
  },
  {
    title: 'Pourquoi pas de SLA ?',
    path: '/blog/why-no-sla',
    description: "Pourquoi PocketHost ne propose pas de SLA formel et ce que cela signifie pour les attentes de fiabilité.",
    date: '1 janvier 2025',
    author: 'capn',
  },
  {
    title: 'PocketHost est disponible dans plus de 40 pays',
    path: '/blog/live-in-40-countries',
    description: "L'hébergement PocketHost est maintenant disponible dans plus de 40 pays.",
    date: '25 décembre 2024',
    author: 'capn',
  },
  {
    title: 'Annonce de Pocker',
    path: '/blog/announcing-pocker',
    description: "Présentation de notre solution de conteneurs personnalisée pour l'hébergement PocketBase mondial.",
    date: '20 décembre 2024',
    author: 'capn',
  },
  {
    title: 'Et maintenant pour PocketBase ? (Early Morning Dev Round 1)',
    path: '/blog/early-morning-dev-1',
    description: 'Early Morning Dev Round 1. Réflexions sur la suite de PocketBase.',
  },
]
