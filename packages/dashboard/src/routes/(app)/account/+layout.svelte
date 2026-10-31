<script lang="ts">
  import { page } from '$app/stores'
  import TabbedFeatureLayout from '$components/TabbedFeatureLayout.svelte'
  import type { FeatureTabNavSection } from '$lib/dashboard/featureTabTypes'

  $: pathname = $page.url.pathname
  $: isAccountActive = pathname === '/account' || pathname === '/account/'
  $: isKeysActive = pathname.startsWith('/account/keys')
  $: isSecurityActive = pathname.startsWith('/account/security')

  $: sections = [
    {
      items: [
        { href: '/account', label: 'Compte', icon: 'user', isActive: isAccountActive },
        { href: '/account/keys', label: 'Clés', icon: 'key', isActive: isKeysActive },
        { href: '/account/security', label: 'Sécurité', icon: 'shield-halved', isActive: isSecurityActive },
      ],
    },
  ] satisfies FeatureTabNavSection[]
</script>

<TabbedFeatureLayout title="Paramètres" {sections}>
  {#key pathname}
    <slot />
  {/key}
</TabbedFeatureLayout>
