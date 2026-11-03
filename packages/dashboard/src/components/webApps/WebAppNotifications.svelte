<script lang="ts">
  import { onMount } from 'svelte'
  import { client } from '$src/pocketbase-client'
  import { webRequest, webError, webDate } from '$lib/webApps'
  import { confirmAction } from '$lib/webNotice'
  import {
    notificationEvents,
    notificationEventTitle,
    platformNotificationEvents,
    webhookFormats,
    type NotificationSettings,
    type NotificationDelivery,
    type NotificationTestResult,
    type DeploymentEmailMode,
  } from '$lib/webObservability'
  import { WEB_NO_CAPABILITIES, type WebCapabilityMap } from '$lib/webAccessView'
  /** Application concernée ; vide pour le canal de la plateforme. */
  export let appId = ''
  export let capabilities: WebCapabilityMap = WEB_NO_CAPABILITIES
  /** 'platform' : canal des superAdmins (/api/web-platform-notifications), mêmes contrôles que pour une application. */
  export let scope: 'app' | 'platform' = 'app'
  /** Ouvre un autre onglet de l'application (ex. « Santé ») ; facultatif. */
  export let onopentab: ((tab: string) => void) | undefined = undefined
  type Channel = 'webhook' | 'email'
  type TestState = { state: 'sending' } | { state: 'done'; row: NotificationDelivery; done: boolean } | null
  const platform = scope === 'platform'
  // Canal plateforme : la page qui l'affiche est réservée aux superAdmins, le serveur le revérifie à chaque appel.
  $: canManage = platform || !!capabilities['notifications.manage']
  $: canTest = platform || !!capabilities['alerts.test']
  const request = <T,>(suffix: string, options: Record<string, unknown> = {}) =>
    platform
      ? client().client.send<T>(`/api/web-platform-notifications${suffix}`, { ...options, requestKey: null })
      : webRequest<T>(`/${appId}/notifications${suffix}`, options)
  let settings: NotificationSettings | null = null,
    rows: NotificationDelivery[] = [],
    page = 1,
    hasMore = false,
    busy = '',
    historyBusy = false,
    error = '',
    message = '',
    disposed = false
  let webhookEnabled = false,
    url = '',
    secret = '',
    format: 'generic' | 'slack' | 'discord' = 'generic',
    emailEnabled = false,
    recipients = '',
    projectAdmins = false,
    copyAppEvents = false,
    events: string[] = [],
    cooldownSeconds = 300,
    removeWebhook = false,
    revealed = ''
  let testEvent: Record<Channel, string> = { webhook: 'test', email: 'test' },
    testResult: Record<Channel, TestState> = { webhook: null, email: null }
  let deploymentEmails: DeploymentEmailMode = 'inherit'
  const labels: Record<string, string> = {
    pending: 'Nouvel essai prévu',
    sending: 'Envoi en cours',
    sent: 'Envoyée',
    failed: 'Échec définitif',
    suppressed: 'Regroupée / annulée',
  }
  $: available = platform
    ? platformNotificationEvents
    : Object.fromEntries(
        Object.entries(notificationEvents).filter(
          // Capacité de l'hôte : seulement pour les applications opérées par la plateforme.
          ([event]) => !event.startsWith('disk_') || settings?.platformOperated
        )
      )
  $: placeholder =
    format === 'slack'
      ? 'https://hooks.slack.com/services/…'
      : format === 'discord'
        ? 'https://discord.com/api/webhooks/…'
        : 'https://notifications.example.com/webhook'
  const hydrate = (value: NotificationSettings) => {
    settings = value
    webhookEnabled = value.webhook.enabled
    format = value.webhook.format || 'generic'
    emailEnabled = value.email.enabled
    recipients = value.email.recipients.join('\n')
    // Nouvelle configuration dans un projet : prévenir ses owners/admins est proposé par défaut (rien n'est envoyé
    // avant l'enregistrement explicite).
    projectAdmins = value.revision === '' && !platform ? !!value.inProject : !!value.email.projectAdmins
    copyAppEvents = !!value.copyAppEvents
    deploymentEmails = value.deploymentEmails || 'inherit'
    events = [...value.events]
    cooldownSeconds = value.cooldownSeconds
    url = ''
    secret = ''
    removeWebhook = false
    if (value.generatedSecret) revealed = value.generatedSecret
  }
  const history = async (next = page) => {
    if (historyBusy || disposed) return
    historyBusy = true
    try {
      const value = await request<{ items: NotificationDelivery[]; page: number; hasMore: boolean }>('/history', {
        query: { page: next },
      })
      if (!disposed) {
        rows = value.items
        page = value.page
        hasMore = value.hasMore
      }
    } catch (e) {
      if (!disposed) error = webError(e)
    } finally {
      historyBusy = false
    }
  }
  const load = async () => {
    busy = 'load'
    error = ''
    try {
      const value = await request<NotificationSettings>('')
      if (!disposed) hydrate(value)
    } catch (e) {
      if (!disposed) error = webError(e)
    } finally {
      busy = ''
    }
  }
  const put = async (body: Record<string, unknown>, done: string) => {
    if (!settings) return
    busy = 'save'
    error = ''
    message = ''
    try {
      const value = await request<NotificationSettings>('', {
        method: 'PUT',
        body: { revision: settings.revision, ...body },
      })
      hydrate(value)
      message = done
      await history(1)
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  const save = (event: SubmitEvent) => {
    event.preventDefault()
    void put(
      {
        webhook: {
          enabled: removeWebhook ? false : webhookEnabled,
          url,
          secret: format === 'generic' ? secret : '',
          format,
          remove: removeWebhook,
        },
        email: {
          enabled: emailEnabled,
          recipients: recipients
            .split(/[\n,;]/)
            .map((value) => value.trim())
            .filter(Boolean),
          ...(!platform && settings?.inProject ? { projectAdmins } : {}),
        },
        events,
        cooldownSeconds: Number(cooldownSeconds),
        ...(platform ? { copyAppEvents } : { deploymentEmails }),
      },
      'Canaux enregistrés. Seuls les canaux activés recevront les prochains événements.'
    )
  }
  const rotate = async () => {
    if (
      !(await confirmAction(
        'Générer un nouveau secret ?',
        'Les envois en attente signés avec l’ancien secret seront annulés.',
        'Générer'
      ))
    )
      return
    void put({ webhook: { enabled: webhookEnabled, rotateSecret: true } }, 'Nouveau secret généré.')
  }
  const copySecret = async () => {
    try {
      await navigator.clipboard.writeText(revealed)
      message = 'Secret copié dans le presse-papiers.'
    } catch {
      error = 'Copie impossible : sélectionnez le secret et copiez-le manuellement.'
    }
  }
  const test = async (channel: Channel) => {
    busy = 'test-' + channel
    error = ''
    message = ''
    testResult = { ...testResult, [channel]: { state: 'sending' } }
    try {
      const event = testEvent[channel]
      const value = await request<NotificationTestResult>('/test', {
        method: 'POST',
        body: { channel, ...(event !== 'test' ? { event } : {}) },
      })
      let row = value.result,
        done = value.done
      // 202 : le worker n'a pas encore rendu compte ; on suit cette seule livraison quelques secondes.
      for (let i = 0; !done && i < 15 && ['pending', 'sending'].includes(row.status) && !disposed; i++) {
        await new Promise((resolve) => setTimeout(resolve, 2000))
        row = (await request<{ items: NotificationDelivery[] }>('/history', { query: { id: row.id } })).items[0] ?? row
        done = !['pending', 'sending'].includes(row.status)
      }
      if (!disposed) testResult = { ...testResult, [channel]: { state: 'done', row, done } }
      await history(1)
    } catch (e) {
      testResult = { ...testResult, [channel]: null }
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  const testText = (channel: Channel, value: TestState) => {
    if (!value) return ''
    if (value.state === 'sending') return 'Envoi du test…'
    const row = value.row
    if (row.status === 'sent')
      return channel === 'webhook' ? `Envoyé — HTTP ${row.httpStatus || 200}` : 'Envoyé au serveur SMTP'
    if (row.status === 'failed' || row.status === 'suppressed')
      return `Échec : ${row.lastError || 'le canal a refusé le test.'}`
    return 'Toujours en file : le worker web semble indisponible.'
  }
  const toggle = (event: string) =>
    (events = events.includes(event) ? events.filter((value) => value !== event) : [...events, event])
  onMount(() => {
    disposed = false
    // Canaux et destinataires (emails) : administrateurs du projet uniquement ; l'historique reste lisible dès viewer.
    if (canManage) void load()
    void history(1)
    const timer = setInterval(() => {
      if (!document.hidden) void history()
    }, 10000)
    return () => {
      disposed = true
      url = ''
      secret = ''
      recipients = ''
      revealed = ''
      clearInterval(timer)
    }
  })
</script>

<section class="web-panel notification-panel">
  <div class="web-row">
    <div>
      <span class="web-eyebrow">{platform ? 'Exploitation du serveur' : 'Incidents et rétablissements'}</span>
      <h2>{platform ? 'Alertes de la plateforme' : 'Alertes externes'}</h2>
    </div>
    <span
      class="web-status"
      data-tone={settings?.paused || settings?.pausedLegacy
        ? 'warning'
        : settings?.webhook.enabled || settings?.email.enabled
          ? 'success'
          : 'neutral'}
      >{settings?.paused || settings?.pausedLegacy
        ? 'Envois suspendus'
        : settings?.webhook.enabled || settings?.email.enabled
          ? 'Canal actif'
          : 'Canaux dédiés inactifs'}</span
    >
  </div>
  <p class="web-help">
    {#if platform}Worker muet, espace disque, échecs de reprise et de capture : ces alertes ne concernent que les
      opérateurs du serveur.{:else}Réglez les emails de déploiement de cette application et ses autres alertes. Les
      emails utilisent les destinataires configurés ici ou par les opérateurs de la plateforme.{/if}
  </p>
  {#if platform && settings?.worker}<p class="web-help" role="status">
      Dernier contact du worker : {settings.worker.lastClaimAt
        ? webDate(settings.worker.lastClaimAt)
        : 'jamais'}{#if settings.worker.down}
        · <strong>worker injoignable</strong>{/if}
    </p>{/if}
  {#if settings?.pausedLegacy}<div class="web-alert" role="status">
      Ces alertes ont été suspendues par une ancienne version de la plateforme. Enregistrez à nouveau pour les
      reprendre.
    </div>{:else if settings?.paused}<div class="web-alert" role="status">
      Application hors projet : l’auteur de ces canaux n’a plus les droits plateforme. Rattachez l’application à un
      projet ou ré-enregistrez avec votre compte.
    </div>{/if}
  {#if revealed}<div class="web-alert web-alert--success" role="status" data-notice="off">
      <strong>Secret de signature à copier maintenant</strong> — il ne sera plus jamais affiché.
      <code class="secret">{revealed}</code>
      <div class="web-actions">
        <button class="web-button" type="button" onclick={copySecret}>Copier</button>
        <button class="web-button" type="button" onclick={() => (revealed = '')}>J’ai copié le secret</button>
      </div>
    </div>{/if}
  {#if error}<div class="web-alert web-alert--error" role="alert">{error}</div>{/if}{#if message}<div
      class="web-alert web-alert--success"
      role="status"
    >
      {message}
    </div>{/if}
  {#if !canManage}<p class="web-muted">
      Les canaux d’alerte et leurs destinataires sont réglés par les administrateurs du projet. L’historique des envois
      reste consultable ci-dessous.
    </p>{:else if !settings}<p class="web-muted" role="status">Chargement des canaux…</p>{:else}<form
      class="web-form"
      onsubmit={save}
    >
      {#if !platform}<fieldset class="channel-card deployment-emails" disabled={!!busy || !canManage}>
          <legend>Emails de déploiement</legend>
          <label class="web-field">
            <span>Quand recevoir un email pour cette application ?</span>
            <select bind:value={deploymentEmails}>
              <option value="inherit">Conserver les réglages actuels</option>
              <option value="all">Tous les déploiements · réussis et échoués</option>
              <option value="failed">Seulement les déploiements échoués</option>
              <option value="none">Aucun email de déploiement</option>
            </select>
          </label>
          <p class="web-help">
            Ce choix s’applique aussi aux emails envoyés aux opérateurs de la plateforme. En cas d’échec, le journal
            reste joint avec les valeurs sensibles masquées.
          </p>
          <div class="web-actions">
            <button
              class="web-button web-button--primary"
              type="button"
              disabled={!!busy || deploymentEmails === (settings.deploymentEmails || 'inherit')}
              onclick={() => put({ deploymentEmails }, 'Préférence des emails de déploiement enregistrée.')}
              >{busy === 'save' ? 'Enregistrement…' : 'Enregistrer ce choix'}</button
            >
          </div>
        </fieldset>{/if}
      <fieldset class="web-fieldset" disabled={!canManage}>
        <fieldset class="channel-card">
          <legend>Webhook HTTPS</legend><label class="web-field"
            ><span>Format</span><select bind:value={format} disabled={!!busy || removeWebhook}
              >{#each Object.entries(webhookFormats) as [value, text]}<option {value}>{text}</option>{/each}</select
            >{#if format !== 'generic'}<span class="web-help"
                >Le secret HMAC n’est pas vérifié par Slack ou Discord ; gardez l’URL secrète.</span
              >{/if}</label
          ><label class="web-check"
            ><input type="checkbox" bind:checked={webhookEnabled} disabled={!!busy || removeWebhook} /> Activer ce webhook</label
          ><label class="web-field"
            ><span>URL du destinataire</span><input
              type="password"
              bind:value={url}
              autocomplete="new-password"
              maxlength="2000"
              disabled={!!busy || removeWebhook}
              placeholder={settings.webhook.configured
                ? `Enregistrée pour ${settings.webhook.endpointHost} · vide pour conserver`
                : placeholder}
            /><span class="web-help"
              >Adresse publique HTTPS, port 443. Les adresses privées et les redirections sont refusées.</span
            ></label
          >{#if format === 'generic'}<label class="web-field"
              ><span>Secret de signature HMAC · facultatif</span><input
                type="password"
                bind:value={secret}
                autocomplete="new-password"
                minlength="16"
                maxlength="256"
                disabled={!!busy || removeWebhook}
                placeholder={settings.webhook.hasSecret
                  ? 'Secret enregistré · vide pour conserver'
                  : '16 caractères minimum · sinon une clé est générée et affichée une fois'}
              /><span class="web-help"
                >Les requêtes portent un identifiant stable, un horodatage et une signature SHA-256. Les valeurs des
                variables et les journaux ne sont pas envoyés.</span
              ></label
            >{/if}
          <div class="web-actions">
            {#if settings.webhook.configured && settings.webhook.format === 'generic' && !removeWebhook}<button
                class="web-button"
                type="button"
                onclick={rotate}
                disabled={!!busy}>Générer un nouveau secret</button
              >{/if}{#if settings.webhook.configured}<label class="web-check"
                ><input type="checkbox" bind:checked={removeWebhook} disabled={!!busy} /> Effacer ce webhook à l’enregistrement</label
              >{/if}
          </div>
          <div class="test-row">
            <label class="web-field"
              ><span>Type d’alerte à simuler</span><select bind:value={testEvent.webhook} disabled={!!busy}
                ><option value="test">Test simple</option>{#each Object.entries(available) as [event, label]}<option
                    value={event}>{label}</option
                  >{/each}</select
              ></label
            ><button
              class="web-button"
              type="button"
              onclick={() => test('webhook')}
              disabled={!!busy ||
                !canTest ||
                !settings.webhook.configured ||
                !settings.webhook.enabled ||
                removeWebhook}>{busy === 'test-webhook' ? 'Envoi…' : 'Envoyer le test'}</button
            >
          </div>
          {#if testResult.webhook}<p
              class="web-help test-result"
              role="status"
              data-tone={testResult.webhook.state === 'done' && testResult.webhook.row.status === 'sent'
                ? 'success'
                : ''}
            >
              {testText('webhook', testResult.webhook)}
            </p>{/if}
        </fieldset>
        <fieldset class="channel-card">
          <legend>Email via SMTP de la plateforme</legend>{#if !settings.smtpAvailable}<div class="web-alert">
              Configurez SMTP dans <a href="/admin">l’administration du serveur</a> pour activer les emails. Vous pouvez préparer
              les destinataires.
            </div>{/if}<label class="web-check"
            ><input type="checkbox" bind:checked={emailEnabled} disabled={!!busy || !settings.smtpAvailable} /> Activer les
            emails</label
          ><label class="web-field"
            ><span>Destinataires · cinq adresses maximum</span><textarea
              bind:value={recipients}
              rows="3"
              disabled={!!busy}
              autocomplete="off"
              placeholder="ops@example.com&#10;admin@example.com"
            ></textarea><span class="web-help"
              >{platform
                ? 'Une adresse par ligne : les opérateurs du serveur.'
                : 'Une adresse par ligne : membres vérifiés du projet, ou la vôtre.'}</span
            ></label
          >{#if !platform && settings.inProject}<label class="web-check"
              ><input type="checkbox" bind:checked={projectAdmins} disabled={!!busy} /> Prévenir aussi les propriétaires
              et administrateurs du projet ({settings.email.projectAdminCount ?? 0})</label
            >{/if}
          <div class="test-row">
            <label class="web-field"
              ><span>Type d’alerte à simuler</span><select bind:value={testEvent.email} disabled={!!busy}
                ><option value="test">Test simple</option>{#each Object.entries(available) as [event, label]}<option
                    value={event}>{label}</option
                  >{/each}</select
              ></label
            ><button
              class="web-button"
              type="button"
              onclick={() => test('email')}
              disabled={!!busy ||
                !canTest ||
                !settings.email.configured ||
                !settings.email.enabled ||
                !settings.smtpAvailable}>{busy === 'test-email' ? 'Envoi…' : 'Envoyer le test'}</button
            >
          </div>
          {#if testResult.email}<p
              class="web-help test-result"
              role="status"
              data-tone={testResult.email.state === 'done' && testResult.email.row.status === 'sent' ? 'success' : ''}
            >
              {testText('email', testResult.email)}
            </p>{/if}
        </fieldset>
        <fieldset class="channel-card">
          <legend>Événements à recevoir</legend>
          {#if !platform && deploymentEmails !== 'inherit'}<p class="web-help">
              Pour les déploiements, ces cases règlent les webhooks. Les emails suivent le choix ci-dessus.
            </p>{/if}
          {#if !platform && settings.healthMonitoring === 'disabled' && (events.includes('health_degraded') || events.includes('health_recovered'))}<div
              class="web-alert"
              role="status"
            >
              La surveillance de santé est désactivée pour cette application : aucune alerte « Site indisponible » ni «
              Site rétabli » ne sera émise.{#if onopentab}
                <button class="web-button" type="button" onclick={() => onopentab?.('health')}
                  >Activer la surveillance</button
                >{:else}
                Activez-la dans l’onglet Santé.{/if}
            </div>{/if}
          <div class="event-choices">
            {#each Object.entries(available) as [event, label]}<label class="web-check"
                ><input
                  type="checkbox"
                  checked={events.includes(event)}
                  onchange={() => toggle(event)}
                  disabled={!!busy}
                />
                {label}</label
              >{/each}
          </div>
          {#if platform}<label class="web-check"
              ><input type="checkbox" bind:checked={copyAppEvents} disabled={!!busy} /> Recevoir aussi les événements des
              applications</label
            >
            <p class="web-help">
              Les emails de déploiement respectent le choix enregistré dans chaque application.
            </p>{/if}
          <label class="web-field cooldown"
            ><span>Regrouper un même type d’alerte pendant · secondes</span><input
              type="number"
              bind:value={cooldownSeconds}
              min="60"
              max="86400"
              step="1"
              required
              disabled={!!busy}
            /></label
          >
          <p class="web-help">
            Les emails de déploiement ne sont pas regroupés. Les autres alertes automatiques sont limitées à 12 par
            canal et par heure ; les tests ne sont jamais regroupés (10 par heure).
          </p>
        </fieldset>
      </fieldset>
      <div class="web-actions">
        {#if canManage}<button class="web-button web-button--primary" type="submit" disabled={!!busy}
            >{busy === 'save' ? 'Enregistrement…' : 'Enregistrer les canaux et événements'}</button
          >{:else}<span class="web-help"
            >Consultation seule : les alertes sont réglées par un administrateur du projet.</span
          >{/if}<button class="web-button" type="button" onclick={load} disabled={!!busy}
          >Recharger la configuration</button
        >
      </div>
    </form>{/if}
</section>
<section class="web-panel notification-panel">
  <div class="web-row">
    <h2>Historique des envois</h2>
    <button class="web-button" type="button" onclick={() => history()} disabled={historyBusy}>Actualiser</button>
  </div>
  <div class="web-table-wrap">
    <table>
      <thead><tr><th>Événement</th><th>Canal</th><th>État</th><th>Tentatives</th><th>Date</th></tr></thead><tbody
        >{#each rows as row}<tr
            ><td
              >{notificationEventTitle(row.event)}{#if row.trigger === 'test'}
                <span class="web-status" data-tone="neutral">Test</span>{/if}</td
            ><td>{row.channel === 'email' ? 'Email' : 'Webhook HTTPS'}</td><td
              ><span
                class="web-status"
                data-tone={row.status === 'sent' ? 'success' : row.status === 'failed' ? 'error' : 'neutral'}
                >{labels[row.status] || row.status}</span
              >{#if row.httpStatus}<div class="web-help">HTTP {row.httpStatus}</div>{/if}</td
            ><td>{row.attempts}/{row.trigger === 'test' ? 1 : 3}</td><td>{webDate(row.sentAt || row.created)}</td></tr
          >{#if row.lastError}<tr
              ><td colspan="5"
                ><p class="web-help">
                  {row.lastError}{#if row.status === 'pending'}
                    Prochain essai : {webDate(row.nextAttemptAt)}.{/if}
                </p></td
              ></tr
            >{/if}{:else}<tr
            ><td colspan="5"
              >{historyBusy
                ? 'Chargement…'
                : 'Aucun envoi. Configurez un canal pour recevoir les prochains événements.'}</td
            ></tr
          >{/each}</tbody
      >
    </table>
  </div>
  {#if page > 1 || hasMore}<nav class="web-pagination" aria-label="Pages de l’historique des notifications">
      <button class="web-button" type="button" onclick={() => history(page - 1)} disabled={historyBusy || page === 1}
        >Précédent</button
      ><span>Page {page}</span><button
        class="web-button"
        type="button"
        onclick={() => history(page + 1)}
        disabled={historyBusy || !hasMore}>Suivant</button
      >
    </nav>{/if}
  <p class="web-help">Les échecs d’envoi sont visibles ici. Les paramètres secrets des canaux restent masqués.</p>
</section>

<style>
  .notification-panel {
    display: grid;
    gap: 1rem;
    min-width: 0;
  }
  .notification-panel p {
    margin: 0;
  }
  .channel-card {
    display: grid;
    gap: 1rem;
    border: 1px solid var(--web-border);
    border-radius: var(--web-radius, 6px);
    padding: 1rem;
    min-width: 0;
  }
  .channel-card legend {
    font-size: 0.8125rem;
    font-weight: 600;
    padding: 0 0.4rem;
  }
  .deployment-emails {
    border-inline-start: 3px solid var(--web-brand, #4878f8);
  }
  .event-choices {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.75rem;
  }
  .cooldown {
    max-width: 26rem;
  }
  .test-row {
    display: flex;
    flex-wrap: wrap;
    align-items: end;
    gap: 0.75rem;
  }
  .test-row .web-field {
    flex: 1 1 14rem;
    min-width: 0;
  }
  .test-result[data-tone='success'] {
    color: var(--web-success, inherit);
  }
  .secret {
    display: block;
    margin: 0.5rem 0;
    overflow-wrap: anywhere;
    user-select: all;
  }
  .notification-panel td[colspan] {
    white-space: normal;
  }
  @media (max-width: 750px) {
    .event-choices {
      grid-template-columns: 1fr;
    }
    .channel-card {
      padding: 0.8rem;
    }
  }
</style>
