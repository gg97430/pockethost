<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import FeatureTab from '$components/FeatureTab.svelte'
  import { client } from '$src/pocketbase-client'
  import { instance } from '../store'
  type Account = { id: string; label: string; email: string; lastAuthAt: string; revokedAt: string; created: string }
  let accounts: Account[] = [],
    label = '',
    busy = false,
    error = '',
    secret: { email: string; password: string } | null = null
  let selected: Account | null = null,
    action = ''
  const date = (v: string) => (v ? new Date(v).toLocaleString('fr-FR') : 'Jamais')
  const load = async () => {
    accounts = (await client().client.send<any>(`/api/instance/${$instance.id}/service-accounts`, { method: 'GET' }))
      .accounts
  }
  const execute = async (operation: string, account?: Account) => {
    busy = true
    error = ''
    secret = null
    try {
      const result = await client().client.send<any>(`/api/instance/${$instance.id}/service-accounts`, {
        method: 'POST',
        body: { action: operation, label, account: account?.id },
      })
      if (result.password) secret = { email: result.email, password: result.password }
      label = ''
      selected = null
      await load()
    } catch (e) {
      error = (e instanceof Error ? client().parseError(e)[0] || e.message : String(e)) || 'Opération impossible.'
    } finally {
      busy = false
    }
  }
  onMount(() => {
    void load().catch((e) => (error = e instanceof Error ? client().parseError(e)[0] || e.message : String(e)))
  })
  onDestroy(() => {
    secret = null
  })
</script>

<FeatureTab title="Comptes techniques">
  <div class="ops-page">
    <p>
      Des identifiants distincts pour les traitements serveur de vos applications. Les comptes humains et leur double
      authentification restent séparés.
    </p>
    <p class="warning">
      <strong>Accès superutilisateur complet.</strong> Ces comptes contournent les règles API PocketBase. Stockez leurs identifiants
      dans les variables secrètes de votre serveur applicatif. Pour un accès limité, utilisez une collection d’authentification
      et ses règles dans PocketBase.
    </p>
    {#if error}<p role="alert" class="error">{error}</p>{/if}
    {#if secret}<section class="credentials" aria-label="Nouveaux identifiants">
        <h3>À enregistrer maintenant</h3>
        <p>Le mot de passe ne sera plus affiché après fermeture ou rechargement de cette page.</p>
        <label>Identifiant<input readonly value={secret.email} autocomplete="off" /></label><label
          >Mot de passe<input
            readonly
            type="text"
            value={secret.password}
            autocomplete="off"
            spellcheck="false"
          /></label
        ><button on:click={() => (secret = null)}>Masquer les identifiants</button>
      </section>{/if}
    <section>
      <h3>Créer un compte</h3>
      <form on:submit|preventDefault={() => execute('create')}>
        <label
          >Application ou usage<input
            bind:value={label}
            maxlength="80"
            placeholder="GESTASSUR — serveur"
            required
          /></label
        ><button disabled={busy || !$instance.power}>Créer le compte technique</button>
      </form>
    </section>
    {#if selected}<section role="alert">
        <h3>{action === 'revoke' ? 'Révoquer' : 'Renouveler le mot de passe de'} « {selected.label} » ?</h3>
        <p>
          Les anciens jetons et identifiants cesseront de fonctionner. {action === 'rotate'
            ? 'Vous devrez mettre à jour la configuration de l’application.'
            : 'L’application perdra son accès à la base.'}
        </p>
        <button disabled={busy} on:click={() => execute(action, selected!)}>Confirmer</button><button
          on:click={() => (selected = null)}>Annuler</button
        >
      </section>{/if}
    <section>
      <h3>Accès des applications</h3>
      {#each accounts as account}<article>
          <div class="row">
            <strong>{account.label}</strong><small>{account.revokedAt ? 'Révoqué' : 'Actif'}</small>
          </div>
          <code>{account.email}</code>
          <p>Dernière authentification réussie : {date(account.lastAuthAt)}</p>
          <small>Ce champ ne suit pas chaque lecture API avec un jeton déjà valide.</small>{#if !account.revokedAt}<div
              class="row"
            >
              <button
                disabled={busy}
                on:click={() => {
                  secret = null
                  selected = account
                  action = 'rotate'
                }}>Renouveler le mot de passe</button
              ><button
                disabled={busy}
                on:click={() => {
                  secret = null
                  selected = account
                  action = 'revoke'
                }}>Révoquer</button
              >
            </div>{/if}
        </article>{:else}<p>Aucun compte technique géré par PocketHost.</p>{/each}
    </section>
  </div>
</FeatureTab>

<style>
  .ops-page {
    display: grid;
    gap: 1rem;
    max-width: 76rem;
  }
  section {
    padding: 1.5rem;
    border: 1px solid var(--wa-color-neutral-border-normal);
    border-radius: 12px;
    background: var(--wa-color-surface-default);
  }
  form,
  .row {
    display: flex;
    gap: 1rem;
    align-items: center;
    flex-wrap: wrap;
  }
  label {
    display: grid;
    gap: 0.5rem;
    flex: 1;
    min-width: 0;
  }
  input {
    padding: 0.65rem;
    border: 1px solid var(--wa-color-neutral-border-normal);
    border-radius: 6px;
    background: var(--wa-color-surface-default);
    color: inherit;
    width: 100%;
    box-sizing: border-box;
  }
  button {
    padding: 0.6rem 0.85rem;
    border: 1px solid var(--wa-color-neutral-border-normal);
    border-radius: 7px;
    background: var(--wa-color-surface-default);
    color: inherit;
    cursor: pointer;
  }
  button:disabled {
    opacity: 0.5;
  }
  article {
    padding: 1rem 0;
    border-bottom: 1px solid var(--wa-color-neutral-border-normal);
  }
  code {
    overflow-wrap: anywhere;
  }
  .credentials {
    border-color: #68a45b;
    display: grid;
    gap: 1rem;
  }
  .warning {
    border-left: 3px solid #c38a2c;
    padding: 1rem;
    background: #c38a2c0d;
  }
  .error {
    color: var(--wa-color-danger-on-quiet);
  }
  small {
    opacity: 0.75;
  }
  .row {
    margin: 0.7rem 0;
  }
</style>
