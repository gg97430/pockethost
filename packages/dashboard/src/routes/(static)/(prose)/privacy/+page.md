<div class="prose">

# Politique de confidentialité

**Dernière mise à jour : 5 octobre 2024**

Chez PocketHost, nous nous engageons à protéger votre vie privée et à assurer la sécurité de vos données. Cette Politique de confidentialité explique comment nous collectons, utilisons et protégeons vos informations lorsque vous utilisez nos services.

## 1. Introduction

PocketHost fournit des services d'hébergement pour les applications PocketBase. En utilisant nos services, vous acceptez la collecte et l'utilisation des informations conformément à cette politique.

## 2. Collecte et utilisation des données

### 2.1 Informations collectées

- **Informations personnelles** : nous collectons uniquement les informations personnelles que vous nous fournissez volontairement, comme votre adresse email. Ces informations sont nécessaires pour la création de compte, l'authentification et les communications.

### 2.2 Utilisation de vos informations

- **Communication** : votre adresse email est utilisée uniquement pour les communications transactionnelles (confirmations de compte, réinitialisations de mot de passe et notifications de service) et pour vous envoyer des informations sur nos services.

- **Aucune vente à des tiers** : nous ne vendons ni ne louons vos informations personnelles à des tiers.

- **Tiers autorisés** : nous pouvons partager vos informations avec des prestataires tiers autorisés à communiquer en notre nom, uniquement dans le but de vous fournir nos services.

### 2.3 Données agrégées

- **Statistiques publiques** : nous pouvons publier des données agrégées, comme le nombre d'utilisateurs, le nombre d'instances et d'autres statistiques de plateforme. Ces informations ne contiennent aucune donnée permettant de vous identifier personnellement et servent à informer le public sur l'utilisation de la plateforme.

### 2.4 Dépannage anonymisé

- **Support communautaire** : il peut nous arriver d'évoquer certains comportements utilisateur sur des plateformes comme Discord à des fins de dépannage. Ces discussions sont toujours anonymisées pour protéger votre identité.

## 3. Stockage et sécurité des données

### 3.1 Environnement d'hébergement

Nous utilisons des prestataires tiers de confiance, **DigitalOcean** et **Fly.io**, pour héberger notre infrastructure.

#### Mesures de sécurité de DigitalOcean et Fly.io

- **Chiffrement au repos** : les deux prestataires proposent le chiffrement du stockage des volumes au repos, ce qui protège les données contre les accès physiques non autorisés.

- **Sécurité réseau** : des mesures avancées comme les pare-feu et les systèmes de détection d'intrusion sont utilisées.

- **Standards de conformité** : ils respectent des certifications et réglementations de sécurité reconnues, notamment le RGPD, ISO 27001 et SOC 2 Type II.

- **Contrôles d'accès** : des contrôles d'accès stricts sont en place, avec authentification multifacteur et permissions basées sur les rôles.

- **Sauvegardes régulières et redondance** : des sauvegardes régulières et des systèmes redondants préviennent la perte de données et assurent une haute disponibilité.

### 3.2 PocketBase Instances

Chaque instance PocketBase s'exécute dans un conteneur Docker sécurisé avec accès uniquement à ses propres données, ce qui renforce la sécurité par l'isolation.

#### Données au repos

- **Stockage non chiffré des données** : même si les volumes VPS sont chiffrés, les données dans chaque instance PocketBase, y compris les bases SQLite et les fichiers uploadés, sont stockées non chiffrées au repos, car PocketBase ne prend pas en charge le chiffrement au repos.

- **Risques potentiels** : une compromission de l'accès administrateur au VPS pourrait exposer les données utilisateur non chiffrées présentes dans votre instance PocketBase.

### 3.3 Sécurité SSH et chiffrement des données

- **Accès SSH** : nos serveurs sont sécurisés avec des clés SSH 2048 bits, afin que seul le personnel autorisé puisse y accéder.

- **Chiffrement des données en transit** : toutes les données transmises entre nos serveurs sont chiffrées avec des protocoles standards du secteur afin de les protéger contre l'interception.

### 3.4 Utilisation de Cloudflare

Nous utilisons les services **Cloudflare** pour améliorer la sécurité et les performances.

- **Cache** : Cloudflare fournit un cache intelligent pour améliorer les temps de chargement.

- **Sécurité de l'origine** : Cloudflare agit comme proxy inverse et fournit une mitigation DDoS, une protection Web Application Firewall (WAF) et un chiffrement SSL/TLS.

- **Chiffrement SSL/TLS** : toutes les connexions entre les utilisateurs finaux et Cloudflare sont chiffrées, ce qui assure une transmission sécurisée des données.

### 3.5 Recommandations pour renforcer la sécurité

- **Utilisation d'un stockage compatible S3** : nous encourageons fortement les utilisateurs à configurer PocketBase avec un service de stockage compatible S3 (Amazon S3, Backblaze B2 ou Wasabi, par exemple) pour les fichiers et les sauvegardes, car ces services proposent un stockage chiffré au repos.

- **Sauvegardes chiffrées** : utiliser un stockage S3 pour les sauvegardes garantit que vos données sont chiffrées pendant le transit et au repos.

## 4. Cookies et technologies de suivi

- **Authentification et analytics** : nous utilisons des cookies pour gérer l'authentification des utilisateurs et collecter des analytics afin d'améliorer nos services.

- **Aucun suivi publicitaire** : nous n'utilisons pas de cookies ni de scripts pour le suivi publicitaire ou des finalités similaires.

- **Refus** : vous pouvez configurer votre navigateur pour refuser les cookies, mais cela peut affecter le fonctionnement de nos services.

## 5. Partage et divulgation des données

- **Aucune vente de données personnelles** : nous ne vendons ni ne louons vos informations personnelles à qui que ce soit.

- **Prestataires tiers** : nous pouvons partager vos informations avec des prestataires tiers qui nous aident à exploiter nos services, dans le cadre d'accords de confidentialité stricts.

- **Obligations légales** : nous pouvons divulguer vos informations si la loi l'exige ou en réponse à des demandes valides d'autorités publiques.

## 6. Droits des utilisateurs

- **Accès et correction** : vous avez le droit d'accéder à vos données personnelles et de les corriger à tout moment.

- **Portabilité des données** : vous pouvez demander une copie de vos données dans un format structuré et lisible par machine.

- **Suppression** : vous pouvez demander la suppression de vos données personnelles, sous réserve de certaines obligations légales.

## 7. Modifications de cette Politique de confidentialité

Nous pouvons mettre à jour cette Politique de confidentialité périodiquement. Nous vous informerons de tout changement significatif en publiant la nouvelle version sur cette page avec une date d'entrée en vigueur mise à jour.

## 8. Nous contacter

Pour toute question ou préoccupation concernant cette Politique de confidentialité, contactez-nous :

- **Email** : ben@pockethost.io
- **Adresse** : PO Box 871, Reno NV 89501.

</div>
