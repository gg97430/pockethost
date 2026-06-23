Pendant la [bascule Mothership v0.39](/blog/mothership-pocketbase-v039), je redémarrais les daemons edge plus souvent que d'habitude. BetterStack affichait quelques minutes de micro-coupures. Pour la plupart des requêtes, cela voulait dire une page lente, pas une page cassée.

Pour certains d'entre vous, cela voulait dire des **erreurs 500**. Un client me l'a dit franchement : quand PocketHost avait un à-coup, tout son frontend tombait avec. C'est une remarque légitime, même quand l'interruption est courte et "prévue".

J'ai livré deux changements le même après-midi. Ensemble, ils réduisent fortement la douleur.

### Ce qui n'allait pas

Chaque instance hébergée tourne dans Docker sur un noeud **edge**. Le **firewall** est devant et proxifie le trafic vers le daemon edge, qui route les requêtes vers le bon conteneur.

Quand je recyclais le daemon edge pendant la migration, deux mauvaises choses se produisaient à la suite :

1. Le firewall perdait immédiatement son upstream. Les connexions ouvertes échouaient vite. Votre app voyait une erreur dure au lieu d'une courte attente.
2. L'ancien daemon s'arrêtait en **stoppant tous les conteneurs actifs**, puis redémarrait et les **relançait à froid**. Cela ajoutait du mouvement inutile au redémarrage lui-même.

Les outils de monitoring et les checks d'uptime avaient globalement raison. L'expérience utilisateur était pire que ce que les graphiques suggéraient.

### Correctif 1 : retenir le trafic au niveau du firewall

Le firewall traite maintenant un daemon edge momentanément indisponible comme un redémarrage de sous-système, pas comme une erreur fatale.

Avant de proxifier le trafic d'instance, il interroge `/_api/daemon/health` sur l'edge. Si le daemon démarre encore ou réconcilie son état après un redémarrage, le firewall **retient la requête** et réessaie toutes les 500 ms, jusqu'à **60 secondes** par défaut.

Du point de vue de votre app, cela ressemble généralement à un chargement lent, pas à un 500 instantané. Les navigateurs et clients HTTP attendent. BetterStack peut encore signaler une micro-coupure si l'attente dure longtemps, mais vos utilisateurs ne voient souvent jamais de page cassée.

Si l'edge n'est toujours pas prêt après la fenêtre de grâce, vous recevez un **503** avec `Retry-After: 5` et un message simple indiquant que le daemon d'hébergement redémarre. C'est un signal d'indisponibilité volontaire, pas une erreur proxy opaque.

Les chemins de health probe contournent la période de grâce afin que les moniteurs externes voient toujours correctement la séquence de démarrage de l'edge.

Les auto-hébergeurs peuvent régler ce comportement avec `PH_FIREWALL_DAEMON_GRACE_MS` (défaut `60000`) et `PH_FIREWALL_DAEMON_GRACE_RETRY_MS` (défaut `500`). Mettez la grâce à `0` pour la désactiver.

### Correctif 2 : garder les conteneurs d'instance en marche

Le second correctif est côté edge.

Quand le daemon recevait SIGTERM, il arrêtait auparavant tous les conteneurs PocketBase gérés avant de quitter. Au démarrage suivant, il devait tous les relancer. C'était sûr, mais coûteux pendant les redémarrages routiniers du daemon.

Maintenant, le daemon se **détache**. Les conteneurs Docker continuent de tourner sous leurs noms d'ID d'instance. Quand le daemon revient, il **réconcilie** ce qui tourne déjà : adoption des conteneurs chauds, arrêt des orphelins et respect de `power` et des différences de version. Ensuite seulement il marque le trafic prêt et renvoie `{ status: 'ok' }` sur l'endpoint de santé.

Les instances chaudes ne remarquent souvent même pas que le daemon a été recyclé.

### Ce que vous devriez remarquer

- Une courte maintenance edge pendant les travaux plateforme devrait ressembler à de la latence, pas à un 500 global.
- Les instances déjà actives devraient revenir plus vite après un redémarrage de daemon.
- Si quelque chose est réellement cassé pendant plus d'une minute, vous obtenez toujours un 503 clair, pas une erreur mystérieuse venant de votre framework frontend.

Ce n'est pas du zéro downtime. Je reste une seule personne qui déploie une grosse migration. Il y aura des micro-coupures. L'objectif était d'éviter qu'un redémarrage interne de dix secondes devienne une catastrophe visible par vos utilisateurs.

### Une note sur la résilience frontend

Découpler votre frontend de la disponibilité backend reste une bonne pratique. Une page "on revient vite" vaut mieux que n'importe quel 503. PocketHost ne devrait toutefois pas rendre cela nécessaire pour un simple recyclage de daemon. Ces changements sont ma part du contrat côté hébergement.

Si vous avez vu des 500 pendant le déploiement v0.39 et plus depuis, c'est probablement pour cette raison. Questions ou régression ? [Discord](https://discord.gg/nVTxCMEcGT), comme toujours.
