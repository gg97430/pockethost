<script lang="ts">
  import type { PlatformDiagnostics } from '../../../../pockethost/src/common/platformDiagnostics'

  export let diagnostics: PlatformDiagnostics | null = null
  export let stale = false
  let expanded = false
  const states = {
    error: 'À traiter',
    warning: 'À vérifier',
    unknown: 'Sans mesure actuelle',
    ok: 'Répond',
    paused: 'En pause',
  }
  const categories = {
    resources: 'Capacité indisponible avant la construction',
    build: 'Commande de construction en échec',
    health: 'Nouvelle version refusée au contrôle de santé',
    superseded: 'Remplacé par un commit plus récent',
    interrupted: 'Traitement interrompu',
    other: 'Consulter le journal pour le détail',
  }
  const date = (value: string) => new Date(value).toLocaleString('fr-FR')
  $: outdated = stale || diagnostics?.stale
  $: visible = !outdated ? diagnostics?.items.slice(0, expanded ? 100 : 3) || [] : []
</script>

{#if diagnostics}
  <section class="web-panel diagnostics" aria-labelledby="diagnostics-heading">
    <div class="web-row diagnostics-heading">
      <div>
        <span class="web-eyebrow">Comprendre avant d’agir</span>
        <h2 id="diagnostics-heading">
          {outdated
            ? 'Mesures à actualiser'
            : diagnostics.items.length
              ? 'Points à examiner'
              : 'Aucun incident observé'}
        </h2>
        <p class="web-help">
          {outdated
            ? 'Le dernier relevé est ancien. L’état actuel des services reste à confirmer. Vérifiez le collecteur avant de conclure à une panne.'
            : 'Constats regroupés par dépendance ; les contrôles détaillés et les alertes restent inchangés.'}
        </p>
      </div>
      <span class="diagnostics-count"
        >{outdated ? '—' : diagnostics.items.length + diagnostics.omittedCount}<small>
          {outdated ? 'relevé périmé' : 'point(s)'}</small
        ></span
      >
    </div>
    {#if !diagnostics.dependencyInventoryAvailable || !diagnostics.eventInventoryAvailable}
      <p class="web-notice" role="status">
        Le diagnostic est partiel : {#if !diagnostics.dependencyInventoryAvailable}inventaire des liaisons privées
          indisponible{/if}{#if !diagnostics.dependencyInventoryAvailable && !diagnostics.eventInventoryAvailable}
          ;
        {/if}{#if !diagnostics.eventInventoryAvailable}événements de déploiement indisponibles{/if}. Les autres
        contrôles restent consultables.
      </p>
    {/if}
    {#if !outdated && !diagnostics.items.length}
      <p class="diagnostics-quiet">
        Les sondes de ce relevé n’indiquent aucun incident. Les parcours métier et les restaurations sont vérifiés
        séparément.
      </p>
    {/if}
    <div class="diagnostics-list">
      {#each visible as item (item.id)}
        <article class="diagnostic" data-state={item.state}>
          <div class="diagnostic-header">
            <h3>{item.title}</h3>
            <span class="diagnostic-state">{states[item.state]}</span>
          </div>
          <p class="diagnostic-explanation">{item.explanation}</p>
          {#if item.evidence.length}
            <details class="diagnostic-evidence">
              <summary>Constats observés <span>{item.evidence.length + item.additionalEvidence}</span></summary>
              <ul>
                {#each item.evidence as proof (proof.id)}
                  <li>
                    <div class="proof-heading">
                      {#if proof.href}<a href={proof.href}>{proof.name}</a>{:else}<strong>{proof.name}</strong
                        >{/if}<span data-state={proof.state}>{states[proof.state]}</span>
                    </div>
                    <p>{proof.detail}</p>
                    <time datetime={proof.checkedAt}>{date(proof.checkedAt)}</time>
                  </li>
                {/each}
              </ul>
              {#if item.additionalEvidence}<p class="web-help">
                  {item.additionalEvidence} autres mesures sont disponibles dans le tableau des contrôles.
                </p>{/if}
            </details>
          {/if}
          {#if item.hypotheses.length}
            <div class="diagnostic-hypothesis">
              <strong>Hypothèse à confirmer</strong>{#each item.hypotheses as text}<p>{text}</p>{/each}
            </div>
          {/if}
          <div class="diagnostic-action">
            <span>Prochaine vérification</span>
            <p>{item.nextAction}</p>
          </div>
        </article>
      {/each}
    </div>
    {#if !outdated && diagnostics.items.length > 3}
      <button class="web-button secondary" type="button" onclick={() => (expanded = !expanded)} aria-expanded={expanded}
        >{expanded ? 'Réduire les diagnostics' : `Voir les ${diagnostics.items.length} points`}</button
      >
    {/if}
    {#if !outdated && diagnostics.omittedCount}<p class="web-help">
        {diagnostics.omittedCount} points supplémentaires sont conservés dans le tableau des contrôles.
      </p>{/if}
    {#if !outdated && diagnostics.events.length}
      <details class="diagnostics-events">
        <summary
          >Tentatives de déploiement récentes <span>{diagnostics.events.length} dans la dernière heure</span></summary
        >
        <p class="web-help">Une tentative non publiée ne signifie pas que le site en service est arrêté.</p>
        <ol>
          {#each diagnostics.events as event (event.id)}
            <li>
              <time datetime={event.finishedAt}>{date(event.finishedAt)}</time>
              <div>
                <strong>{event.appName}</strong>
                <p>
                  {event.status === 'cancelled'
                    ? 'Tentative annulée'
                    : event.category === 'superseded'
                      ? 'Version remplacée'
                      : 'Tentative non publiée'} · {categories[event.category]}
                </p>
                <a href={`/applications/${event.appId}?tab=deployments`}>Ouvrir le déploiement · {event.id}</a>
              </div>
            </li>
          {/each}
        </ol>
      </details>
    {/if}
  </section>
{/if}

<style>
  .diagnostics {
    gap: 1.1rem;
    min-width: 0;
  }
  .diagnostics-heading {
    align-items: flex-start;
    gap: 1rem;
  }
  h2 {
    margin: 0.35rem 0 0.5rem;
  }
  .diagnostics-count {
    font-size: 1.8rem;
    line-height: 1.15;
    font-variant-numeric: tabular-nums;
    font-weight: 650;
    white-space: nowrap;
  }
  .diagnostics-count small {
    display: block;
    text-align: right;
    font-size: 0.7rem;
    font-weight: 400;
    color: var(--web-muted);
    margin-top: 0.3rem;
  }
  .diagnostics-quiet {
    font-size: 0.82rem;
    line-height: 1.6;
    color: var(--web-muted);
  }
  .diagnostics-list {
    display: grid;
    gap: 0.85rem;
    min-width: 0;
  }
  .diagnostic {
    padding: 1.1rem;
    border: 1px solid var(--web-border);
    border-left: 3px solid var(--web-muted);
    border-radius: var(--web-radius, 6px);
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .diagnostic[data-state='error'] {
    border-left-color: #ba5f51;
  }
  .diagnostic[data-state='warning'] {
    border-left-color: #b89051;
  }
  .diagnostic-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 0.7rem;
  }
  h3 {
    margin: 0;
    font-size: 0.94rem;
    line-height: 1.4;
  }
  .diagnostic-state {
    font-size: 0.68rem;
    line-height: 1.4;
    background: var(--web-surface-soft);
    border: 1px solid var(--web-border);
    padding: 0.2rem 0.45rem;
    border-radius: 4px;
    white-space: nowrap;
  }
  .diagnostic-explanation {
    font-size: 0.8rem;
    line-height: 1.65;
    margin: 0.65rem 0;
    color: var(--web-muted);
    max-width: 80rem;
  }
  .diagnostic-evidence {
    margin: 0.8rem 0;
  }
  summary {
    cursor: pointer;
    font-size: 0.78rem;
    line-height: 1.7;
  }
  summary span {
    font-size: 0.7rem;
    color: var(--web-muted);
    margin-left: 0.6rem;
  }
  ul {
    padding: 0;
    margin: 0.7rem 0;
    list-style: none;
    display: grid;
    gap: 0.6rem;
  }
  ul li {
    padding: 0.7rem 0.8rem;
    background: var(--web-surface-soft);
    border: 1px solid var(--web-border);
    border-radius: 4px;
  }
  .proof-heading {
    display: flex;
    justify-content: space-between;
    gap: 0.75rem;
    font-size: 0.76rem;
    line-height: 1.5;
  }
  .proof-heading a,
  .proof-heading strong {
    font-weight: 550;
  }
  .proof-heading span {
    font-size: 0.66rem;
    color: var(--web-muted);
    white-space: nowrap;
  }
  li p {
    font-size: 0.76rem;
    line-height: 1.5;
    margin: 0.25rem 0;
    color: var(--web-muted);
  }
  li time {
    font-size: 0.67rem;
    color: var(--web-muted);
  }
  .diagnostic-hypothesis {
    border: 1px dashed var(--web-border);
    background: var(--web-surface-soft);
    border-radius: 4px;
    padding: 0.75rem 0.85rem;
    margin: 0.8rem 0;
    font-size: 0.76rem;
    line-height: 1.65;
  }
  .diagnostic-hypothesis strong {
    font-size: 0.68rem;
    letter-spacing: 0.03em;
    text-transform: uppercase;
    font-weight: 550;
  }
  .diagnostic-hypothesis p {
    margin: 0.3rem 0 0;
    color: var(--web-muted);
  }
  .diagnostic-action {
    font-size: 0.76rem;
    line-height: 1.6;
    margin-top: 0.8rem;
  }
  .diagnostic-action span {
    font-weight: 600;
    font-size: 0.72rem;
  }
  .diagnostic-action p {
    margin: 0.2rem 0 0;
  }
  .diagnostics-events {
    border-top: 1px solid var(--web-border);
    padding-top: 1rem;
    min-width: 0;
  }
  .diagnostics-events ol {
    list-style: none;
    padding: 0;
    margin: 0.8rem 0 0;
    display: grid;
    gap: 0.75rem;
  }
  .diagnostics-events li {
    display: grid;
    grid-template-columns: 10rem minmax(0, 1fr);
    gap: 1rem;
    padding: 0.8rem 0;
    border-bottom: 1px solid var(--web-border);
    overflow-wrap: anywhere;
  }
  .diagnostics-events strong {
    font-size: 0.77rem;
    font-weight: 550;
  }
  .diagnostics-events a {
    font-size: 0.7rem;
  }
  @media (max-width: 600px) {
    .diagnostic {
      padding: 0.85rem;
    }
    .diagnostic-header {
      flex-wrap: wrap;
    }
    .proof-heading {
      flex-wrap: wrap;
    }
    .diagnostics-events li {
      grid-template-columns: 1fr;
      gap: 0.3rem;
    }
    .diagnostics-events summary span {
      display: block;
      margin-left: 0;
    }
    .diagnostics-count {
      font-size: 1.4rem;
    }
  }
</style>
