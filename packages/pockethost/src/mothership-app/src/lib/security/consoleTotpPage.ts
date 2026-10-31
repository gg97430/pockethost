/** Standalone console sign-in, without external assets or third-party QR services. */
export const consoleTotpPage = `<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer"><title>Connexion PocketBase</title>
<style>
:root{color-scheme:dark;font-family:system-ui,sans-serif;background:#111;color:#f5f5f5}body{margin:0;padding:32px 20px}main{max-width:440px;margin:5vh auto}h1{font-size:24px;font-weight:550;margin:0 0 12px}p{line-height:1.5;color:#b8b8b8;font-size:14px}form,.stack{display:grid;gap:16px}label{display:grid;gap:8px;font-size:14px}input,textarea,button{font:inherit;box-sizing:border-box;min-height:44px;border:1px solid #444;border-radius:8px;padding:10px 12px;background:#202020;color:inherit;width:100%}input:focus,button:focus-visible,textarea:focus{outline:2px solid #1eb854;outline-offset:2px}button{cursor:pointer;background:#167d3a;border-color:#167d3a;font-weight:550}button.secondary{background:transparent;border-color:#444}button:disabled{opacity:.6;cursor:wait}a{color:#63d78a}section{margin-top:24px}.error{color:#ffb5b5}.success{color:#82e1a1}[hidden]{display:none!important}.qr{background:white;padding:12px;border-radius:8px;max-width:220px;width:100%}code{overflow-wrap:anywhere;font-size:14px}textarea{min-height:220px;font-family:monospace}.row{display:flex;gap:12px;flex-wrap:wrap}
</style></head><body><main>
<h1 id="title">Connexion PocketBase</h1>
<p id="intro">Connectez-vous à votre console d’administration.</p>
<p id="error" class="error" role="alert" hidden></p><p id="success" class="success" role="status" hidden></p>
<form id="login"><label>Email<input id="email" type="email" autocomplete="username" required></label>
<label>Mot de passe<input id="password" type="password" autocomplete="current-password" required></label>
<label id="factor-field" hidden>Code d’authentification ou code de secours<input id="factor" autocomplete="one-time-code" maxlength="80"></label>
<button>Se connecter</button></form>
<section id="security" class="stack" hidden>
<p id="status"></p>
<form id="settings"><label>Mot de passe actuel<input id="settings-password" type="password" autocomplete="current-password" required></label>
<label id="settings-factor-field" hidden>Nouveau code d’authentification ou code de secours<input id="settings-factor" autocomplete="one-time-code" maxlength="80"></label>
<div id="off-controls"><button id="setup-button" type="button">Configurer le TOTP</button></div>
<div id="on-controls" class="stack" hidden><button id="regenerate" type="button" class="secondary">Renouveler les codes de secours</button><button id="disable" type="button" class="secondary">Désactiver le TOTP</button></div></form>
<div id="setup" class="stack" hidden><p>Scannez ce QR code dans votre application d’authentification, puis saisissez le code à six chiffres.</p><img class="qr" id="qr" alt="QR code de configuration TOTP"><code id="secret"></code>
<form id="confirm"><label>Code à six chiffres<input id="confirmation-code" autocomplete="one-time-code" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" required></label><button>Activer le TOTP</button></form></div>
<div id="recovery" class="stack" hidden><p>Conservez ces codes de secours. Chaque code peut être utilisé une seule fois.</p><textarea id="codes" aria-label="Codes de secours" readonly></textarea><button type="button" id="download" class="secondary">Télécharger les codes</button></div>
<a href="/_/">Ouvrir la console PocketBase</a></section>
<p id="security-link"><a href="/_/ph-auth?security=1">Configurer l’authentification à deux facteurs</a></p>
</main><script>
(() => {
  const el = (id) => document.getElementById(id);
  const sso = __PH_CONSOLE_SSO_CONFIG__;
  const securityMode = new URLSearchParams(location.search).get('security') === '1';
  let token = '', record = null, codes = [], busy = false;
  const message = (id, value) => { el(id).textContent = value; el(id).hidden = !value; };
  const api = async (path, body) => {
    const response = await fetch(path, { method: body ? 'POST' : 'GET', headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: token } : {}) }, body: body ? JSON.stringify(body) : undefined });
    const data = await response.json();
    if (!response.ok) { if (data.data && data.data.totpRequired) el('factor-field').hidden = false; throw new Error(data.message || 'Opération impossible.'); }
    return data;
  };
  const saveToken = (value) => { token = value; const auth = JSON.stringify({token, record}); ['__pb_superuser_auth__','__pb_superusers__/_'].forEach(key => localStorage.setItem(key, auth)); };
  const run = async (fn) => { if (busy) return; busy = true; message('error',''); document.querySelectorAll('button').forEach(b => b.disabled = true); try { await fn(); } catch(e) { message('error',e.message); } finally { busy = false; document.querySelectorAll('button').forEach(b => b.disabled = false); } };
  const loadStatus = async () => {
    const state = await api('/api/ph-totp/status'); el('login').hidden = true; el('security').hidden = false;
    el('status').textContent = state.enabled ? 'TOTP activé · ' + state.recoveryCodesRemaining + ' codes de secours restants.' : 'Le TOTP n’est pas encore activé.';
    el('off-controls').hidden = state.enabled; el('on-controls').hidden = !state.enabled; el('settings-factor-field').hidden = !state.enabled;
  };
  const showCodes = (list) => { codes = list; el('codes').value = list.join('\\n'); el('recovery').hidden = false; };
  el('login').onsubmit = (event) => { event.preventDefault(); run(async () => {
    const auth = await api('/api/collections/_superusers/auth-with-password', {identity:el('email').value,password:el('password').value,totpCode:el('factor').value});
    record = auth.record; saveToken(auth.token); el('password').value = ''; el('factor').value = '';
    if (securityMode) await loadStatus(); else location.assign('/_/');
  }); };
  el('setup-button').onclick = () => run(async () => {
    if (!el('settings').reportValidity()) return;
    const setup = await api('/api/ph-totp/setup', {password:el('settings-password').value});
    el('qr').src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(setup.qrSvg); el('secret').textContent = setup.secret; el('setup').hidden = false; el('confirmation-code').focus();
  });
  el('confirm').onsubmit = (event) => { event.preventDefault(); run(async () => {
    const result = await api('/api/ph-totp/confirm', {password:el('settings-password').value,code:el('confirmation-code').value});
    saveToken(result.token); el('setup').hidden = true; el('secret').textContent = ''; el('qr').removeAttribute('src'); el('settings-password').value = ''; showCodes(result.recoveryCodes); await loadStatus(); message('success','Authentification à deux facteurs activée.');
  }); };
  el('disable').onclick = () => run(async () => {
    if (!el('settings').reportValidity()) return;
    const result = await api('/api/ph-totp/disable', {password:el('settings-password').value,code:el('settings-factor').value});
    saveToken(result.token); codes = []; el('recovery').hidden = true; el('codes').value = ''; el('settings-factor').value = ''; await loadStatus(); message('success','Authentification à deux facteurs désactivée.');
  });
  el('regenerate').onclick = () => run(async () => {
    if (!el('settings').reportValidity()) return;
    const result = await api('/api/ph-totp/recovery', {password:el('settings-password').value,code:el('settings-factor').value});
    el('settings-factor').value = ''; showCodes(result.recoveryCodes); await loadStatus();
  });
  el('download').onclick = () => { const url = URL.createObjectURL(new Blob([codes.join('\\n')+'\\n'],{type:'text/plain'})); const a=document.createElement('a');a.href=url;a.download='pocketbase-codes-de-secours.txt';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000); };
  if (securityMode) { el('title').textContent='Sécurité de la console';el('intro').textContent='Protégez votre compte administrateur avec une application d’authentification.';el('security-link').hidden=true; }
  const ticket = new URLSearchParams(location.hash.slice(1)).get('ticket');
  if (ticket) {
    history.replaceState(null, '', location.pathname + location.search);
    run(async () => {
      const auth = await api('/api/ph-console/login', {ticket});
      record = auth.record; saveToken(auth.token);
      if (securityMode) await loadStatus(); else location.replace('/_/');
    });
  } else if (sso && !new URLSearchParams(location.search).has('manual')) {
    el('login').hidden = true; el('intro').textContent = 'Vérification de votre session dashboard…';
    location.replace(sso.dashboard + '/console/' + sso.instance + (securityMode ? '?security=1' : ''));
  }
})();
</script></body></html>`
