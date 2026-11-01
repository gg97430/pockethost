# Allocation adaptative des ressources web

Le mode adaptatif partage la capacité CPU et admet les charges selon leur demande mémoire et la pression réelle. Les plafonds restent des protections, pas une consommation préallouée. Il n'augmente pas automatiquement un plafond lors d'un OOM et ne réduit jamais à chaud le plafond mémoire d'un conteneur actif.

## Configuration d'une application

Dans **Moteur**, choisir l'allocation et la priorité, puis préparer l'aperçu, enregistrer et redéployer. Les applications sans politique explicite utilisent le mode adaptatif ; leurs plafonds existants sont conservés.

```json
{
  "kind": "dockerfile",
  "allocation": "adaptive",
  "priority": "normal",
  "memoryRequestMiB": 512,
  "resources": { "cpuMillicores": 2000, "memoryMiB": 4096, "pids": 256 }
}
```

- `memoryRequestMiB` est la mémoire habituelle comptée pour l'admission et transmise comme réservation **souple** Docker. Elle n'est pas une garantie physique. Minimum 32 Mio, maximum le plafond mémoire. Défaut : la moitié du plafond, avec un maximum de 256 Mio.
- `resources.memoryMiB` reste le plafond de pointe : un processus peut être tué s'il l'atteint. En mode adaptatif, un service accepte jusqu'à 8 Gio / 4 CPU ; ses valeurs existantes ne sont pas augmentées silencieusement.
- `resources.cpuMillicores` est un plafond CPU. Plusieurs projets peuvent déclarer des plafonds dont la somme dépasse le budget parent : le noyau partage le temps CPU disponible.
- `priority` vaut `low`, `normal` ou `high` : poids CPU respectifs 256, 1024 et 2048. Pour Compose, le poids du projet est réparti entre ses services ; la demande mémoire totale est répartie selon leur capacité au-delà du minimum Docker de 6 Mio par service, avec une somme exactement égale à la demande du projet et sans dépasser le plafond d’un service. L'admission utilise la somme exacte des réservations résultantes.
- Les quotas CPU/RAM optionnels du **projet regroupant plusieurs applications** restent stricts : l'allocation adaptative ne les contourne pas. Leur contrôle conserve les plafonds, y compris lors de la coexistence de deux versions.
- `allocation: "fixed"` conserve l'admission prudente par addition des plafonds. Les limites ordinaires 1 CPU / 1536 Mio restent applicables hors Compose.

L'usage de la RAM peut augmenter ou diminuer à la demande dans le plafond existant. Ce mécanisme ne force pas le ramasse-miettes d'un langage à rendre sa mémoire, ne crée pas de RAM et ne garantit pas tous les pics simultanés. Choisir une mémoire habituelle représentative de l'application, à partir des graphes de supervision. Une valeur artificiellement basse augmente le risque de saturation.

## Admission et protection du serveur

Le worker vérifie directement les compteurs cgroup, sans dépendre d'un graphe ou d'un cache de supervision potentiellement ancien.

1. Les conteneurs actifs doivent conserver des plafonds CPU/RAM explicites. Une limite manquante ou une réservation invalide refuse l'admission.
2. Chaque ancêtre cgroup doit contenir le maximum entre l'usage réel non récupérable et les demandes déjà engagées, plus la nouvelle demande et une marge de **10 %**, au minimum **512 Mio**. Seul le cache fichier inactif est considéré récupérable, jamais le tas mémoire applicatif.
3. Le serveur entier doit disposer de la nouvelle demande et d'une marge de **10 % de la RAM physique**, au minimum **1 Gio**. Cela tient compte des BDD rootful et des services hors du groupe web.
4. Une pression mémoire PSI `some avg10 >= 10` diffère les nouvelles charges. Si PSI n'existe pas sur le noyau, les compteurs mémoire restent obligatoires. Des données présentes mais invalides refusent l'admission.
5. Une construction engage son plafond mémoire complet. Elle a un poids CPU 128, inférieur à celui des sites, un plafond CPU, un plafond mémoire et aucun swap supplémentaire autorisé. Sa priorité s'applique lors de la contention, pas comme ralentissement permanent.
6. Les constructions attendent au plus **120 secondes**, avec réévaluation toutes les 5 secondes, renouvellement du bail et vérification de l'annulation. L'expiration échoue proprement ; elle ne crée pas une boucle de tentatives infinie et ne stoppe pas la version publiée. Les erreurs de configuration ne sont pas réessayées.

La marge physique est un contrôle d'admission, pas une réservation exclusive du noyau pour les BDD. Une fuite mémoire ou des pics simultanés déjà admis peuvent toujours provoquer un OOM. Conserver les limites globales, la supervision et des plafonds cohérents pour les BDD et les autres services. En cas de danger pendant une commande de construction, le contrôle périodique existant peut interrompre cette construction ; il ne redimensionne pas les sites publiés.

## Déploiement et retour arrière

Déployer ensemble le worker, `web_app_runtime.py`, les hooks recompilés et le dashboard. Les réglages se trouvent dans le profil JSON existant : aucune migration de collection, aucune modification des comptes ou des variables BDD.

Le changement de poids et de réservation mémoire prend effet lors d'un **nouveau déploiement du site**. Les conteneurs existants restent intacts ; ceux qui n'ont pas de réservation souple continuent d'engager leur plafond complet jusqu'à leur redéploiement. L'admission CPU du worker devient immédiatement adaptative. Le builder est recréé avec `--keep-state` si ses options changent, uniquement dans la boucle de prérequis entre les jobs : prévoir une mise à jour sans construction active.

Le fichier privé du worker accepte :

```text
WEB_APPS_ALLOCATION_MODE=adaptive
```

La valeur par défaut est `adaptive`. `fixed` impose l'admission fixe au serveur entier, même aux profils adaptatifs, pour les prochains démarrages. Redémarrer uniquement le worker dans un créneau sans job ; les conteneurs existants ne sont pas modifiés. Ce retour arrière ne remet pas automatiquement la somme des plafonds sous le budget : si le serveur est déjà surengagé, l'admission fixe peut refuser les nouveaux déploiements jusqu'à ajustement.

Le profil public de réception du serveur utilise `allocationMode` avec la même valeur. En mode adaptatif, `siteMemoryGiB` représente la demande de démarrage prévue (une charge), pas la somme des plafonds de pointe. Le contrôle statique vérifie qu'une construction ou cette demande tient avec la marge ; l'admission vérifie la disponibilité réelle avant chaque charge. Les champs de réserve CPU ne constituent pas une garantie CPU en mode adaptatif.

## Validation

- `python3 packages/pockethost/scripts/test-web-app-allocation.py` : partage des pics, demandes/plafonds, ancêtres cgroup, réserve hôte, PSI, refus de métriques invalides, priorités, Compose, annulation et attente bornée.
- Tests worker/runtime/Railpack existants, tests TypeScript des profils et recette API PocketBase jetable.
- Sur un hôte Linux : construire et publier une application jetable ; vérifier `NanoCpus`, `CpuShares`, `MemoryReservation`, `Memory` et `MemorySwap` des conteneurs et du builder ; tester la concurrence et vérifier que les BDD et les sites existants restent disponibles. Ne pas utiliser les sites clients pour une simulation de saturation.

Références : [ressources Docker](https://docs.docker.com/engine/containers/resource_constraints/) et [options du builder](https://docs.docker.com/build/builders/drivers/docker-container/).
