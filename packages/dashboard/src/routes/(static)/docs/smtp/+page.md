---
title: Email sortant dans PocketBase
description: Apprendre à configurer l'email sortant dans PocketBase avec Amazon SES ou Google Suite pour une livraison fiable
---
# Email sortant dans PocketBase

L'email sortant fiable est crucial pour beaucoup d'applications, et PocketBase propose des options flexibles pour envoyer des emails. Garantir une livraison constante et fiable peut toutefois être complexe à cause des filtres anti-spam, de la réputation d'expéditeur et de l'authentification email. Ce guide vous aide à gérer ces sujets et recommande deux solutions fiables : **Amazon SES** et **Google Suite**.

## Configurer l'email sortant dans PocketBase

![](2024-10-06-15-28-02.png)

PocketBase inclut une **fonction de test** pour vérifier que votre configuration d'email sortant fonctionne correctement. Vous pouvez l'utiliser pour envoyer des emails de test et vérifier que :

- Les emails arrivent dans la boîte de réception du destinataire.
- Le nom et l'adresse email de l'expéditeur s'affichent correctement.

### Étapes de test

1. Configurez l'email sortant avec le service choisi (SES, Google Suite ou autre fournisseur).
2. Utilisez la fonction de test email de PocketBase pour envoyer un email de test.
3. Vérifiez que l'email est reçu et que le nom et l'adresse **From** sont corrects.
4. Vérifiez que l'email arrive en boîte de réception et non dans les spams. Si les emails vont en spam, revoyez votre configuration d'authentification (SPF, DKIM, etc.).

Ce processus permet de s'assurer que la configuration d'email sortant fonctionne comme prévu avant la mise en production.

## Services recommandés

### Amazon SES

Amazon Simple Email Service (SES) est une plateforme d'envoi d'emails puissante et économique. Elle est conçue pour être scalable et fiable, ce qui en fait une bonne option pour les emails transactionnels, réinitialisations de mot de passe, etc. Suivez le guide [Amazon SES](/docs/ses) pour l'intégrer à votre instance PocketBase.

### Google Suite

Google Suite (désormais Google Workspace) fournit des services email professionnels avec votre domaine personnalisé. C'est idéal si vous voulez envoyer depuis un fournisseur reconnu tout en utilisant l'infrastructure sécurisée de Google. Un guide pas à pas est disponible ici : [Configuration email Google Suite](/docs/gs-gmail).

## Complexité d'un email sortant fiable

Envoyer des emails sortants fiables ne se limite pas à cliquer sur "envoyer". La délivrabilité peut être affectée par :

- **Filtres anti-spam** : les emails peuvent être marqués comme spam si la configuration est incorrecte.
- **Authentification** : DKIM, SPF et DMARC sont essentiels pour que vos emails soient considérés comme fiables.
- **Réputation d'expéditeur** : des envois réguliers et de qualité construisent une bonne réputation, indispensable à long terme.

Utiliser des fournisseurs reconnus comme Amazon SES et Google Suite aide à gérer ces complexités grâce à leur expertise d'infrastructure email.
