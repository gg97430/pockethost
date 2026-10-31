import { writable } from 'svelte/store'
import type { FeatureTabNavSection } from './featureTabTypes'

export type WorkspaceFeatureNavigation = {
  owner: symbol
  title: string
  sections: FeatureTabNavSection[]
  backHref?: string
}

// Populated by mounted feature layouts only; no account state is shared in SSR.
export const workspaceFeatureNavigation = writable<WorkspaceFeatureNavigation | null>(null)
