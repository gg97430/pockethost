import { createHash, timingSafeEqual } from 'node:crypto'

const digest = (value: string) => createHash('sha256').update(value).digest()

/** Comparaison en temps constant sur des empreintes (longueurs égales). Un secret vide ou absent ne valide jamais rien. */
export const secretMatches = (candidate: unknown, secret: string | undefined) =>
  !!secret && typeof candidate === 'string' && timingSafeEqual(digest(candidate), digest(secret))
