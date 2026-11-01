<script lang="ts">
  import { webError } from '$lib/webApps'
  import type { WebCapabilityMap } from '$lib/webAccessView'
  import { webAppMove, type WebGroupedApp, type WebGroupedApps } from '$lib/webProjectGroupsApi'
  import { WEB_ENVIRONMENT_LABELS, webMoveDefaultSlug, webMoveSummary, webMoveTargets } from '$lib/webProjectGroupsView'
  import type { WebPermanentEnvironment } from '../../../../pockethost/src/common/webProjectGroups'
  export let app: WebGroupedApp
  export let groups: WebGroupedApps['groups'] = []
  export let globalCapabilities: Partial<WebCapabilityMap> = {}
  /** Nom du projet actuel ('' pour une application sans projet). */
  export let fromName = ''
  export let oncancel: () => void = () => {}
  /** Appelé après un déplacement réussi, avec le message de succès à afficher. */
  export let onmoved: (summary: string) => void | Promise<void> = () => {}

  const NONE = '__none__'
  const environments = Object.keys(WEB_ENVIRONMENT_LABELS) as WebPermanentEnvironment[]
  let busy = false,
    error = '',
    targetKey = '',
    slug = '',
    slugTouched = false,
    slugFor: string | null = null,
    copySharedVariables = false,
    environment: WebPermanentEnvironment =
      app.membership && app.membership.environment !== 'preview' ? app.membership.environment : 'prod'
  $: targets = webMoveTargets(groups, app, globalCapabilities)
  $: if (!targetKey && targets.length) targetKey = targets[0]!.projectId ?? NONE
  $: targetId = targetKey === NONE ? null : targetKey
  $: target = targetId ? groups.find((group) => group.project.id === targetId) || null : null
  $: toName = target?.project.name || ''
  $: moving = !!fromName && !!targetId
  // Nouvelle destination : nom court reproposé (le serveur applique la même règle si le champ n'est pas modifié).
  $: if (targetKey && slugFor !== targetKey) {
    slugFor = targetKey
    slug = webMoveDefaultSlug(app, target)
    slugTouched = false
  }

  const submit = async (event: SubmitEvent) => {
    event.preventDefault()
    if (busy || !targetKey) return
    busy = true
    error = ''
    try {
      const result = await webAppMove(
        app.id,
        targetId
          ? {
              projectId: targetId,
              environment,
              ...(slugTouched && slug.trim() ? { slug: slug.trim() } : {}),
              ...(moving ? { copySharedVariables } : {}),
            }
          : { projectId: null }
      )
      // Le serveur voit aussi les previews fermées : son nom court peut différer de celui proposé ici.
      const given = result.app.membership?.slug || ''
      await onmoved(
        [
          webMoveSummary(result, fromName, targetId ? toName : null),
          ...(targetId && given && given !== slug.trim() ? [`Nom court attribué : « ${given} ».`] : []),
        ].join(' ')
      )
    } catch (e) {
      error = webError(e)
    } finally {
      busy = false
    }
  }
</script>

<div class="web-confirm" role="region" aria-label={`Déplacer ${app.name}`}>
  <form class="web-form" onsubmit={submit}>
    <div class="web-form-grid">
      <label class="web-field"
        ><span>Destination</span><select
          bind:value={targetKey}
          aria-label={`Projet de destination de ${app.name}`}
          disabled={busy}
          >{#each targets as item (item.projectId ?? NONE)}<option value={item.projectId ?? NONE}>{item.label}</option
            >{/each}</select
        ></label
      >
      {#if targetId}<label class="web-field"
          ><span>Nom court dans le projet</span><input
            bind:value={slug}
            oninput={() => (slugTouched = true)}
            required
            pattern={'[a-z0-9][a-z0-9\\-]{0,59}'}
            maxlength="60"
            disabled={busy}
            autocomplete="off"
            spellcheck="false"
          /><span class="web-help">Lettres minuscules, chiffres et tirets.</span></label
        ><label class="web-field"
          ><span>Environnement</span><select bind:value={environment} disabled={busy}
            >{#each environments as value}<option {value}>{WEB_ENVIRONMENT_LABELS[value]}</option>{/each}</select
          ></label
        >{/if}
    </div>
    {#if moving}<label class="web-check"
        ><input type="checkbox" bind:checked={copySharedVariables} disabled={busy} /> Copier dans ce site les variables
        partagées de « {fromName} »</label
      >
      <p class="web-help">
        Sinon, ces variables ne s’appliqueront plus à ce site. Ses variables locales sont conservées.
      </p>{/if}
    {#if moving}<p>
        Les membres de « {fromName} » perdront l’accès à ce site ; ceux de « {toName} » l’obtiendront. Les versions publiées
        dans « {fromName} » ne pourront plus être rejouées.
      </p>
    {:else if !targetId && targetKey}<p>
        Seuls les administrateurs de la plateforme verront ce site. Ses variables résolues seront conservées localement.
      </p>
    {:else if targetId}<p>Les membres de « {toName} » obtiendront l’accès à ce site.</p>{/if}
    {#if error}<div class="web-alert web-alert--error" role="alert">{error}</div>{/if}
    <div class="web-actions">
      <button class="web-button web-button--primary" type="submit" disabled={busy || !targetKey}
        >{busy ? 'Déplacement…' : targetId ? 'Confirmer le déplacement' : 'Confirmer le retrait du projet'}</button
      ><button class="web-button" type="button" onclick={oncancel} disabled={busy}>Annuler</button>
    </div>
  </form>
</div>
