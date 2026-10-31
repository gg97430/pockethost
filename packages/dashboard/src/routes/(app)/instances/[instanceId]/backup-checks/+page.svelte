<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import FeatureTab from '$components/FeatureTab.svelte'
  import { client } from '$src/pocketbase-client'
  import { instance } from '../store'
  type Check = {
    id: string
    backupId: string
    status: string
    created: string
    finishedAt: string
    result: {
      percent?: number
      phase?: string
      message?: string
      files?: number
      restoredBytes?: number
      checksumVerified?: boolean
    }
  }
  let checks: Check[] = [],
    enabled = false,
    intervalDays = 7,
    nextRunAt = '',
    page = 1,
    hasMore = false,
    busy = false,
    error = '',
    message = ''
  let timer: ReturnType<typeof setInterval>
  const date = (v: string) => (v ? new Date(v).toLocaleString('fr-FR') : '—')
  const labels: Record<string, string> = {
    queued: 'En attente',
    running: 'En cours',
    passed: 'Vérifiée',
    failed: 'Échec',
  }
  const phases: Record<string, string> = {
    checksum: 'Lecture et SHA-256',
    restore: 'Restauration temporaire',
    sqlite: 'Intégrité SQLite',
    complete: 'Terminée',
    failed: 'Échec',
  }
  const load = async (policy = false) => {
    try {
      const result = await client().client.send<any>(`/api/instance/${$instance.id}/backup-checks`, {
        method: 'GET',
        query: { page },
        requestKey: null,
      })
      checks = result.items
      hasMore = result.hasMore
      nextRunAt = result.policy.nextRunAt
      if (policy) {
        enabled = result.policy.enabled
        intervalDays = result.policy.intervalDays
      }
    } catch (e) {
      error = (e instanceof Error ? client().parseError(e)[0] || e.message : String(e)) || 'Chargement impossible.'
    }
  }
  const run = async () => {
    busy = true
    error = ''
    message = ''
    try {
      await client().client.send(`/api/instance/${$instance.id}/backup-checks`, { method: 'POST', body: {} })
      page = 1
      await load()
      message = 'Le contrôle de la dernière sauvegarde prête démarrera dans la file de traitement.'
    } catch (e) {
      error = e instanceof Error ? client().parseError(e)[0] || e.message : String(e)
    } finally {
      busy = false
    }
  }
  const save = async () => {
    busy = true
    error = ''
    try {
      await client().client.send(`/api/instance/${$instance.id}/backup-checks/policy`, {
        method: 'PUT',
        body: { enabled, intervalDays },
      })
      await load(true)
      message = 'Planification enregistrée.'
    } catch (e) {
      error = e instanceof Error ? client().parseError(e)[0] || e.message : String(e)
    } finally {
      busy = false
    }
  }
  onMount(() => {
    void load(true)
    timer = setInterval(() => {
      if (!document.hidden) void load()
    }, 5000)
  })
  onDestroy(() => clearInterval(timer))
</script>

<FeatureTab title="Vérification des sauvegardes">
  <div class="ops-page">
    <p>
      Chaque contrôle restaure une archive dans un dossier temporaire isolé, vérifie sa somme SHA-256 lorsqu’elle est
      disponible et lance le contrôle d’intégrité SQLite. Les hooks et les tâches ne sont pas exécutés. L’instance en
      service reste disponible.
    </p>
    <p class="note">
      Ce contrôle vérifie l’archive et la base restaurée. Il ne teste pas les parcours métier de votre application. Une
      seule vérification est traitée à la fois sur le serveur ; elle nécessite de l’espace libre pour décompresser la
      sauvegarde.
    </p>
    {#if error}<div role="alert" class="error">{error}</div>{/if}
    {#if message}<p role="status">{message}</p>{/if}
    <section>
      <h3>Contrôles automatiques</h3>
      <form on:submit|preventDefault={save}>
        <label><input type="checkbox" bind:checked={enabled} /> Activer les contrôles périodiques</label>
        <label>Tous les <input type="number" min="1" max="30" bind:value={intervalDays} required /> jours</label>
        <button disabled={busy}>Enregistrer</button>
      </form>
      <p>Prochain contrôle : {enabled ? date(nextRunAt) : 'Désactivé'}. La dernière sauvegarde prête sera utilisée.</p>
      <button
        type="button"
        on:click={run}
        disabled={busy || checks.some((c) => ['queued', 'running'].includes(c.status))}
        >Vérifier la dernière sauvegarde maintenant</button
      >
      <a href={`/instances/${$instance.id}/monitoring`}>Configurer les alertes en cas d’échec</a>
    </section>
    <section>
      <h3>Historique des contrôles</h3>
      {#each checks as check}<article>
          <div class="row">
            <strong class:failure={check.status === 'failed'}>{labels[check.status] || check.status}</strong><time
              >{date(check.created)}</time
            >
          </div>
          <small>Sauvegarde {check.backupId}</small>
          {#if check.status === 'running'}<p>
              {phases[check.result.phase || ''] || 'Préparation'} — {check.result.restoredBytes
                ? `${(check.result.restoredBytes / 1048576).toLocaleString('fr-FR', { maximumFractionDigits: 0 })} Mo restaurés`
                : ''}
            </p>
            <progress max="100" value={check.result.percent || 0} aria-label="Progression du contrôle"></progress>{/if}
          {#if check.result.message}<p>{check.result.message}</p>{/if}
          {#if check.status === 'passed'}<small
              >{check.result.files} fichiers restaurés · SHA-256 {check.result.checksumVerified
                ? 'conforme'
                : 'non enregistrée à la création'} · intégrité SQLite valide</small
            >{/if}
        </article>{:else}<p>Aucun contrôle exécuté.</p>{/each}
      <nav aria-label="Pagination des contrôles">
        <button
          disabled={page === 1}
          on:click={() => {
            page--
            void load()
          }}>Précédent</button
        ><span>Page {page}</span><button
          disabled={!hasMore}
          on:click={() => {
            page++
            void load()
          }}>Suivant</button
        >
      </nav>
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
  .row,
  nav {
    display: flex;
    gap: 1rem;
    align-items: center;
    flex-wrap: wrap;
  }
  form label {
    display: flex;
    gap: 0.5rem;
    align-items: center;
  }
  input[type='number'] {
    width: 5rem;
  }
  button {
    padding: 0.55rem 0.85rem;
    border: 1px solid var(--wa-color-neutral-border-normal);
    border-radius: 7px;
    cursor: pointer;
    background: var(--wa-color-surface-default);
    color: inherit;
  }
  button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  article {
    padding: 1.2rem 0;
    border-bottom: 1px solid var(--wa-color-neutral-border-normal);
  }
  .row {
    justify-content: space-between;
  }
  .note,
  small {
    opacity: 0.75;
  }
  .error,
  .failure {
    color: var(--wa-color-danger-on-quiet);
  }
  progress {
    width: 100%;
    accent-color: #67a658;
  }
  a {
    display: inline-block;
    margin: 0.7rem;
  }
  nav {
    margin-top: 1rem;
  }
</style>
