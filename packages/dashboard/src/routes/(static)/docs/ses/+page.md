---
title: Configurer AWS SES
description: Guide rapide pour configurer AWS SES afin d'envoyer des emails depuis votre instance PocketBase avec Google Suite comme fournisseur email
---
# Guide rapide : configurer l'email avec Google Suite et AWS SES

<!-- @import "[TOC]" {cmd="toc" depthFrom=2 depthTo=6 orderedList=false} -->

<!-- code_chunk_output -->

- [Prérequis : assurez-vous d'abord de pouvoir recevoir des emails](#prérequis--assurez-vous-dabord-de-pouvoir-recevoir-des-emails)
- [Créer une identité SES](#créer-une-identité-ses)
- [Ajouter les enregistrements DNS](#ajouter-les-enregistrements-dns)
- [Ajouter un domaine FROM personnalisé](#ajouter-un-domaine-from-personnalisé)
- [Ajouter les paramètres DMARC](#ajouter-les-paramètres-dmarc)
- [Vérifier la configuration](#vérifier-la-configuration)
- [Soumettre pour approbation](#soumettre-pour-approbation)
- [Créer les identifiants SMTP](#créer-les-identifiants-smtp)
- [Configurer SMTP dans PocketBase](#configurer-smtp-dans-pocketbase)

<!-- /code_chunk_output -->

## Prérequis : assurez-vous d'abord de pouvoir recevoir des emails

Pour recevoir des emails, vous avez besoin d'un fournisseur email. J'utilise Google Suite, mais vous pouvez choisir le fournisseur que vous préférez.

[Configurer un nouveau domaine email dans Google Suite](/docs/gs-gmail).

(Il faudrait aussi documenter la même configuration avec Proton.me.)

## Créer une identité SES

Connectez-vous à votre compte Amazon SES et suivez leur guide de démarrage pour créer une adresse FROM et un domaine d'envoi. Vérifiez que vous êtes dans la région souhaitée. C'est important si vous avez déjà des domaines, car AWS n'apprécie pas les demandes d'approbation sur plusieurs régions d'envoi.

![](2024-09-08-20-29-10.png)

Commencez par créer une identité :

![](2024-09-08-20-30-52.png)

![](2024-09-08-20-33-41.png)

## Ajouter les enregistrements DNS

Vous devez ajouter chez votre fournisseur DNS les enregistrements fournis par SES. Contrairement à Google Suite, ces valeurs doivent être saisies manuellement. AWS SES ne fournit pas d'intégration directe avec les fournisseurs DNS.

![Configuration DNS AWS SES](2024-09-03-06-20-55.png)

**Note pour les utilisateurs Cloudflare : si vous utilisez Cloudflare, assurez-vous que ces enregistrements ne sont pas proxifiés.**

![](2024-09-08-22-28-33.png)

## Ajouter un domaine FROM personnalisé

La délivrabilité est meilleure si vous ajoutez un domaine FROM personnalisé :

![](2024-09-08-22-38-12.png)

![](2024-09-08-22-38-51.png)

Cela nécessite quelques entrées DNS supplémentaires. Notez que la `priority` MX doit aller dans un champ séparé, pas dans le champ du serveur MX :

![](2024-09-08-22-41-08.png)

![](2024-09-08-22-42-35.png)

## Ajouter les paramètres DMARC

![](2024-09-08-22-45-05.png)

## Vérifier la configuration

Après avoir ajouté les enregistrements DNS, retournez dans AWS SES et attendez que le domaine apparaisse comme vérifié. Envoyez ensuite un email de vérification. Vous devriez le recevoir si tout est correctement configuré.

![](2024-09-08-22-32-26.png)

![](2024-09-08-22-35-46.png)

Si la vérification prend trop de temps, allez dans **Identities > DKIM**, cliquez sur **Edit**, puis sur **Save** pour déclencher une revérification immédiate.

![](2024-09-08-22-33-32.png)

## Soumettre pour approbation

Avant de pouvoir envoyer des emails à n'importe qui, vous devez soumettre votre configuration pour approbation dans AWS SES. Cette étape est cruciale pour éviter les problèmes lors de l'envoi d'emails hors de votre domaine vérifié.

Si vous avez déjà fait approuver un domaine auparavant, vous pourrez peut-être ignorer cette étape. Pour le vérifier, essayez simplement d'envoyer un message de test vers une adresse appartenant à un autre domaine.

## Créer les identifiants SMTP

Créez des identifiants SMTP dans la console AWS SES. Ces identifiants seront utilisés dans le panneau admin PocketBase pour envoyer des emails.

![](2024-09-08-22-48-53.png)

## Configurer SMTP dans PocketBase

Une fois vos identifiants SMTP obtenus, allez dans l'admin PocketBase.

Saisissez les identifiants SMTP dans le panneau admin PocketBase et vérifiez que l'adresse **SENDER** est correctement définie. Si SES n'a pas encore vérifié votre email, vous pouvez tester avec un envoi vers `test@<yourdomain>`, qui devrait passer si le domaine est le même.

![](2024-09-08-22-53-02.png)
