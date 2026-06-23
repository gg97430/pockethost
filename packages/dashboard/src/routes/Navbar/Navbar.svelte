<script lang="ts">
  import Logo from '$src/routes/Navbar/Logo.svelte'
  import NavbarMenu from './NavbarMenu.svelte'
</script>

<div class="site-navbar max-w-content mx-auto flex items-center justify-between px-4 py-3 md:px-20">
  <div class="m-0 flex min-w-10 flex-1 items-center">
    <wa-dropdown class="navbar-mobile-dropdown md:hidden">
      <wa-button slot="trigger" appearance="plain" class="mx-0 px-0 lg:hidden">
        <Logo />
      </wa-button>
      <NavbarMenu isCollapsed />
    </wa-dropdown>
    <a href="/dashboard" class="hidden items-center md:flex">
      <Logo />
    </a>
  </div>
  <div class="flex flex-none items-center">
    <NavbarMenu />
  </div>
</div>

<style>
  :global(.navbar-mobile-dropdown) {
    display: contents;
  }

  @media (min-width: 768px) {
    :global(.navbar-mobile-dropdown) {
      display: none !important;
    }
  }
</style>
