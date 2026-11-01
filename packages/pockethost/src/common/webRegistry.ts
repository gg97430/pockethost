/**
 * Applications web — hôtes de registre d'images. Logique pure, partagée par les hooks mothership (Goja JSVM). Le worker
 * tire les images depuis le réseau de l'hôte : un locataire ne vise ni la boucle locale ni le réseau interne. Contrôle
 * syntaxique uniquement : un nom public peut toujours se résoudre vers une adresse privée.
 */
const HOST = /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/
const PRIVATE_SUFFIXES = ['arpa', 'corp', 'home', 'internal', 'intranet', 'lan', 'local', 'localdomain', 'localhost']
const IMAGE = /^(?:[a-z0-9.-]+(?::[0-9]{1,5})?\/)?[a-z0-9][a-z0-9._/-]*@sha256:[a-f0-9]{64}$/

/** Nom DNS complet (un point, TLD alphabétique : ni localhost, ni IP, ni hôte court), port facultatif. */
export const webRegistryHostAllowed = (value: unknown) => {
  const match = typeof value === 'string' ? /^([^:/]+)(?::([1-9][0-9]{0,4}))?$/.exec(value) : null
  if (!match || Number(match[2] || 0) > 65535 || !HOST.test(match[1]!)) return false
  return !PRIVATE_SUFFIXES.includes(match[1]!.split('.').pop()!)
}
/**
 * Image fixée par digest. Règle de Docker : le premier segment est un registre s'il contient « . » ou « : » ou vaut
 * localhost, sinon l'image vient de Docker Hub. `localhost/x` et `10.0.0.1/x` sont aussi de simples chemins pour
 * l'expression : le contrôle porte donc sur ce segment, pas sur le groupe d'hôte.
 */
export const webImageDigestValid = (value: unknown) => {
  if (typeof value !== 'string' || !IMAGE.test(value)) return false
  const first = value.split('/')[0]!
  return !value.includes('/') || !(/[.:]/.test(first) || first === 'localhost') || webRegistryHostAllowed(first)
}
