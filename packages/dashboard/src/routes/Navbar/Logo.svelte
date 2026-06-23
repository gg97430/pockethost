<script>
  export let hideLogoText = false
  export let className = ''
</script>

<div class="flex min-w-0 items-center justify-center {className}">
  <span
    class="brand-wordmark truncate text-sm font-black uppercase tracking-[0.12em] text-white md:text-base {hideLogoText
      ? 'sr-only'
      : ''}"
  >
    GESTION POCKETBASE
  </span>
</div>

<style>
  .brand-wordmark {
    letter-spacing: 0.12em;
  }
</style>
