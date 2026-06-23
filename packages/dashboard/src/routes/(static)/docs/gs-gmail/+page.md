---
title: Configurer un nouveau domaine email dans Google Suite
description: Apprendre à configurer un domaine email dans Google Suite
---

# Configurer un nouveau domaine email dans Google Suite

La configuration d'un nouveau domaine email dans Google Suite se fait en plusieurs étapes. Ce guide couvre l'ajout du domaine, l'activation de Gmail et la mise en place d'une adresse catch-all. En suivant ces étapes, vous pourrez configurer correctement votre domaine email et assurer une communication fluide dans votre organisation.

<!-- @import "[TOC]" {cmd="toc" depthFrom=2 depthTo=6 orderedList=false} -->

<!-- code_chunk_output -->

- [Ajouter le domaine](#ajouter-le-domaine)
- [Vérification du domaine](#vérification-du-domaine)
- [Activer Gmail](#activer-gmail)
- [Ajouter une adresse catch-all](#ajouter-une-adresse-catch-all)
- [Tester la configuration email](#tester-la-configuration-email)
- [Ajouter un compte d'expédition dans Gmail](#ajouter-un-compte-dexpédition-dans-gmail)

<!-- /code_chunk_output -->

## Ajouter le domaine

Allez dans l'[admin des domaines](https://admin.google.com/ac/domains/manage) et ajoutez un nouveau domaine.

![](2024-09-08-19-12-14.png)

Configurez un "alias domain". Cette option implique une étape de vérification où vous devez mettre à jour vos enregistrements DNS. Sinon, vous devrez peut-être effectuer les étapes suivantes manuellement.

![](2024-09-08-19-04-47.png)

## Vérification du domaine

Si vous utilisez Cloudflare comme gestionnaire DNS, Google le reconnaîtra automatiquement et ajoutera les enregistrements DNS nécessaires.

![](2024-09-08-19-06-15.png)

![](2024-09-08-19-07-10.png)

Ensuite, attendez la fin de la vérification du domaine. Cela devrait être rapide.

![](2024-09-08-19-07-35.png)

## Activer Gmail

![](2024-09-08-19-11-38.png)

![](2024-09-08-19-12-59.png)

Là encore, Cloudflare rend l'opération simple et automatique. Sinon, des étapes manuelles peuvent être nécessaires si Google n'est pas intégré à votre fournisseur DNS.

![](2024-09-08-19-14-04.png)

![](2024-09-08-19-15-06.png)

## Ajouter une adresse catch-all

Je configure une adresse catch-all pour que tout email envoyé à `<anything>@yourdomain.com` soit redirigé vers `admin@yourdomain.com` si l'adresse précise n'existe pas. C'est optionnel, mais il est utile de décider ce qui arrive aux adresses email non reconnues dans votre configuration.

[Cet article d'aide Google](https://apps.google.com/supportwidget/articlehome) couvre le sujet, mais voici aussi les étapes :

Allez dans [Default Routing](https://admin.google.com/ac/apps/gmail/defaultrouting) et ajoutez une règle :

![](2024-09-08-19-21-45.png)

Utilisez une regex pour cibler tout le domaine, par exemple `@yourdomain\.com$`. Vous pouvez aussi préfixer l'objet avec `[CATCHALL] - ` pour savoir que le message n'a pas été envoyé à votre adresse "réelle".

![](2024-09-08-19-25-59.png)

Redirigez les emails vers votre adresse "réelle" :

![](2024-09-08-19-25-14.png)

Enfin, assurez-vous d'appliquer cette règle uniquement aux adresses email non reconnues.

![](2024-09-08-19-24-55.png)

![](2024-09-08-19-51-48.png)

## Tester la configuration email

Envoyez-vous un email avec le nouveau domaine pour vérifier que tout fonctionne correctement.

Il peut être nécessaire d'attendre un peu que les enregistrements MX se propagent. Le fait que Google indique que tout est vérifié ne veut pas dire que l'ensemble d'Internet a déjà reçu l'information.

![](2024-09-08-19-39-27.png)

Pour vérifier plus largement, essayez d'envoyer un message depuis un fournisseur email totalement différent comme Proton ou AOL.

Dès que vous recevez les messages, la configuration est prête.

## Ajouter un compte d'expédition dans Gmail

Vous pouvez aussi vouloir envoyer des emails DEPUIS votre domaine, par exemple pour répondre ou contacter des clients. Pour cela, allez dans les paramètres utilisateur de Gmail et ajoutez un compte :

![](2024-09-08-19-56-56.png)

![](2024-09-08-19-57-35.png)
