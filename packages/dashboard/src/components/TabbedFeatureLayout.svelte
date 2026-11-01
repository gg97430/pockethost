<script lang="ts">
  import { workspaceFeatureNavigation } from '$lib/dashboard/workspaceNavigation'
  import type { FeatureTabNavSection } from '$lib/dashboard/featureTabTypes'
  import type { Snippet } from 'svelte'

  interface Props {
    title: string
    mobileTitle?: string
    sections?: FeatureTabNavSection[]
    width?: 'narrow' | 'wide'
    backHref?: string
    backLabel?: string
    children?: Snippet
    mobileHeader?: Snippet
    toolbar?: Snippet
    header?: Snippet
    alerts?: Snippet
  }

  let {
    title,
    mobileTitle = title,
    sections = [],
    width = 'narrow',
    backHref,
    backLabel = 'Retour',
    children,
    mobileHeader,
    toolbar,
    header,
    alerts,
  }: Props = $props()
  const owner = Symbol('feature-navigation')
  $effect(() => {
    workspaceFeatureNavigation.set({ owner, title, sections, backHref })
    return () => workspaceFeatureNavigation.update((current) => (current?.owner === owner ? null : current))
  })
</script>

<div class="tabbed-feature-layout" class:tabbed-feature-layout--wide={width === 'wide'}>
  {#if backHref}
    <a class="feature-back-link" href={backHref}><wa-icon name="arrow-left"></wa-icon>{backLabel}</a>
  {/if}
  <header class="feature-page-head">
    <div class="feature-heading-desktop">
      {#if header}{@render header()}{:else}<h1>{title}</h1>{/if}
    </div>
    <div class="feature-heading-mobile">
      {#if mobileHeader}{@render mobileHeader()}{:else}<h1>{mobileTitle}</h1>{/if}
    </div>
    {#if toolbar}<div class="feature-page-toolbar">{@render toolbar()}</div>{/if}
  </header>
  {#if alerts}<div class="feature-alerts">{@render alerts()}</div>{/if}
  <div class="feature-content" class:feature-content--narrow={width === 'narrow'}>{@render children?.()}</div>
</div>

<style>
  .tabbed-feature-layout {
    width: 100%;
    max-width: 100%;
    margin: 0;
    padding: 0;
  }
  .feature-back-link {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    margin-bottom: 17px;
    color: var(--app-text-faint);
    font-size: 11px;
    text-decoration: none;
  }
  .feature-back-link:hover {
    color: var(--app-text);
  }
  .feature-back-link wa-icon {
    font-size: 10px;
  }
  .feature-page-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 18px;
    min-height: 36px;
    margin-bottom: 22px;
    padding-bottom: 20px;
    border-bottom: 1px solid var(--app-border);
  }
  .feature-heading-desktop {
    min-width: 0;
  }
  .feature-heading-mobile {
    display: none;
    min-width: 0;
  }
  .feature-page-head h1,
  .feature-page-head :global(h1) {
    margin: 0;
    color: var(--app-text-strong);
    font-size: 22px;
    font-weight: 600;
    line-height: 1.3;
    letter-spacing: -0.025em;
  }
  .feature-page-head :global(h1 > span:last-child) {
    gap: 6px;
  }
  .feature-page-head :global(h1 a) {
    border-radius: 4px;
    font-size: 10px;
    height: 20px;
    padding-inline: 6px;
  }
  .feature-page-toolbar {
    flex-shrink: 0;
  }
  .feature-alerts {
    margin-bottom: 20px;
  }
  .feature-content {
    min-width: 0;
  }
  .feature-content--narrow {
    max-width: 820px;
  }
  @media (max-width: 767px) {
    .feature-heading-desktop {
      display: none;
    }
    .feature-heading-mobile {
      display: block;
    }
    .feature-page-head {
      gap: 12px;
      margin-bottom: 18px;
      padding-bottom: 16px;
      align-items: flex-start;
    }
    .feature-page-head h1,
    .feature-page-head :global(h1) {
      font-size: 20px;
    }
    .feature-back-link {
      margin-bottom: 14px;
    }
    .feature-page-toolbar {
      padding-top: 3px;
    }
  }
</style>
