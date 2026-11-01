<script lang="ts">
  import { onMount } from 'svelte'
  import FeatureTab from '$components/FeatureTab.svelte'
  import { client } from '$src/pocketbase-client'
  import { instance } from '../store'

  type Check = {
    status?: string
    startedAt?: string
    finishedAt?: string
    restoredBytes?: number
    elapsedSeconds?: number
    error?: string
  }
  type Report = {
    checkedAt: string
    enabled: boolean
    sourceBytes: number
    positionState: string
    policy: {
      s3Bucket?: string
      replicaPath?: string
      syncInterval?: string
      monitorInterval?: string
      snapshotInterval?: string
      snapshotRetention?: string
      validationInterval?: string
    }
    service: { state: string; restarts?: number; since?: number; cpu?: number; memoryBytes?: number }
    local: { status?: string; local_txid?: string; wal_size?: string }
    metrics: Record<string, number>
    remote: {
      ok: boolean
      error?: string
      checkedAt?: string
      objects?: number
      bytes?: number
      partial?: boolean
      maxTxid?: string
      newestObjectAt?: string
      multipartAvailable?: boolean
      multipartPartial?: boolean
      uploads?: { bytes: number; totalBytes?: number; partial: boolean; startedAt: string }[]
    }
    verification: Check & {
      lastSuccess?: {
        finishedAt: string
        restoredBytes: number
        collections: number
        integrity: string
        seconds: number
      }
      history?: Check[]
    }
    history: { checkedAt: string; ok: boolean; bytes: number; objects: number; partial: boolean; maxTxid: string }[]
  }
  let report: Report | null = null
  let busy = false
  let starting = false
  let confirm = false
  let error = ''
  let auto = true
  let disposed = false
  const bytes = (n?: number) => {
    if (n == null) return 'Non disponible'
    const unit = n >= 1073741824 ? 1073741824 : n >= 1048576 ? 1048576 : 1024
    return `${(n / unit).toLocaleString('fr-FR', { maximumFractionDigits: 2 })} ${unit === 1073741824 ? 'Gio' : unit === 1048576 ? 'Mio' : 'Kio'}`
  }
  const date = (value?: string | number) => (value ? new Date(value).toLocaleString('fr-FR') : 'Pas encore contrôlé')
  const states: Record<string, string> = {
    passed: 'Réussie',
    failed: 'Échec',
    running: 'En cours',
    queued: 'En attente',
    interrupted: 'Interrompue',
  }
  $: checking = ['queued', 'running'].includes(report?.verification.status || '')
  $: localErrors = report?.metrics.sync_error_count
  $: position =
    report?.positionState === 'caught-up'
      ? 'Positions concordantes'
      : report?.positionState === 'pending'
        ? 'Envoi à confirmer'
        : 'Position distante à confirmer'
  $: problem =
    !!report?.enabled &&
    (report.service.state !== 'online' ||
      !report.remote.ok ||
      report.positionState === 'unknown' ||
      report.metrics.disk_full === 1)
  const refresh = async () => {
    if (busy || disposed) return
    busy = true
    try {
      const value = await client().client.send<Report>(`/api/instance/${$instance.id}/litestream/status`, {
        method: 'GET',
        requestKey: null,
      })
      if (!disposed) {
        report = value
        error = ''
      }
    } catch (e) {
      if (!disposed) error = e instanceof Error ? client().parseError(e)[0] || e.message : String(e)
    } finally {
      busy = false
    }
  }
  const verify = async () => {
    starting = true
    error = ''
    try {
      await client().client.send(`/api/instance/${$instance.id}/litestream/verify`, {
        method: 'POST',
        requestKey: null,
      })
      confirm = false
      await refresh()
    } catch (e) {
      error = e instanceof Error ? client().parseError(e)[0] || e.message : String(e)
    } finally {
      starting = false
    }
  }
  const download = () => {
    if (!report) return
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `litestream-${$instance.subdomain}.json`
    a.click()
    URL.revokeObjectURL(url)
  }
  onMount(() => {
    disposed = false
    void refresh()
    const timer = setInterval(() => {
      if (auto && !document.hidden) void refresh()
    }, 15000)
    return () => {
      disposed = true
      clearInterval(timer)
    }
  })
</script>

<svelte:head><title>Litestream - Kolab Hosting</title></svelte:head>
<FeatureTab title="Contrôle Litestream" errorMessage={error}>
  <svelte:fragment slot="summary"
    ><p>
      Suivez la réplication de la base et vérifiez qu’une restauration depuis le stockage distant fonctionne.
    </p></svelte:fragment
  >
  <div class="ls-dashboard">
    <div class="toolbar">
      <label><input type="checkbox" bind:checked={auto} /> Actualisation automatique · 15 s</label>
      <div class="buttons">
        <button class="button" onclick={() => refresh()} disabled={busy}>{busy ? 'Contrôle…' : 'Actualiser'}</button>
        <button class="button" onclick={download} disabled={!report}>Exporter le rapport</button>
        <a class="button" href={`/instances/${$instance.id}/backups#litestream`}>Configurer Litestream</a>
      </div>
    </div>
    {#if !report}
      <p role="status">Chargement des contrôles…</p>
    {:else}
      <div class:warning={problem || !report.enabled} class="headline" role="status">
        <strong
          >{!report.enabled
            ? 'Réplication désactivée'
            : problem
              ? 'Un contrôle demande votre attention'
              : position}</strong
        >
        <span>Contrôle local : {date(report.checkedAt)} · S3 : {date(report.remote.checkedAt)}</span>
      </div>
      {#if !report.enabled}
        <p>
          Configurez le bucket et activez Litestream dans les sauvegardes pour commencer la réplication de cette
          instance.
        </p>
      {:else}
        <div class="cards">
          <article class="card">
            <span class="eyebrow">Service de réplication</span><strong
              >{report.service.state === 'online' ? 'En ligne' : 'Indisponible'}</strong
            >
            <p>Service partagé du serveur · {report.service.restarts ?? '—'} redémarrage(s)</p>
            <small>CPU : {report.service.cpu ?? '—'} % · RAM : {bytes(report.service.memoryBytes)}</small>
          </article>
          <article class="card">
            <span class="eyebrow">Base source</span><strong>{bytes(report.sourceBytes)}</strong>
            <p>Journal WAL : {report.local.wal_size || bytes(report.metrics.wal_size)}</p>
            <small>Lecture locale : {report.local.status || 'inconnue'}</small>
          </article>
          <article class="card">
            <span class="eyebrow">Stockage distant</span><strong
              >{report.remote.ok ? bytes(report.remote.bytes) : 'À vérifier'}</strong
            >
            <p>{report.remote.objects ?? '—'} objets{report.remote.partial ? ' · inventaire partiel' : ''}</p>
            <small>Volume compressé, comprenant l’historique conservé</small>
          </article>
          <article class="card">
            <span class="eyebrow">Dernière restauration réussie</span><strong
              >{report.verification.lastSuccess ? 'Intégrité vérifiée' : 'Non vérifiée'}</strong
            >
            <p>{date(report.verification.lastSuccess?.finishedAt)}</p>
            <small
              >{report.verification.lastSuccess
                ? `${bytes(report.verification.lastSuccess.restoredBytes)} · ${report.verification.lastSuccess.collections} collections`
                : 'Un service en ligne ne garantit pas une restauration.'}</small
            >
          </article>
        </div>
        {#if report.remote.error}<div class="notice warning" role="alert">{report.remote.error}</div>{/if}
        {#if report.metrics.disk_full === 1}<div class="notice warning" role="alert">
            Litestream signale un disque plein. La réplication peut être interrompue.
          </div>{/if}
        <section class="panel">
          <div class="section-heading">
            <h3>Synchronisation et envois</h3>
            <span class="tag">{position}</span>
          </div>
          <div class="positions">
            <div><span>Position locale</span><code>{report.local.local_txid || 'Inconnue'}</code></div>
            <div><span>Position S3 observée</span><code>{report.remote.maxTxid || 'Inconnue'}</code></div>
          </div>
          <p>
            Ces positions identifient les lots Litestream. Une base sans écriture peut conserver un ancien horodatage
            sans être en retard. Un inventaire partiel ne permet pas de conclure à une synchronisation complète.
          </p>
          {#each report.remote.uploads || [] as upload}
            <div class="upload">
              <div class="section-heading">
                <strong>Envoi multipart en cours</strong><span
                  >{bytes(upload.bytes)}{upload.totalBytes ? ` / ${bytes(upload.totalBytes)}` : ' envoyés'}</span
                >
              </div>
              {#if upload.totalBytes && !upload.partial}<progress
                  max={upload.totalBytes}
                  value={Math.min(upload.bytes, upload.totalBytes)}
                  aria-label="Progression de l’envoi S3"
                ></progress>{:else}<progress aria-label="Envoi S3 en cours, taille totale inconnue"></progress>{/if}
              <small>Début : {date(upload.startedAt)}{upload.partial ? ' · parties comptées partiellement' : ''}</small>
            </div>
          {:else}<p>
              {report.remote.multipartAvailable
                ? 'Aucun envoi multipart observé lors du dernier contrôle.'
                : 'Progression multipart indisponible : les droits de consultation peuvent manquer.'}
            </p>{/each}
          <div class="facts">
            <div><span>Dernier objet modifié sur S3</span><strong>{date(report.remote.newestObjectAt)}</strong></div>
            <div>
              <span>Erreurs locales cumulées depuis le démarrage</span><strong
                >{localErrors == null ? 'Métriques indisponibles' : localErrors.toLocaleString('fr-FR')}</strong
              >
            </div>
            <div>
              <span>Erreurs de vérification de compaction</span><strong
                >{report.metrics.compaction_verify_error_count ?? 'Non disponible'}</strong
              >
            </div>
          </div>
          <p class="muted">
            Les compteurs peuvent inclure des erreurs déjà résolues. Ils repartent de zéro au redémarrage. Les erreurs
            locales ne couvrent pas toutes les erreurs d’envoi S3.
          </p>
        </section>
        <section class="panel">
          <div class="section-heading">
            <h3>Vérifier une restauration</h3>
            <button class="button primary" onclick={() => (confirm = true)} disabled={checking || starting}
              >{checking ? 'Vérification en cours' : 'Tester la restauration S3'}</button
            >
          </div>
          <p>
            Reconstruit une copie temporaire de la base depuis S3, puis vérifie toute son intégrité SQLite. L’instance
            continue de fonctionner. Les fichiers téléversés et les hooks sont couverts par vos sauvegardes complètes
            séparées.
          </p>
          {#if confirm}<div class="notice">
              <p>
                Ce test télécharge la base et utilise du disque et des ressources serveur. Taille actuelle : <strong
                  >{bytes(report.sourceBytes)}</strong
                >. Il faut au moins 2,5 fois cette taille plus 1 Gio d’espace libre. La copie temporaire est supprimée à
                la fin. Une seule vérification Litestream s’exécute à la fois sur le serveur.
              </p>
              <div class="buttons">
                <button class="button primary" onclick={verify} disabled={starting}
                  >{starting ? 'Démarrage…' : 'Lancer le contrôle'}</button
                ><button class="button" onclick={() => (confirm = false)} disabled={starting}>Annuler</button>
              </div>
            </div>{/if}
          {#if report.verification.status}<div class="upload" aria-live="polite">
              <div class="section-heading">
                <strong>{states[report.verification.status] || report.verification.status}</strong><span
                  >{bytes(report.verification.restoredBytes)} · {report.verification.elapsedSeconds ?? 0} s</span
                >
              </div>
              {#if checking}<progress aria-label="Restauration et contrôle d’intégrité en cours"></progress>
                <p>
                  Les octets reconstruits ne mesurent pas l’avancement du contrôle d’intégrité. Le résultat reste en
                  attente jusqu’à la fin des deux étapes.
                </p>{/if}{#if report.verification.error}<p role="alert">{report.verification.error}</p>{/if}
            </div>{/if}
          {#if report.verification.history?.length}<div class="table-scroll">
              <table>
                <caption>Dernières vérifications</caption><thead
                  ><tr><th>Début</th><th>Résultat</th><th>Base restaurée</th><th>Durée</th></tr></thead
                ><tbody
                  >{#each report.verification.history as check}<tr
                      ><td>{date(check.startedAt)}</td><td>{states[check.status || ''] || check.status}</td><td
                        >{bytes(check.restoredBytes)}</td
                      ><td>{check.elapsedSeconds ?? '—'} s</td></tr
                    >{/each}</tbody
                >
              </table>
            </div>{/if}
        </section>
        <section class="panel">
          <h3>Destination et fréquence</h3>
          <div class="facts">
            <div><span>Bucket</span><strong>{report.policy.s3Bucket || 'Non configuré'}</strong></div>
            <div>
              <span>Synchronisation / surveillance</span><strong
                >{report.policy.syncInterval} / {report.policy.monitorInterval}</strong
              >
            </div>
            <div>
              <span>Snapshot / rétention</span><strong
                >{report.policy.snapshotInterval} / {report.policy.snapshotRetention}</strong
              >
            </div>
            <div><span>Validation Litestream</span><strong>{report.policy.validationInterval}</strong></div>
          </div>
          <code class="path">{report.policy.replicaPath}</code>
          <p>
            La fréquence de synchronisation est une cible, pas une garantie de perte maximale en cas de panne. Le
            contrôle S3 est mutualisé pendant 30 secondes. L’historique ci-dessous se constitue quand cette page est
            consultée.
          </p>
        </section>
        <section class="panel">
          <h3>Historique des contrôles S3</h3>
          <div class="table-scroll">
            <table>
              <thead
                ><tr
                  ><th>Contrôle</th><th>Accès S3</th><th>Volume distant</th><th>Objets</th><th>Position observée</th
                  ></tr
                ></thead
              ><tbody
                >{#each (report.history || []).slice(-10).reverse() as sample}<tr
                    ><td>{date(sample.checkedAt)}</td><td
                      >{sample.ok ? (sample.partial ? 'Inventaire partiel' : 'Accessible') : 'Échec'}</td
                    ><td>{bytes(sample.bytes)}</td><td>{sample.objects}</td><td><code>{sample.maxTxid || '—'}</code></td
                    ></tr
                  >{/each}</tbody
              >
            </table>
          </div>
        </section>
      {/if}
    {/if}
  </div>
</FeatureTab>

<style>
  .ls-dashboard {
    display: grid;
    gap: 1.25rem;
    min-width: 0;
  }
  .toolbar,
  .buttons,
  .section-heading {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.65rem;
    justify-content: space-between;
  }
  .toolbar label {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.85rem;
  }
  .button {
    border: 1px solid var(--wa-color-neutral-border-normal, #d6dcd5);
    border-radius: 0.5rem;
    padding: 0.65rem 0.9rem;
    font: inherit;
    font-size: 0.85rem;
    cursor: pointer;
    color: inherit;
    text-decoration: none;
    background: var(--wa-color-surface-default, white);
  }
  .button:hover {
    border-color: #68a25d;
  }
  .button:disabled {
    opacity: 0.55;
    cursor: wait;
  }
  .button:focus-visible {
    outline: 2px solid #4e9940;
    outline-offset: 3px;
  }
  .primary {
    background: #397c35;
    border-color: #397c35;
    color: white;
  }
  .headline {
    display: grid;
    gap: 0.4rem;
    padding: 1rem 1.2rem;
    border: 1px solid #9ec991;
    border-radius: 0.75rem;
    background: #6aaa4b12;
  }
  .headline span,
  small,
  .muted {
    font-size: 0.8rem;
    opacity: 0.75;
  }
  .warning {
    border-color: #d6a63a;
    background: #c79a2610;
  }
  .cards {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 1rem;
  }
  .card,
  .panel {
    border: 1px solid var(--wa-color-neutral-border-normal, #dde2db);
    background: var(--wa-color-surface-default, white);
    border-radius: 0.8rem;
    padding: 1.2rem;
    min-width: 0;
  }
  .card {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
  }
  .card > strong {
    font-size: 1.3rem;
    letter-spacing: -0.025em;
  }
  .eyebrow {
    font-size: 0.8rem;
    opacity: 0.75;
  }
  .card p {
    margin: 0;
  }
  p {
    line-height: 1.6;
    font-size: 0.88rem;
  }
  h3 {
    font-size: 1rem;
    font-weight: 650;
    margin: 0;
  }
  .tag {
    font-size: 0.78rem;
    border: 1px solid #a1bd99;
    border-radius: 2rem;
    padding: 0.3rem 0.65rem;
  }
  .positions,
  .facts {
    display: flex;
    gap: 1.5rem;
    flex-wrap: wrap;
    margin: 1rem 0;
  }
  .positions > div,
  .facts > div {
    display: grid;
    gap: 0.4rem;
    min-width: 0;
  }
  .facts span,
  .positions span {
    font-size: 0.8rem;
    opacity: 0.75;
  }
  .facts strong {
    font-size: 0.88rem;
    overflow-wrap: anywhere;
  }
  code {
    font-size: 0.8rem;
    overflow-wrap: anywhere;
  }
  .path {
    display: block;
    padding: 0.6rem;
    background: #7d91700c;
    border-radius: 0.3rem;
  }
  .notice,
  .upload {
    border: 1px solid #b6c4af;
    padding: 1rem;
    border-radius: 0.6rem;
    margin: 0.8rem 0;
  }
  progress {
    display: block;
    width: 100%;
    height: 0.7rem;
    margin: 1rem 0;
    accent-color: #519d43;
  }
  .table-scroll {
    overflow-x: auto;
    max-width: 100%;
    margin-top: 1rem;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.8rem;
    text-align: left;
  }
  th,
  td {
    padding: 0.75rem 0.6rem;
    border-bottom: 1px solid #8c9a822a;
    white-space: nowrap;
  }
  caption {
    text-align: left;
    margin-bottom: 0.7rem;
    font-weight: 600;
  }
  @media (max-width: 1100px) {
    .cards {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }
  @media (max-width: 600px) {
    .cards {
      grid-template-columns: 1fr;
    }
    .toolbar {
      align-items: start;
    }
    .section-heading {
      align-items: start;
    }
  }
</style>
