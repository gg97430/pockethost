<script lang="ts">
  import { onMount } from 'svelte'
  import FeatureTab from '$components/FeatureTab.svelte'
  import { client } from '$src/pocketbase-client'
  import { instance } from '../store'
  type Entry = { id: string; actor: string; action: string; created: string; details: Record<string, unknown> }
  let items: Entry[] = [],
    page = 1,
    hasMore = false,
    busy = false,
    error = ''
  const labels: Record<string, string> = {
    'instance.created': 'Instance créée',
    'instance.started': 'Instance démarrée',
    'instance.stopped': 'Instance arrêtée',
    'backup.restore': 'Restauration demandée',
    'backup.restore-new': 'Restauration vers une nouvelle instance',
    'transfer.cancel': 'Transfert annulé',
    'transfer.retry': 'Transfert relancé',
    'transfer.link': 'Lien de transfert renouvelé',
    'PUT instance': 'Configuration de l’instance',
    'DELETE instance': 'Suppression de l’instance',
    'POST backups': 'Opération sur une sauvegarde',
    'DELETE backups': 'Suppression d’une sauvegarde',
    'PUT monitoring': 'Configuration des alertes',
    'POST monitoring': 'Test des notifications',
    'POST transfers': 'Préparation d’un transfert',
    'service-account.create': 'Compte technique créé',
    'service-account.rotate': 'Mot de passe technique renouvelé',
    'service-account.revoke': 'Compte technique révoqué',
    'backup-check.queued': 'Vérification de sauvegarde demandée',
    'backup-check.passed': 'Sauvegarde vérifiée',
    'backup-check.failed': 'Vérification de sauvegarde échouée',
    'backup-check.policy': 'Planification des vérifications',
  }
  const load = async () => {
    busy = true
    error = ''
    try {
      const r = await client().client.send<any>(`/api/instance/${$instance.id}/actions`, {
        method: 'GET',
        query: { page },
      })
      items = r.items
      hasMore = r.hasMore
    } catch (e) {
      error = e instanceof Error ? client().parseError(e)[0] || e.message : String(e)
    } finally {
      busy = false
    }
  }
  onMount(() => void load())
</script>

<FeatureTab title="Journal d’administration">
  <div class="journal">
    <p>
      Historique des actions effectuées dans PocketHost depuis l’activation de cette fonction. Les mots de passe,
      secrets et liens de transfert ne sont pas enregistrés. Les actions effectuées directement dans la console native
      ne figurent pas dans ce journal.
    </p>
    <button on:click={load} disabled={busy}>Actualiser</button>
    {#if error}<p role="alert">{error}</p>{/if}
    <section aria-label="Actions d’administration">
      {#each items as entry}<article>
          <time>{new Date(entry.created).toLocaleString('fr-FR')}</time>
          <div>
            <strong>{labels[entry.action] || entry.action}</strong>
            <p>{entry.actor}</p>
            {#if Object.keys(entry.details).length}<small>{JSON.stringify(entry.details)}</small>{/if}
          </div>
        </article>{:else}<p>Aucune action enregistrée.</p>{/each}
    </section>
    <nav aria-label="Pagination du journal">
      <button
        disabled={busy || page === 1}
        on:click={() => {
          page--
          void load()
        }}>Précédent</button
      ><span>Page {page}</span><button
        disabled={busy || !hasMore}
        on:click={() => {
          page++
          void load()
        }}>Suivant</button
      >
    </nav>
  </div>
</FeatureTab>

<style>
  .journal {
    display: grid;
    gap: 1.5rem;
    max-width: 76rem;
  }
  section {
    border: 1px solid var(--wa-color-neutral-border-normal);
    border-radius: 12px;
    background: var(--wa-color-surface-default);
  }
  article {
    display: grid;
    grid-template-columns: 12rem 1fr;
    gap: 1.5rem;
    padding: 1.3rem;
    border-bottom: 1px solid var(--wa-color-neutral-border-normal);
  }
  time,
  small {
    opacity: 0.7;
    font-size: 0.85rem;
  }
  small {
    overflow-wrap: anywhere;
  }
  p {
    margin: 0.35rem 0;
  }
  button {
    padding: 0.55rem 0.85rem;
    justify-self: start;
    border: 1px solid var(--wa-color-neutral-border-normal);
    border-radius: 7px;
    background: var(--wa-color-surface-default);
    color: inherit;
    cursor: pointer;
  }
  button:disabled {
    opacity: 0.5;
  }
  nav {
    display: flex;
    gap: 1rem;
    align-items: center;
  }
  @media (max-width: 650px) {
    article {
      grid-template-columns: 1fr;
      gap: 0.5rem;
    }
  }
</style>
