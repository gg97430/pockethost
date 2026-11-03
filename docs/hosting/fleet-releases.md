# Versions communes d’app3, app4 et app5

La flotte active comprend `app3.monappli.re`, `app4.monappli.re` et `app5.monappli.re`. App1 et app2 sont retirés pour le moment. Toute évolution commune de Kolab Hosting est livrée aux trois serveurs dans la même intervention.

Les applications des clients gardent leurs versions, données, bases, volumes et secrets. Les adresses, domaines, clés, comptes et réglages de ressources de chaque serveur restent locaux. Des ressources ou nombres d’applications différents ne constituent pas un écart de version.

## Vérification

Depuis la racine du dépôt :

```sh
python3 packages/pockethost/scripts/platform-fleet.py check
```

Le contrôle interroge uniquement les trois serveurs actifs. Il vérifie leur identifiant de version, l’empreinte commune des sources, les empreintes des fichiers de plateforme installés et des assets propres à chaque domaine, ainsi que la santé du contrôleur. Il échoue si une version manque, si le code a changé depuis sa publication ou si les trois serveurs ne sont pas à la même version.

Les anciennes migrations automatiques déjà appliquées à un seul serveur restent dans son historique. On compare le schéma fonctionnel et les migrations communes ; on ne rejoue pas une ancienne migration d’un serveur sur l’autre pour rendre leurs historiques identiques.

Les contrôles de démarrage propres à chaque hôte font partie du précontrôle, notamment celui du service de reprise d’app4. Son assemblage initial reste vérifié strictement ; en fonctionnement normal, les nouvelles bases sont admises si les bases migrées sont toujours présentes et que les chemins actuels sont valides. Les clés, archives et preuves de migration restent contrôlées. Le namespace historique des anciennes sauvegardes est conservé sans contacter les serveurs retirés.

## Publication sur toute la flotte

Préparer un instantané isolé des sources, les compilations propres aux trois domaines, les tests, les sauvegardes et un installateur réversible. Aucun fichier `.env`, secret ou fichier de données métier n’entre dans les archives de publication.

```sh
python3 packages/pockethost/scripts/platform-fleet.py publish /chemin/du/lot
```

Le lot contient `release.json`, un installateur et une archive par serveur. Le manifeste contient un identifiant commun, `sourceHash`, `installer: {file, sha256}` et `targets: {app3: {file, sha256}, app4: {file, sha256}, app5: {file, sha256}}`. Les trois cibles et les empreintes sont obligatoires ; une publication isolée ou vers une ancienne cible est refusée.

L’installateur reçoit l’archive locale et une phase : `prepare`, `activate`, `verify`, `commit` ou `rollback`. Il contrôle les fichiers actuels avant toute écriture, conserve la configuration locale, prépare le retour arrière du code et n’émet que des résultats agrégés sans secret. Les phases sont idempotentes. Il n’applique aucun retour arrière automatique des données métier.

Les archives sont transférées et les trois précontrôles terminent avant la première activation. Les trois serveurs doivent être vérifiés avant de valider la publication. Un échec pendant l’activation ou la vérification déclenche le retour arrière de tous les serveurs touchés, y compris celui dont l’activation n’a été que partielle. Une divergence restante est signalée explicitement.

Après validation, chaque serveur conserve `/var/lib/kolab-platform-release.json` avec `id`, `sourceHash`, `releaseRoot`, `sharedFiles` et `uiFiles`. Les entrées de `sharedFiles` sont préfixées par `release:` (relatives au répertoire de la plateforme) ou `installed:` (relatives à `/opt/pockethost-web`). Les anciens assets immuables sont conservés pour les sessions déjà ouvertes.

Si plusieurs répertoires de code alimentent encore les services d’un serveur, ils figurent dans `runtimeRoots` et sont vérifiés eux aussi. Un même serveur ne doit pas mélanger une ancienne version du pare-feu ou du daemon avec un contrôleur récent.

Une livraison n’est terminée qu’après le contrôle final réussi sur **app3, app4 et app5**.
