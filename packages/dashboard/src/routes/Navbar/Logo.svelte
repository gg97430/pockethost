<script>
  export let hideLogoText = false
  export let className = ''
  export let large = false
</script>

<div class="brand-lockup {className}" class:large>
  <img class="brand-symbol" src="/images/kolab-hosting-mark.png" alt="" width="30" height="30" />
  <span class="brand-wordmark" class:sr-only={hideLogoText}
    ><strong>Kolab</strong> Hosting<span>GESTION & HÉBERGEMENT</span></span
  >
</div>

<style>
  .brand-lockup {
    display: flex;
    align-items: center;
    gap: 9px;
    min-width: 0;
  }
  .brand-symbol {
    display: block;
    flex: none;
    width: 30px;
    height: 30px;
    object-fit: contain;
  }
  /* Wordmark stays live text so it follows the light/dark theme. */
  .brand-wordmark {
    display: block;
    color: var(--app-text-strong);
    font-size: 15px;
    line-height: 1.1;
    font-weight: 400;
    letter-spacing: -0.02em;
    white-space: nowrap;
  }
  .brand-wordmark strong {
    font-weight: 800;
  }
  .large {
    gap: 12px;
  }
  .large .brand-symbol {
    width: 46px;
    height: 46px;
  }
  .large .brand-wordmark {
    font-size: 24px;
  }
  .large .brand-wordmark span {
    margin-top: 6px;
    font-size: 9px;
  }
  .brand-wordmark span {
    display: block;
    margin-top: 4px;
    color: var(--app-text-faint);
    font-size: 7px;
    font-weight: 500;
    letter-spacing: 0.1em;
  }
</style>
