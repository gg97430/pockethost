<script lang="ts">
  import { page } from '$app/stores'
  import TabbedFeatureLayout from '$components/TabbedFeatureLayout.svelte'
  import type { FeatureTabNavSection } from '$lib/dashboard/featureTabTypes'

  $: pathname = $page.url.pathname
  $: isAccountActive = pathname === '/account' || pathname === '/account/'
  $: isKeysActive = pathname.startsWith('/account/keys')

  $: sections = [
    {
      items: [
        { href: '/account', label: 'Compte', icon: 'user', isActive: isAccountActive },
        { href: '/account/keys', label: 'Clés', icon: 'key', isActive: isKeysActive },
      ],
    },
  ] satisfies FeatureTabNavSection[]
</script>

<TabbedFeatureLayout title="Paramètres" {sections}>
  {#key pathname}
    <slot />
  {/key}
</TabbedFeatureLayout>
