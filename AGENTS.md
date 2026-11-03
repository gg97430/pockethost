# Consignes de déploiement

## Serveurs actifs

- Les seuls serveurs à maintenir sont **app3.monappli.re**, **app4.monappli.re** et **app5.monappli.re**.
- Par défaut, les demandes de mise à jour de la plateforme concernent **les trois serveurs**, sauf indication contraire explicite de l’utilisateur. Ne pas redemander ce périmètre.
- **app1 et app2 sont retirés pour le moment** : ne pas les contacter, les modifier ou les remettre en service sans nouvelle demande explicite.
- Toute évolution commune de Kolab Hosting (interface, contrôleur, droits, moteur, corrections, migrations) doit être livrée et vérifiée sur **app3, app4 et app5 dans la même intervention**.
- Un déploiement de plateforme n'est terminé qu'après vérification des trois serveurs. Si un serveur échoue, corriger ou revenir à la dernière version commune ; signaler explicitement tout écart restant.
- Utiliser la publication sur toute la flotte et terminer par `python3 packages/pockethost/scripts/platform-fleet.py check`. Procédure : `docs/hosting/fleet-releases.md`.
- Conserver les domaines, adresses, secrets, utilisateurs, bases, applications, volumes et limites propres à chaque serveur. Ne jamais synchroniser les données métier ou copier les secrets d'un serveur à l'autre pour aligner le code.
- Les applications hébergées (CRM, sites, workers métier) gardent leurs déploiements propres ; elles ne sont pas des versions du logiciel de plateforme.

## Code et publication

- Consulter `MEMORY.md` pour l'architecture et les procédures existantes.
- Préserver les autres travaux locaux. Construire un lot isolé et cohérent, avec sauvegarde et retour arrière, plutôt que publier des modifications en cours sans les vérifier.
- Les fichiers `pb_hooks/mothership.js` et `mothership.pb.js` sont générés depuis TypeScript. Les régénérer avec le compilateur du projet.

## Authentification

- Le TOTP est obligatoire dès la première connexion au tableau de bord Kolab pour tous les comptes, superadmins compris, sur chaque serveur actuel et futur. Aucun accès aux bases ou hébergements avant validation du premier code. Aucun réglage ne doit permettre de le désactiver.
