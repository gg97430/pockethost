<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import { webRequest, webError } from '$lib/webApps'
  import {
    environmentRowsText,
    environmentAccessDenied,
    environmentDraftRows,
    environmentRowsPayload,
    environmentRowsFromText,
    mergeRevealedEnvironmentRows,
    publicEnvironmentResponse,
    localEnvironmentImport,
    nextLocalEnvironment,
    type EnvironmentRow,
    type EnvironmentDraftRow,
    type EnvironmentResponse,
    type EnvironmentPreview,
  } from '$lib/webEnvironment'
  import { WEB_NO_CAPABILITIES, type WebCapabilityMap } from '$lib/webAccessView'

  export let rows: EnvironmentRow[] = []
  export let disabled = false
  export let appId = ''
  export let refreshKey = ''
  export let capabilities: WebCapabilityMap = WEB_NO_CAPABILITIES
  export let onchanged: (value: EnvironmentResponse) => void | Promise<void> = () => {}

  type Candidate = {
    revision: string
    mode: 'merge' | 'replace'
    text?: string
    rows?: ReturnType<typeof environmentRowsPayload>
  }
  let drafts: EnvironmentDraftRow[] = rows.map((row) => ({ ...row }))
  let parentRows = rows
  let view: 'normal' | 'developer' = 'normal'
  let mode: 'merge' | 'replace' = 'replace'
  let text = ''
  let rawEditor: HTMLTextAreaElement | undefined
  let createTextError = ''
  let textMatchesRows = false
  let data: EnvironmentResponse | null = null
  let preview: EnvironmentPreview | null = null
  let pending: Candidate | null = null
  let localPreview: ReturnType<typeof localEnvironmentImport> | null = null
  let busy = ''
  let error = ''
  let message = ''
  let dirty = false
  let valuesVisible = false
  let accessLost = false
  let mounted = false
  let loadedFor = ''
  let loadedRefresh = ''
  let generation = 0

  $: revealed = data?.revealed === true
  $: canReveal = !appId || capabilities['variables.reveal']
  $: readOnly = !!appId && (!capabilities['variables.write'] || accessLost)
  $: blocked = disabled || !!busy
  $: nextExport = data ? nextLocalEnvironment(data) : null
  $: canExport = !!data && revealed && !nextExport?.warnings.length && !dirty
  $: changes = preview?.changes || localPreview?.changes
  $: if (mounted && appId && loadedFor !== appId) {
    loadedFor = appId
    loadedRefresh = refreshKey
    valuesVisible = false
    accessLost = false
    view = 'normal'
    clearSecrets()
    void load()
  }
  $: if (mounted && appId && loadedFor === appId && loadedRefresh !== refreshKey && !dirty && !busy) {
    loadedRefresh = refreshKey
    void load()
  }
  $: if (!appId && rows !== parentRows && !dirty && !busy) {
    parentRows = rows
    drafts = rows.map((row) => ({ ...row }))
  }
  $: if (mounted && appId && !canReveal && valuesVisible) hide()

  const clearPreview = () => {
    preview = null
    pending = null
    localPreview = null
  }
  const clearSecrets = () => {
    data = null
    drafts = []
    text = ''
    createTextError = ''
    rawEditor?.setCustomValidity('')
    textMatchesRows = false
    clearPreview()
  }
  const serverFailure = (problem: unknown) => {
    if (environmentAccessDenied(problem)) {
      generation++
      valuesVisible = false
      accessLost = true
      view = 'normal'
      dirty = false
      clearSecrets()
      rows = []
      parentRows = rows
      busy = ''
      message = ''
      loadedRefresh = refreshKey
      error = 'L’accès aux variables a été refusé. Les valeurs et les brouillons ont été effacés de cet éditeur.'
      return true
    }
    error = webError(problem)
    return false
  }
  const publish = () => {
    // Existing apps expose only metadata to the parent, even while the local editor is revealed.
    if (appId) {
      rows = (data ? publicEnvironmentResponse(data).variables : rows).map((row) => ({ ...row, value: '' }))
    } else rows = drafts.map(({ valueKnown, ...row }) => ({ ...row }))
    parentRows = rows
  }
  const syncCreationText = () => {
    try {
      const imported = localEnvironmentImport(text, drafts, mode)
      drafts = imported.rows.map((row) => ({ ...row, valueKnown: true }))
      createTextError = ''
      rawEditor?.setCustomValidity('')
      publish()
    } catch (problem) {
      createTextError = webError(problem)
      rawEditor?.setCustomValidity(createTextError)
    }
  }
  const changed = (source: 'rows' | 'text' = 'rows') => {
    clearPreview()
    message = ''
    dirty = true
    if (source === 'rows') textMatchesRows = false
    if (!appId) {
      if (source === 'text') syncCreationText()
      else publish()
    }
  }
  const update = (index: number, patch: Partial<EnvironmentDraftRow>) => {
    drafts = drafts.map((row, i) => (i === index ? { ...row, ...patch } : row))
    changed()
  }
  const editValue = (index: number, value: string) => {
    const valueKnown = revealed || !appId || !!drafts[index]?.valueKnown
    update(index, { value, valueKnown, setEmpty: value === '' && valueKnown })
  }
  const compactText = (current = drafts) => environmentRowsText(current, { compact: true, baseline: data?.variables })
  const adopt = (value: EnvironmentResponse) => {
    accessLost = false
    data = value
    drafts = environmentDraftRows(value)
    valuesVisible = value.revealed
    dirty = false
    clearPreview()
    text = ''
    textMatchesRows = false
    if (view === 'developer' && value.revealed) {
      try {
        text = compactText()
        textMatchesRows = true
      } catch (problem) {
        error = webError(problem)
        view = 'normal'
      }
    }
    publish()
  }
  const notify = (value: EnvironmentResponse) => {
    const safe = publicEnvironmentResponse(value)
    void Promise.resolve()
      .then(() => onchanged(safe))
      .catch(() => {})
  }
  const load = async () => {
    const request = ++generation,
      id = appId
    const show = valuesVisible && canReveal
    error = ''
    message = ''
    clearPreview()
    busy = 'load'
    try {
      const value = await webRequest<EnvironmentResponse>(
        `/${id}/environment${show ? '/reveal' : ''}`,
        show ? { method: 'POST' } : {}
      )
      if (!mounted || request !== generation || id !== appId) return
      adopt(value)
      loadedRefresh = refreshKey
      notify(value)
    } catch (problem) {
      if (mounted && request === generation) serverFailure(problem)
    } finally {
      if (mounted && request === generation) busy = ''
    }
  }
  const revealValues = async () => {
    if (!appId || !canReveal || !data) return !appId
    const request = generation,
      id = appId,
      revision = data.revision
    busy = 'reveal'
    error = ''
    try {
      const value = await webRequest<EnvironmentResponse>(`/${id}/environment/reveal`, { method: 'POST' })
      if (!mounted || request !== generation || id !== appId) return false
      if (dirty && revision !== value.revision)
        throw new Error(
          'Les variables enregistrées ont changé. Vos modifications sont conservées ; rechargez avant de continuer.'
        )
      drafts = dirty ? mergeRevealedEnvironmentRows(drafts, value) : environmentDraftRows(value)
      data = value
      valuesVisible = true
      publish()
      return true
    } catch (problem) {
      if (mounted && request === generation) serverFailure(problem)
      return false
    } finally {
      if (mounted && request === generation) busy = ''
    }
  }
  const chooseView = async (next: 'normal' | 'developer') => {
    if (view === next || blocked) return
    error = ''
    if (next === 'developer') {
      if (appId && (!canReveal || (!revealed && !(await revealValues())))) return
      try {
        if (!textMatchesRows) text = compactText()
        textMatchesRows = true
        view = next
        clearPreview()
      } catch (problem) {
        error = webError(problem)
      }
    } else {
      try {
        drafts = environmentRowsFromText(text, drafts, mode, appId ? data?.variables : undefined)
        textMatchesRows = true
        view = next
        clearPreview()
        if (!appId) publish()
      } catch (problem) {
        error = webError(problem)
      }
    }
  }
  const hide = () => {
    generation++
    valuesVisible = false
    view = 'normal'
    dirty = false
    clearSecrets()
    publish()
    if (appId) void load()
  }
  const add = () => {
    drafts = [
      ...drafts,
      {
        name: '',
        value: '',
        build: true,
        runtime: true,
        hasValue: false,
        valueKnown: !appId || revealed,
        setEmpty: false,
      },
    ]
    changed()
  }
  const remove = (index: number) => {
    if (drafts[index]?.hasValue) drafts = drafts.map((row, i) => (i === index ? { ...row, remove: !row.remove } : row))
    else drafts = drafts.filter((_, i) => i !== index)
    changed()
  }
  const commit = async (candidate = pending, check = preview) => {
    const request = generation,
      id = appId
    busy = 'save'
    error = ''
    message = ''
    try {
      if (id && candidate && check) {
        const keepVisible = valuesVisible && canReveal
        let result = await webRequest<EnvironmentResponse>(`/${id}/environment`, {
          method: 'PUT',
          body: { ...candidate, previewToken: check.previewToken },
        })
        if (!mounted || request !== generation || id !== appId) return
        if (keepVisible) {
          try {
            result = await webRequest<EnvironmentResponse>(`/${id}/environment/reveal`, { method: 'POST' })
            if (!mounted || request !== generation || id !== appId) return
          } catch (problem) {
            if (!mounted || request !== generation) return
            if (serverFailure(problem)) return
            // PUT succeeded, but a transient reread failure must not wipe the user's visible file or fields.
            if (data) {
              const savedDrafts =
                candidate.text !== undefined
                  ? environmentRowsFromText(candidate.text, drafts, candidate.mode, data.variables)
                  : drafts
              data = {
                ...data,
                revision: result.revision,
                variables: result.variables.map((item) => ({
                  ...item,
                  value: savedDrafts.find((row) => row.name === item.name && !row.remove)?.value || '',
                })),
              }
            }
            rows = publicEnvironmentResponse(result).variables.map((row) => ({ ...row, value: '' }))
            parentRows = rows
            dirty = true
            clearPreview()
            loadedRefresh = refreshKey
            notify(result)
            message = 'Variables enregistrées. La relecture a échoué ; votre saisie reste ouverte.'
            return
          }
        }
        adopt(result)
        loadedRefresh = refreshKey
        notify(result)
        message = result.requiresRebuild
          ? 'Variables enregistrées. Une reconstruction est nécessaire pour les appliquer.'
          : result.pendingChanges
            ? 'Variables enregistrées. Vous pouvez les appliquer sans reconstruire l’image.'
            : 'Variables enregistrées.'
      } else if (localPreview) {
        drafts = localPreview.rows.map((row) => ({ ...row, valueKnown: true }))
        publish()
        text = compactText()
        textMatchesRows = true
        dirty = false
        clearPreview()
        message = 'Variables appliquées au formulaire de création.'
      }
    } catch (problem) {
      if (mounted && request === generation) serverFailure(problem)
    } finally {
      if (mounted && request === generation) busy = ''
    }
  }
  const save = async () => {
    const request = generation,
      id = appId
    busy = 'preview'
    error = ''
    message = ''
    clearPreview()
    try {
      if (id) {
        if (!data) throw new Error('Rechargez les variables avant de continuer.')
        const candidate: Candidate = {
          revision: data.revision,
          mode: view === 'normal' ? 'replace' : mode,
          ...(view === 'normal' ? { rows: environmentRowsPayload(drafts) } : { text }),
        }
        const check = await webRequest<EnvironmentPreview>(`/${id}/environment/preview`, {
          method: 'POST',
          body: candidate,
        })
        if (!mounted || request !== generation || id !== appId) return
        if (check.changes.removed.length) {
          pending = candidate
          preview = check
        } else await commit(candidate, check)
      } else {
        const candidate = view === 'normal' ? environmentRowsText(drafts) : text
        localPreview = localEnvironmentImport(candidate, rows, view === 'normal' ? 'replace' : mode)
        if (!localPreview.changes.removed.length) await commit()
      }
    } catch (problem) {
      if (mounted && request === generation) serverFailure(problem)
    } finally {
      if (mounted && request === generation) busy = ''
    }
  }
  const copy = async (value: string, label: string) => {
    try {
      await navigator.clipboard.writeText(value)
      message = `${label} copié.`
    } catch {
      error = 'Copie indisponible. Sélectionnez le texte dans l’éditeur.'
    }
  }
  const download = (value: string, filename: string) => {
    const url = URL.createObjectURL(new Blob([value], { type: 'text/plain;charset=utf-8' }))
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = filename
    anchor.click()
    URL.revokeObjectURL(url)
  }
  onMount(() => {
    mounted = true
  })
  onDestroy(() => {
    mounted = false
    generation++
    valuesVisible = false
    clearSecrets()
    if (appId)
      rows = rows.map(({ name, build, runtime, hasValue, multiline, isEmpty }) => ({
        name,
        build,
        runtime,
        hasValue,
        multiline,
        isEmpty,
        value: '',
      }))
  })
</script>

<div class="web-env" aria-busy={!!busy}>
  <div class="env-toolbar">
    <h3>Variables d’environnement</h3>
    {#if !readOnly}<div class="env-tabs" role="tablist" aria-label="Vue des variables">
        <button
          type="button"
          role="tab"
          aria-selected={view === 'normal'}
          class:active={view === 'normal'}
          onclick={() => chooseView('normal')}
          disabled={blocked}>Normale</button
        >
        <button
          type="button"
          role="tab"
          aria-selected={view === 'developer'}
          class:active={view === 'developer'}
          onclick={() => chooseView('developer')}
          disabled={blocked || (!!appId && !canReveal)}
          title={appId && !canReveal
            ? 'Votre rôle ne permet pas de révéler les valeurs.'
            : 'Éditer le vrai fichier .env'}>Dev · .env</button
        >
      </div>{/if}
  </div>
  <div class="env-subtoolbar">
    <p class="web-help">
      {view === 'developer'
        ? 'Fichier .env local de cette application. Les valeurs sont lisibles et modifiables.'
        : revealed
          ? 'Modifiez les valeurs directement. Un champ effacé enregistre une valeur vide.'
          : 'Les valeurs sont masquées. Un champ laissé vide conserve la valeur enregistrée.'}
    </p>
    {#if appId}<div class="env-actions">
        {#if canReveal}<button
            type="button"
            class="env-text-button"
            onclick={revealed ? hide : revealValues}
            disabled={blocked || !data}>{revealed ? 'Masquer' : 'Révéler les valeurs'}</button
          >{/if}
        <button type="button" class="env-text-button" onclick={load} disabled={blocked}>Recharger</button>
        <details class="env-export">
          <summary>Exporter</summary>
          <div class="env-export-menu">
            <button
              type="button"
              class="web-button"
              onclick={() => copy(nextExport?.text || '', '.env.local Next.js')}
              disabled={blocked || !canExport}>Copier .env.local Next.js</button
            >
            <button
              type="button"
              class="web-button"
              onclick={() => download(nextExport?.text || '', '.env.local')}
              disabled={blocked || !canExport}>Télécharger .env.local Next.js</button
            >
            <button
              type="button"
              class="web-button"
              onclick={() => download(data?.example || '', '.env.example')}
              disabled={blocked || !data}>Modèle sans secrets</button
            >
          </div>
        </details>
      </div>{/if}
  </div>
  {#if error}<div class="web-alert web-alert--error" role="alert">{error}</div>{/if}
  {#if createTextError}<div class="web-alert web-alert--error" role="alert">{createTextError}</div>{/if}
  {#if message}<div class="web-alert web-alert--success" role="status">{message}</div>{/if}
  {#if busy === 'load' || busy === 'reveal'}<p class="web-muted" role="status">
      {busy === 'reveal' ? 'Révélation des valeurs…' : 'Chargement des variables…'}
    </p>{/if}
  {#if readOnly}
    <ul class="env-names" aria-label="Variables configurées">
      {#each data?.variables || [] as variable}<li>
          <code>{variable.name}</code><span class="web-help"
            >{[variable.build ? 'construction' : '', variable.runtime ? 'exécution' : '']
              .filter(Boolean)
              .join(' · ')}</span
          >
        </li>{/each}
    </ul>
    <p class="web-help">Consultation des noms uniquement. Les valeurs restent masquées pour votre rôle.</p>
  {:else if view === 'developer'}
    <div class="env-editor-heading">
      <code>.env</code><label
        ><span class="env-sr-only">Variables absentes du fichier</span><select
          value={mode}
          onchange={(event) => {
            mode = event.currentTarget.value as 'merge' | 'replace'
            changed('text')
          }}
          disabled={blocked}
          ><option value="replace">Remplacer les variables</option><option value="merge"
            >Fusionner avec les variables existantes</option
          ></select
        ></label
      >
    </div>
    <textarea
      class="env-code"
      aria-label="Contenu du fichier .env"
      bind:this={rawEditor}
      value={text}
      oninput={(event) => {
        text = event.currentTarget.value
        changed('text')
      }}
      disabled={blocked || (!!appId && !revealed)}
      spellcheck="false"
      autocomplete="off"
      autocapitalize="off"
      rows="14"
      placeholder={"API_URL='https://api.example.com'\nAPI_SECRET='valeur'"}
    ></textarea>
    <p class="web-help env-editor-help">
      <code>NOM='valeur'</code> définit une valeur ; <code>NOM=</code> la vide. Les <code>$VARIABLE</code> restent
      littéraux. Les disponibilités existantes sont conservées ; une directive
      <code># pockethost: build=true runtime=false</code> permet de les modifier.
    </p>
  {:else}
    {#if drafts.length}<div class="env-table-heading" aria-hidden="true">
        <span>Nom</span><span>Valeur</span><span>Disponibilité</span><span></span>
      </div>{/if}
    <div class="env-table">
      {#each drafts as row, index}<div class="env-table-row" class:env-row-deleted={row.remove}>
          <input
            class="env-name"
            aria-label={`Nom de la variable ${index + 1}`}
            value={row.name}
            oninput={(event) => update(index, { name: event.currentTarget.value })}
            placeholder="API_SECRET"
            pattern="[A-Za-z_][A-Za-z0-9_]*"
            maxlength="101"
            disabled={blocked || !!row.remove || !!row.hasValue}
            spellcheck="false"
            autocomplete="off"
          />
          <div class="env-value">
            {#if row.multiline}<textarea
                aria-label={`Valeur de ${row.name || 'la variable ' + (index + 1)}`}
                value={row.value || ''}
                oninput={(event) => editValue(index, event.currentTarget.value)}
                rows="2"
                placeholder={row.hasValue && !revealed ? '•••••••• · vide pour conserver' : 'Valeur'}
                disabled={blocked || !!row.remove || (!!row.setEmpty && !row.valueKnown)}
                autocomplete="off"
                spellcheck="false"
              ></textarea>{:else}<input
                aria-label={`Valeur de ${row.name || 'la variable ' + (index + 1)}`}
                type={revealed || !appId ? 'text' : 'password'}
                value={row.value || ''}
                oninput={(event) => editValue(index, event.currentTarget.value)}
                placeholder={row.hasValue && !revealed ? '•••••••• · vide pour conserver' : 'Valeur'}
                disabled={blocked || !!row.remove || (!!row.setEmpty && !row.valueKnown)}
                autocomplete="new-password"
                spellcheck="false"
              />{/if}
          </div>
          <div class="env-scopes">
            <label title="Disponible à la construction"
              ><input
                type="checkbox"
                checked={row.build}
                onchange={(event) => update(index, { build: event.currentTarget.checked })}
                disabled={blocked || !!row.remove}
              /> Build</label
            ><label title="Disponible à l’exécution"
              ><input
                type="checkbox"
                checked={row.runtime}
                onchange={(event) => update(index, { runtime: event.currentTarget.checked })}
                disabled={blocked || !!row.remove}
              /> Runtime</label
            >
          </div>
          <div class="env-row-actions">
            <details>
              <summary aria-label={`Options de ${row.name || 'la variable ' + (index + 1)}`}>Options</summary>
              <div class="env-row-options">
                <label
                  ><input
                    type="checkbox"
                    checked={row.multiline}
                    onchange={(event) => update(index, { multiline: event.currentTarget.checked })}
                    disabled={blocked || !!row.remove}
                  /> Multiligne</label
                ><label
                  ><input
                    type="checkbox"
                    checked={row.setEmpty}
                    onchange={(event) =>
                      update(index, {
                        setEmpty: event.currentTarget.checked,
                        ...(event.currentTarget.checked ? { value: '' } : {}),
                      })}
                    disabled={blocked || !!row.remove}
                  /> Valeur vide</label
                >
              </div>
            </details>
            <button
              type="button"
              class="env-remove"
              onclick={() => remove(index)}
              disabled={blocked}
              aria-label={`${row.remove ? 'Rétablir' : 'Retirer'} ${row.name || 'la variable ' + (index + 1)}`}
              >{row.remove ? 'Rétablir' : 'Retirer'}</button
            >
          </div>
        </div>{/each}
    </div>
    {#if !drafts.length}<p class="web-muted">Aucune variable configurée.</p>{/if}
    <button type="button" class="env-add" onclick={add} disabled={blocked}>+ Ajouter une variable</button>
  {/if}
  {#if !readOnly}<div class="env-save-bar">
      <p class="web-help">
        {dirty ? 'Modifications non enregistrées.' : 'Les NEXT_PUBLIC_* sont publiques dans le navigateur.'} Recharger ou
        masquer annule les modifications.
      </p>
      <button
        type="button"
        class="web-button web-button--primary"
        onclick={save}
        disabled={blocked || !dirty || (!!appId && !data) || !!changes?.removed.length}
        >{busy === 'preview'
          ? 'Vérification…'
          : busy === 'save'
            ? 'Enregistrement…'
            : appId
              ? 'Enregistrer'
              : 'Appliquer au formulaire'}</button
      >
    </div>{/if}
  {#if changes?.removed.length}<section class="env-preview" aria-live="polite">
      <h4>Confirmer la suppression</h4>
      <p>Ces variables seront retirées : <strong>{changes.removed.join(', ')}</strong>.</p>
      <p class="web-help">
        {changes.added.length} ajout(s), {changes.updated.length} modification(s). Les valeurs ne figurent pas dans cet aperçu.
      </p>
      <div class="env-actions">
        <button type="button" class="web-button web-button--danger" onclick={() => commit()} disabled={blocked}
          >{busy === 'save'
            ? 'Enregistrement…'
            : `Supprimer ${changes.removed.length} variable(s) et enregistrer`}</button
        ><button type="button" class="web-button" onclick={clearPreview} disabled={blocked}>Continuer l’édition</button>
      </div>
    </section>{/if}
</div>

<style>
  .env-toolbar,
  .env-subtoolbar,
  .env-actions,
  .env-save-bar {
    display: flex;
    align-items: center;
    gap: 0.65rem;
  }
  .env-toolbar,
  .env-subtoolbar,
  .env-save-bar {
    justify-content: space-between;
    flex-wrap: wrap;
  }
  .env-toolbar h3 {
    margin: 0;
  }
  .env-subtoolbar {
    align-items: baseline;
  }
  .env-subtoolbar p {
    flex: 1;
    min-width: 220px;
    margin: 0;
  }
  .env-tabs {
    display: inline-flex;
    padding: 3px;
    border: 1px solid var(--web-border);
    border-radius: var(--web-radius, 6px);
    background: var(--web-surface-soft);
  }
  .env-tabs button {
    border: 0;
    border-radius: 4px;
    background: transparent;
    color: var(--web-muted);
    padding: 5px 10px;
    font: inherit;
    font-size: 0.8rem;
    cursor: pointer;
  }
  .env-tabs button.active {
    background: var(--app-accent-soft, #3b7cff1c);
    color: var(--app-accent-text, #7aa7ff);
  }
  .env-tabs button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  .env-actions {
    flex-wrap: wrap;
  }
  .env-text-button,
  .env-add,
  .env-remove {
    border: 0;
    background: transparent;
    color: var(--web-muted);
    font: inherit;
    font-size: 0.78rem;
    cursor: pointer;
    padding: 6px 0;
  }
  .env-text-button:hover,
  .env-add:hover {
    color: var(--web-text);
  }
  .env-remove:hover {
    color: var(--web-danger);
  }
  button:disabled {
    cursor: default;
    opacity: 0.5;
  }
  .env-export {
    position: relative;
    font-size: 0.78rem;
    color: var(--web-muted);
  }
  .env-row-actions details,
  .env-export {
    margin: 0;
    padding: 0;
    border: 0;
    background: transparent;
  }
  .env-row-actions summary,
  .env-export > summary {
    padding: 4px 0;
  }
  summary {
    cursor: pointer;
    user-select: none;
  }
  .env-export-menu {
    position: absolute;
    right: 0;
    top: calc(100% + 8px);
    min-width: 220px;
    display: grid;
    gap: 6px;
    padding: 10px;
    border: 1px solid var(--web-border);
    border-radius: var(--web-radius, 6px);
    background: var(--web-surface);
    z-index: 3;
    box-shadow: 0 8px 24px #0003;
  }
  .env-table-heading,
  .env-table-row {
    display: grid;
    grid-template-columns: minmax(150px, 1fr) minmax(180px, 1.6fr) 150px 110px;
    gap: 10px;
    align-items: center;
  }
  .env-table-heading {
    color: var(--web-muted);
    font-size: 0.72rem;
    padding: 0 0 4px;
  }
  .env-table {
    border-top: 1px solid var(--web-border);
  }
  .env-table-row {
    min-height: 53px;
    border-bottom: 1px solid var(--web-border);
    padding: 8px 0;
  }
  .env-table-row input:not([type='checkbox']),
  .env-table-row textarea {
    min-width: 0;
    width: 100%;
    min-height: 34px;
    padding: 6px 8px;
    border: 1px solid var(--web-border);
    border-radius: 4px;
    background: var(--web-input-bg, var(--web-surface-soft));
    color: var(--web-text);
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    font-size: 0.78rem;
    line-height: 1.45;
  }
  .env-table-row input:disabled {
    opacity: 0.8;
  }
  .env-name:disabled {
    background: transparent !important;
    border-color: transparent !important;
  }
  .env-value {
    min-width: 0;
  }
  .env-scopes {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 10px;
    font-size: 0.72rem;
  }
  .env-scopes label,
  .env-row-options label {
    display: flex;
    align-items: center;
    gap: 5px;
    white-space: nowrap;
  }
  .env-scopes input,
  .env-row-options input {
    width: 13px;
    height: 13px;
    margin: 0;
    accent-color: var(--app-accent-text, #7aa7ff);
  }
  .env-row-actions {
    display: flex;
    justify-content: flex-end;
    align-items: center;
    gap: 12px;
    font-size: 0.72rem;
  }
  .env-row-actions details {
    position: relative;
  }
  .env-row-options {
    position: absolute;
    right: 0;
    top: calc(100% + 10px);
    display: grid;
    gap: 12px;
    padding: 14px;
    min-width: 155px;
    border: 1px solid var(--web-border);
    border-radius: var(--web-radius, 6px);
    background: var(--web-surface);
    z-index: 2;
    box-shadow: 0 6px 20px #0003;
  }
  .env-row-deleted {
    opacity: 0.55;
  }
  .env-row-deleted .env-name {
    text-decoration: line-through;
  }
  .env-add {
    justify-self: start;
    color: var(--app-accent-text, #7aa7ff);
  }
  .env-code {
    display: block;
    width: 100%;
    min-height: 22rem;
    resize: vertical;
    padding: 14px 16px;
    border: 1px solid var(--web-border);
    border-radius: 0 0 var(--web-radius, 6px) var(--web-radius, 6px);
    background: var(--web-input-bg, var(--web-surface-soft));
    color: var(--web-text);
    font:
      0.8125rem/1.7 ui-monospace,
      SFMono-Regular,
      Consolas,
      monospace;
    tab-size: 2;
    white-space: pre;
    overflow: auto;
  }
  .env-editor-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 8px 12px;
    margin-bottom: -0.8rem;
    border: 1px solid var(--web-border);
    border-bottom: 0;
    border-radius: var(--web-radius, 6px) var(--web-radius, 6px) 0 0;
    background: var(--web-surface-soft);
    font-size: 0.75rem;
  }
  .env-editor-heading code {
    padding: 0;
    background: none;
    flex-shrink: 0;
    white-space: nowrap;
  }
  .env-editor-heading > label {
    width: auto;
    min-width: 0;
    max-width: 100%;
  }
  .env-editor-heading select {
    width: auto;
    max-width: 100%;
    border: 0;
    background: transparent;
    color: var(--web-muted);
    font: inherit;
  }
  .env-editor-help {
    margin: 0;
  }
  .env-save-bar {
    padding-top: 4px;
  }
  .env-save-bar p {
    margin: 0;
    flex: 1;
  }
  .env-preview {
    display: grid;
    gap: 10px;
    padding: 14px;
    border: 1px solid var(--web-border);
    border-radius: var(--web-radius, 6px);
    background: var(--web-surface-soft);
  }
  .env-preview h4,
  .env-preview p {
    margin: 0;
  }
  .env-preview h4 {
    font-size: 0.875rem;
  }
  .env-preview strong {
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    font-size: 0.8rem;
  }
  .env-names {
    display: grid;
    gap: 8px;
    list-style: none;
    padding: 0;
    margin: 0;
  }
  .env-names li {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: baseline;
  }
  .env-sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
  }
  button:focus-visible,
  summary:focus-visible,
  input:focus-visible,
  textarea:focus-visible,
  select:focus-visible {
    outline: 2px solid var(--app-accent-text, #7aa7ff);
    outline-offset: 2px;
  }
  @media (max-width: 1000px) {
    .env-table-heading,
    .env-table-row {
      grid-template-columns: minmax(130px, 1fr) minmax(150px, 1.4fr) 132px 100px;
      gap: 8px;
    }
    .env-scopes {
      gap: 7px;
    }
    .env-row-actions {
      gap: 8px;
    }
  }
  @media (max-width: 760px) {
    .env-table-heading {
      display: none;
    }
    .env-table-row {
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px 12px;
      padding: 12px 0;
    }
    .env-name {
      grid-column: 1/-1;
    }
    .env-value {
      grid-column: 1/-1;
    }
    .env-row-actions {
      grid-column: 2;
      grid-row: 3;
    }
    .env-scopes {
      grid-row: 3;
    }
    .env-table-row input:not([type='checkbox']),
    .env-table-row textarea {
      min-height: 40px;
    }
    .env-tabs button,
    .env-text-button,
    .env-add,
    .env-remove,
    summary {
      min-height: 32px;
    }
    .env-subtoolbar {
      align-items: start;
    }
    .env-editor-heading {
      align-items: start;
      flex-direction: column;
    }
    .env-save-bar {
      align-items: start;
    }
  }
</style>
