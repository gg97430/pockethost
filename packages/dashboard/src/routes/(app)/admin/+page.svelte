<script lang="ts">
  import {
    client,
    type OperatorAdminOverview,
    type OperatorDiskCleanupResult,
    type OperatorSettings,
    type OperatorUser,
  } from '$src/pocketbase-client'
  import { userStore } from '$util/stores'

  type UserDraft = {
    email: string
    password: string
    verified: boolean
    superAdmin: boolean
    subscription: string
    subscription_quantity: number
    suspension: string
  }

  const subscriptionOptions = ['free', 'premium', 'founder', 'flounder', 'legacy']

  let hasLoaded = false
  let isLoading = false
  let isSavingSettings = false
  let isCreatingUser = false
  let isScanningDisk = false
  let isRunningDiskCleanup = false
  let isTestingBackupS3 = false
  let isTestingSMTP = false
  let savingUserId = ''
  let errorMessage = ''
  let successMessage = ''
  let searchQuery = ''
  let smtpTestEmail = ''
  let overview: OperatorAdminOverview | undefined
  let settings: OperatorSettings | undefined
  let diskCleanup: OperatorDiskCleanupResult | undefined
  let users: OperatorUser[] = []
  let userDrafts: Record<string, UserDraft> = {}
  let newUser: UserDraft = {
    email: '',
    password: '',
    verified: true,
    superAdmin: false,
    subscription: 'free',
    subscription_quantity: 250,
    suspension: '',
  }

  $: canAccessAdmin = !!$userStore?.superAdmin
  $: if (canAccessAdmin && !hasLoaded) {
    hasLoaded = true
    loadAdmin()
  }

  $: filteredUsers = users.filter((user) => {
    const target = `${user.email} ${user.username} ${user.suspension}`.toLowerCase()
    return target.includes(searchQuery.toLowerCase())
  })
  $: backupS3Ready =
    !!settings?.backupS3.enabled &&
    !!settings.backupS3.endpoint.trim() &&
    !!settings.backupS3.bucket.trim() &&
    !!settings.backupS3.accessKeyId.trim() &&
    (!!settings.backupS3.secretAccessKey?.trim() || settings.backupS3.hasSecretAccessKey)
  $: smtpReady =
    !!settings?.smtp.enabled &&
    !!settings.smtp.host.trim() &&
    Number(settings.smtp.port) > 0 &&
    !!settings.smtp.senderAddress.trim() &&
    (!!settings.smtp.password?.trim() || settings.smtp.hasPassword || !settings.smtp.username.trim())

  const applyOverview = (next: OperatorAdminOverview) => {
    overview = next
    settings = {
      ...next.settings,
      backupS3: {
        ...next.settings.backupS3,
        secretAccessKey: '',
      },
      smtp: {
        ...next.settings.smtp,
        password: '',
      },
    }
    smtpTestEmail = smtpTestEmail || next.settings.supportEmail || next.settings.smtp.senderAddress || ''
    users = next.users
    userDrafts = Object.fromEntries(users.map((user) => [user.id, toUserDraft(user)]))
    newUser = {
      ...newUser,
      verified: next.settings.autoVerifyUsers,
      subscription: next.settings.defaultSubscription,
      subscription_quantity: next.settings.defaultUserQuota,
    }
  }

  const toUserDraft = (user: OperatorUser): UserDraft => ({
    email: user.email,
    password: '',
    verified: user.verified,
    superAdmin: user.superAdmin,
    subscription: user.subscription || 'free',
    subscription_quantity: user.subscription_quantity,
    suspension: user.suspension || '',
  })

  const showError = (error: unknown) => {
    const message = error instanceof Error ? client().parseError(error)[0] || error.message : `${error}`
    errorMessage = message || 'Erreur inconnue'
    successMessage = ''
  }

  const showSuccess = (message: string) => {
    successMessage = message
    errorMessage = ''
  }

  const formatBytes = (bytes: number) => {
    if (!Number.isFinite(bytes) || bytes <= 0) return '0 o'
    if (bytes < 1024) return `${bytes} o`
    if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} Ko`
    if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} Mo`
    return `${(bytes / 1024 ** 3).toFixed(2)} Go`
  }

  async function loadAdmin() {
    isLoading = true
    errorMessage = ''
    try {
      applyOverview(await client().getOperatorAdminOverview())
    } catch (error) {
      showError(error)
    } finally {
      isLoading = false
    }
  }

  async function previewDiskCleanup() {
    isScanningDisk = true
    try {
      const result = await client().previewOperatorDiskCleanup()
      diskCleanup = result.cleanup
      showSuccess(
        result.cleanup.orphanCount
          ? `${result.cleanup.orphanCount} element(s) orphelin(s) detecte(s).`
          : 'Aucun dossier orphelin detecte.'
      )
    } catch (error) {
      showError(error)
    } finally {
      isScanningDisk = false
    }
  }

  async function runDiskCleanup() {
    isRunningDiskCleanup = true
    try {
      const result = await client().runOperatorDiskCleanup()
      diskCleanup = result.cleanup
      showSuccess(
        `${result.cleanup.removedCount} element(s) supprime(s), ${formatBytes(result.cleanup.freedBytes)} liberes.`
      )
    } catch (error) {
      showError(error)
    } finally {
      isRunningDiskCleanup = false
    }
  }

  async function createUser(event: SubmitEvent) {
    event.preventDefault()
    isCreatingUser = true
    try {
      const { user } = await client().createOperatorUser(newUser)
      users = [user, ...users]
      userDrafts = { ...userDrafts, [user.id]: toUserDraft(user) }
      overview = overview
        ? {
            ...overview,
            stats: {
              ...overview.stats,
              totalUsers: overview.stats.totalUsers + 1,
              verifiedUsers: overview.stats.verifiedUsers + (user.verified ? 1 : 0),
              superAdmins: overview.stats.superAdmins + (user.superAdmin ? 1 : 0),
              suspendedUsers: overview.stats.suspendedUsers + (user.suspension ? 1 : 0),
            },
          }
        : overview
      newUser = {
        email: '',
        password: '',
        verified: settings?.autoVerifyUsers ?? true,
        superAdmin: false,
        subscription: settings?.defaultSubscription ?? 'free',
        subscription_quantity: settings?.defaultUserQuota ?? 250,
        suspension: '',
      }
      showSuccess('Compte cree.')
    } catch (error) {
      showError(error)
    } finally {
      isCreatingUser = false
    }
  }

  async function saveUser(userId: string) {
    savingUserId = userId
    try {
      const draft = userDrafts[userId]
      if (!draft) throw new Error('Brouillon utilisateur introuvable.')
      const payload = {
        ...draft,
        password: draft.password.trim() ? draft.password : undefined,
      }
      const { user } = await client().updateOperatorUser(userId, payload)
      users = users.map((item) => (item.id === user.id ? user : item))
      userDrafts = { ...userDrafts, [user.id]: toUserDraft(user) }
      showSuccess('Utilisateur mis a jour.')
      await loadAdmin()
    } catch (error) {
      showError(error)
    } finally {
      savingUserId = ''
    }
  }

  async function saveSettings(event: SubmitEvent) {
    event.preventDefault()
    if (!settings) return
    isSavingSettings = true
    try {
      const result = await client().updateOperatorSettings(settings)
      settings = {
        ...result.settings,
        backupS3: {
          ...result.settings.backupS3,
          secretAccessKey: '',
        },
        smtp: {
          ...result.settings.smtp,
          password: '',
        },
      }
      showSuccess('Parametres enregistres.')
    } catch (error) {
      showError(error)
    } finally {
      isSavingSettings = false
    }
  }

  async function testBackupS3() {
    if (!settings) return
    isTestingBackupS3 = true
    try {
      const result = await client().testOperatorBackupS3(settings)
      showSuccess(result.test.message || 'Connexion S3/R2 valide.')
    } catch (error) {
      showError(error)
    } finally {
      isTestingBackupS3 = false
    }
  }

  async function testSMTP() {
    if (!settings) return
    isTestingSMTP = true
    try {
      const result = await client().testOperatorSMTP(settings, smtpTestEmail)
      settings = {
        ...result.settings,
        backupS3: {
          ...result.settings.backupS3,
          secretAccessKey: '',
        },
        smtp: {
          ...result.settings.smtp,
          password: '',
        },
      }
      smtpTestEmail = result.test.to
      showSuccess(result.test.message || 'Email SMTP envoye.')
    } catch (error) {
      showError(error)
    } finally {
      isTestingSMTP = false
    }
  }
</script>

<svelte:head>
  <title>Administration - Gestion PocketBase</title>
</svelte:head>

{#if !canAccessAdmin}
  <section class="admin-forbidden">
    <wa-icon name="shield-halved"></wa-icon>
    <h1>Acces superadmin requis</h1>
    <p>Ce backoffice est reserve aux comptes operateurs.</p>
  </section>
{:else}
  <header class="admin-head">
    <div>
      <h1>Administration</h1>
      <p>Utilisateurs, quotas et parametres operateur.</p>
    </div>
    <button class="admin-icon-btn" type="button" onclick={loadAdmin} aria-label="Rafraichir" disabled={isLoading}>
      <wa-icon name="rotate"></wa-icon>
    </button>
  </header>

  {#if errorMessage}
    <wa-callout variant="danger" class="admin-callout">
      <wa-icon slot="icon" name="circle-exclamation"></wa-icon>
      <span>{errorMessage}</span>
    </wa-callout>
  {/if}

  {#if successMessage}
    <wa-callout variant="success" class="admin-callout">
      <wa-icon slot="icon" name="circle-check"></wa-icon>
      <span>{successMessage}</span>
    </wa-callout>
  {/if}

  {#if isLoading && !overview}
    <div class="admin-loading">Chargement...</div>
  {:else if overview && settings}
    <section class="admin-kpis" aria-label="Indicateurs backoffice">
      <div class="admin-kpi">
        <span>Utilisateurs</span>
        <strong>{overview.stats.totalUsers}</strong>
      </div>
      <div class="admin-kpi">
        <span>Verifies</span>
        <strong>{overview.stats.verifiedUsers}</strong>
      </div>
      <div class="admin-kpi">
        <span>Instances</span>
        <strong>{overview.stats.totalInstances}</strong>
      </div>
      <div class="admin-kpi">
        <span>Superadmins</span>
        <strong>{overview.stats.superAdmins}</strong>
      </div>
    </section>

    <section class="admin-grid">
      <form class="admin-panel admin-create" onsubmit={createUser}>
        <div class="admin-panel-head">
          <h2>Nouveau compte</h2>
        </div>
        <label>
          Email
          <input type="email" bind:value={newUser.email} required autocomplete="off" />
        </label>
        <label>
          Mot de passe
          <input type="password" bind:value={newUser.password} required minlength="8" autocomplete="new-password" />
        </label>
        <div class="admin-form-row">
          <label>
            Offre
            <select bind:value={newUser.subscription}>
              {#each subscriptionOptions as option}
                <option value={option}>{option}</option>
              {/each}
            </select>
          </label>
          <label>
            Quota
            <input type="number" min="0" step="1" bind:value={newUser.subscription_quantity} />
          </label>
        </div>
        <div class="admin-checks">
          <label><input type="checkbox" bind:checked={newUser.verified} /> Verifie</label>
          <label><input type="checkbox" bind:checked={newUser.superAdmin} /> Superadmin</label>
        </div>
        <label>
          Suspension
          <input type="text" bind:value={newUser.suspension} placeholder="vide = actif" />
        </label>
        <button class="admin-primary-btn" type="submit" disabled={isCreatingUser}>
          <wa-icon name="user-plus"></wa-icon>
          {isCreatingUser ? 'Creation...' : 'Creer le compte'}
        </button>
      </form>

      <form class="admin-panel admin-settings" onsubmit={saveSettings}>
        <div class="admin-panel-head">
          <h2>Parametres</h2>
        </div>
        <div class="admin-checks admin-checks--grid">
          <label><input type="checkbox" bind:checked={settings.publicSignupEnabled} /> Inscription publique</label>
          <label><input type="checkbox" bind:checked={settings.autoVerifyUsers} /> Verification auto</label>
          <label><input type="checkbox" bind:checked={settings.defaultInstancePower} /> Instances actives</label>
          <label><input type="checkbox" bind:checked={settings.defaultInstanceDevMode} /> Mode dev</label>
          <label><input type="checkbox" bind:checked={settings.defaultSyncAdmin} /> Synchro admin</label>
          <label><input type="checkbox" bind:checked={settings.defaultAutoVacuum} /> Nettoyage auto</label>
        </div>
        <div class="admin-form-row">
          <label>
            Quota par defaut
            <input type="number" min="0" step="1" bind:value={settings.defaultUserQuota} />
          </label>
          <label>
            Offre par defaut
            <select bind:value={settings.defaultSubscription}>
              {#each subscriptionOptions as option}
                <option value={option}>{option}</option>
              {/each}
            </select>
          </label>
        </div>
        <label>
          Fuseau horaire serveur
          <input
            type="text"
            bind:value={settings.serverTimezone}
            list="admin-timezone-options"
            placeholder="Indian/Reunion"
          />
          <span class="admin-field-help"
            >Utilise par les sauvegardes automatiques et les taches planifiees serveur.</span
          >
          <datalist id="admin-timezone-options">
            <option value="Indian/Reunion"></option>
            <option value="Europe/Paris"></option>
            <option value="UTC"></option>
          </datalist>
        </label>
        <div class="admin-s3-box">
          <div class="admin-s3-head">
            <div>
              <h3>Stockage S3/R2 des sauvegardes</h3>
              <p>Destination globale utilisee par les sauvegardes planifiees quand S3/R2 est coche.</p>
            </div>
            <span class:ready={backupS3Ready} class="admin-s3-status">
              {backupS3Ready ? 'Pret' : 'Incomplet'}
            </span>
          </div>
          <label class="admin-inline-check admin-s3-toggle">
            <input type="checkbox" bind:checked={settings.backupS3.enabled} />
            Activer S3/R2 pour les sauvegardes planifiees
          </label>
          <div class="admin-form-row">
            <label>
              Endpoint
              <input
                type="url"
                bind:value={settings.backupS3.endpoint}
                placeholder="https://<account-id>.r2.cloudflarestorage.com"
              />
            </label>
            <label>
              Bucket
              <input type="text" bind:value={settings.backupS3.bucket} placeholder="mon-bucket" />
            </label>
          </div>
          <div class="admin-form-row">
            <label>
              Prefixe
              <input type="text" bind:value={settings.backupS3.prefix} placeholder="instances" />
              <span class="admin-field-help">Chemin de base. Exemple final: prefixe/instance/archive.tar.gz.</span>
            </label>
            <label>
              Region
              <input type="text" bind:value={settings.backupS3.region} placeholder="auto" />
              <span class="admin-field-help">Pour Cloudflare R2, gardez generalement auto.</span>
            </label>
          </div>
          <div class="admin-form-row">
            <label>
              Access key ID
              <input type="text" bind:value={settings.backupS3.accessKeyId} autocomplete="off" />
            </label>
            <label>
              Secret access key
              <input
                type="password"
                bind:value={settings.backupS3.secretAccessKey}
                autocomplete="new-password"
                placeholder={settings.backupS3.hasSecretAccessKey ? 'Deja enregistree' : 'Secret access key'}
              />
              <span class="admin-field-help">
                {settings.backupS3.hasSecretAccessKey
                  ? 'Laissez vide pour conserver la cle existante.'
                  : 'Requise pour activer S3/R2.'}
              </span>
            </label>
          </div>
          <div class="admin-s3-actions">
            <button
              class="admin-secondary-btn"
              type="button"
              onclick={testBackupS3}
              disabled={isTestingBackupS3 || isSavingSettings || !settings.backupS3.enabled}
            >
              <wa-icon name={isTestingBackupS3 ? 'rotate' : 'plug-circle-check'}></wa-icon>
              {isTestingBackupS3 ? 'Test...' : 'Tester S3/R2'}
            </button>
          </div>
        </div>
        <div class="admin-s3-box">
          <div class="admin-s3-head">
            <div>
              <h3>SMTP des emails</h3>
              <p>Configuration utilisee pour les emails de verification, reset mot de passe et notifications.</p>
            </div>
            <span class:ready={smtpReady} class="admin-s3-status">
              {smtpReady ? 'Pret' : 'Incomplet'}
            </span>
          </div>
          <label class="admin-inline-check admin-s3-toggle">
            <input type="checkbox" bind:checked={settings.smtp.enabled} />
            Activer SMTP
          </label>
          <div class="admin-form-row">
            <label>
              Hote SMTP
              <input type="text" bind:value={settings.smtp.host} placeholder="smtp.mailgun.org" />
            </label>
            <label>
              Port
              <input type="number" min="1" max="65535" step="1" bind:value={settings.smtp.port} />
            </label>
          </div>
          <div class="admin-form-row">
            <label>
              Expediteur nom
              <input type="text" bind:value={settings.smtp.senderName} placeholder="Gestion PocketBase" />
            </label>
            <label>
              Expediteur email
              <input type="email" bind:value={settings.smtp.senderAddress} placeholder="no-reply@monappli.re" />
            </label>
          </div>
          <div class="admin-form-row">
            <label>
              Utilisateur
              <input type="text" bind:value={settings.smtp.username} autocomplete="off" />
            </label>
            <label>
              Mot de passe SMTP
              <input
                type="password"
                bind:value={settings.smtp.password}
                autocomplete="new-password"
                placeholder={settings.smtp.hasPassword ? 'Deja enregistre' : 'Mot de passe SMTP'}
              />
              <span class="admin-field-help">
                {settings.smtp.hasPassword
                  ? 'Laissez vide pour conserver le mot de passe existant.'
                  : 'Requis si votre SMTP demande une authentification.'}
              </span>
            </label>
          </div>
          <div class="admin-form-row">
            <label>
              Methode auth
              <select bind:value={settings.smtp.authMethod}>
                <option value="PLAIN">PLAIN</option>
                <option value="LOGIN">LOGIN</option>
              </select>
            </label>
            <label>
              Local name
              <input type="text" bind:value={settings.smtp.localName} placeholder="monappli.re" />
              <span class="admin-field-help">Optionnel. Utile pour certains relais SMTP.</span>
            </label>
          </div>
          <label class="admin-inline-check admin-s3-toggle">
            <input type="checkbox" bind:checked={settings.smtp.tls} />
            Forcer TLS direct
          </label>
          <label>
            Email de test
            <input type="email" bind:value={smtpTestEmail} placeholder="admin@monappli.re" />
          </label>
          <div class="admin-s3-actions">
            <button
              class="admin-secondary-btn"
              type="button"
              onclick={testSMTP}
              disabled={isTestingSMTP || isSavingSettings || !settings.smtp.enabled}
            >
              <wa-icon name={isTestingSMTP ? 'rotate' : 'paper-plane'}></wa-icon>
              {isTestingSMTP ? 'Envoi...' : 'Tester SMTP'}
            </button>
          </div>
        </div>
        <label>
          Email support
          <input type="email" bind:value={settings.supportEmail} placeholder="support@monappli.re" />
        </label>
        <label>
          Message maintenance
          <textarea rows="2" bind:value={settings.maintenanceMessage}></textarea>
        </label>
        <label>
          Notes internes
          <textarea rows="3" bind:value={settings.notes}></textarea>
        </label>
        <button class="admin-primary-btn" type="submit" disabled={isSavingSettings}>
          <wa-icon name="floppy-disk"></wa-icon>
          {isSavingSettings ? 'Enregistrement...' : 'Enregistrer'}
        </button>
      </form>
    </section>

    <section class="admin-panel admin-disk-cleanup">
      <div class="admin-disk-head">
        <div>
          <h2>Nettoyage disque</h2>
          <p>Supprime les dossiers locaux qui n'ont plus de record d'instance ni de conteneur Docker.</p>
        </div>
        <div class="admin-disk-actions">
          <button
            class="admin-secondary-btn"
            type="button"
            onclick={previewDiskCleanup}
            disabled={isScanningDisk || isRunningDiskCleanup}
          >
            <wa-icon name="magnifying-glass-chart"></wa-icon>
            {isScanningDisk ? 'Analyse...' : 'Analyser'}
          </button>
          <button
            class="admin-danger-btn"
            type="button"
            onclick={runDiskCleanup}
            disabled={isScanningDisk || isRunningDiskCleanup || !diskCleanup?.orphanCount}
          >
            <wa-icon name="trash"></wa-icon>
            {isRunningDiskCleanup ? 'Nettoyage...' : 'Nettoyer'}
          </button>
        </div>
      </div>

      {#if diskCleanup}
        <div class="admin-disk-summary">
          <div>
            <span>Orphelins</span>
            <strong>{diskCleanup.orphanCount}</strong>
          </div>
          <div>
            <span>Espace detectable</span>
            <strong>{formatBytes(diskCleanup.totalBytes)}</strong>
          </div>
          <div>
            <span>Supprimes</span>
            <strong>{diskCleanup.removedCount}</strong>
          </div>
          <div>
            <span>Libere</span>
            <strong>{formatBytes(diskCleanup.freedBytes)}</strong>
          </div>
        </div>

        {#if diskCleanup.entries.length}
          <div class="admin-disk-list">
            {#each diskCleanup.entries as entry}
              <div class:error={entry.error} class:removed={entry.removed}>
                <span>{entry.kind}</span>
                <strong>{entry.id}</strong>
                <small>{formatBytes(entry.sizeBytes)} · {entry.removed ? 'supprime' : entry.error || 'pret'}</small>
              </div>
            {/each}
          </div>
        {/if}
      {/if}
    </section>

    <section class="admin-panel admin-users">
      <div class="admin-users-toolbar">
        <div>
          <h2>Comptes utilisateurs</h2>
          <p>{filteredUsers.length} affiche{filteredUsers.length > 1 ? 's' : ''}</p>
        </div>
        <label class="admin-search">
          <wa-icon name="magnifying-glass"></wa-icon>
          <input type="search" bind:value={searchQuery} placeholder="Rechercher..." />
        </label>
      </div>

      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead>
            <tr>
              <th>Email</th>
              <th>Quota</th>
              <th>Etat</th>
              <th>Role</th>
              <th>Instances</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {#each filteredUsers as user (user.id)}
              {@const draft = userDrafts[user.id]}
              {#if draft}
                <tr>
                  <td>
                    <input class="admin-table-input admin-table-input--email" type="email" bind:value={draft.email} />
                    <input
                      class="admin-table-input"
                      type="password"
                      bind:value={draft.password}
                      placeholder="nouveau mot de passe"
                    />
                  </td>
                  <td>
                    <input
                      class="admin-table-input admin-table-input--number"
                      type="number"
                      min="0"
                      step="1"
                      bind:value={draft.subscription_quantity}
                    />
                    <select class="admin-table-input" bind:value={draft.subscription}>
                      {#each subscriptionOptions as option}
                        <option value={option}>{option}</option>
                      {/each}
                    </select>
                  </td>
                  <td>
                    <label class="admin-inline-check">
                      <input type="checkbox" bind:checked={draft.verified} />
                      Verifie
                    </label>
                    <input
                      class="admin-table-input"
                      type="text"
                      bind:value={draft.suspension}
                      placeholder="suspension"
                    />
                  </td>
                  <td>
                    <label class="admin-inline-check">
                      <input type="checkbox" bind:checked={draft.superAdmin} />
                      Superadmin
                    </label>
                  </td>
                  <td>
                    <span class="admin-instance-count">{user.instanceCount}</span>
                  </td>
                  <td class="admin-table-actions">
                    <button
                      class="admin-secondary-btn"
                      type="button"
                      onclick={() => saveUser(user.id)}
                      disabled={savingUserId === user.id}
                    >
                      <wa-icon name="floppy-disk"></wa-icon>
                      {savingUserId === user.id ? '...' : 'OK'}
                    </button>
                  </td>
                </tr>
              {/if}
            {/each}
          </tbody>
        </table>
      </div>
    </section>
  {/if}
{/if}

<style>
  .admin-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 1.5rem;
  }

  .admin-head h1,
  .admin-panel h2,
  .admin-users-toolbar h2 {
    margin: 0;
    color: var(--app-text-strong);
  }

  .admin-head h1 {
    font-size: 1.875rem;
    font-weight: 750;
  }

  .admin-head p,
  .admin-users-toolbar p {
    margin: 0.35rem 0 0;
    color: var(--app-text-muted);
    font-size: 0.875rem;
  }

  .admin-callout {
    margin-bottom: 1rem;
  }

  .admin-forbidden,
  .admin-loading {
    display: grid;
    place-items: center;
    min-height: 22rem;
    text-align: center;
    color: var(--app-text-muted);
  }

  .admin-forbidden wa-icon {
    margin-bottom: 1rem;
    font-size: 2rem;
    color: #1eb854;
  }

  .admin-forbidden h1 {
    margin: 0;
    color: var(--app-text-strong);
    font-size: 1.25rem;
  }

  .admin-forbidden p {
    margin: 0.5rem 0 0;
  }

  .admin-icon-btn,
  .admin-primary-btn,
  .admin-danger-btn,
  .admin-secondary-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 0.45rem;
    border: 1px solid transparent;
    border-radius: 0.5rem;
    font-weight: 700;
    cursor: pointer;
    transition:
      background-color 120ms ease,
      border-color 120ms ease,
      color 120ms ease;
  }

  .admin-icon-btn {
    width: 2.25rem;
    height: 2.25rem;
    background: var(--app-surface);
    color: var(--app-text);
    border-color: var(--app-border);
  }

  .admin-primary-btn {
    min-height: 2.5rem;
    padding: 0 1rem;
    background: #1eb854;
    color: #ffffff;
    box-shadow: 0 12px 24px rgb(30 184 84 / 0.18);
  }

  .admin-secondary-btn {
    min-height: 2rem;
    padding: 0 0.65rem;
    background: var(--app-surface-soft);
    border-color: var(--app-border);
    color: var(--app-text-strong);
  }

  .admin-danger-btn {
    min-height: 2rem;
    padding: 0 0.75rem;
    background: rgb(220 38 38 / 0.12);
    border-color: rgb(220 38 38 / 0.35);
    color: #ef4444;
  }

  button:disabled {
    opacity: 0.55;
    cursor: wait;
  }

  .admin-kpis {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0.75rem;
    margin-bottom: 1rem;
  }

  .admin-kpi,
  .admin-panel {
    border: 1px solid var(--app-border);
    border-radius: 0.5rem;
    background: var(--app-surface);
    box-shadow: var(--app-shadow-sm);
  }

  .admin-kpi {
    padding: 1rem;
  }

  .admin-kpi span {
    display: block;
    color: var(--app-text-muted);
    font-size: 0.7rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }

  .admin-kpi strong {
    display: block;
    margin-top: 0.4rem;
    color: var(--app-text-strong);
    font-size: 1.75rem;
    line-height: 1;
  }

  .admin-grid {
    display: grid;
    grid-template-columns: 1fr;
    gap: 1rem;
    margin-bottom: 1rem;
  }

  .admin-panel {
    padding: 1rem;
  }

  .admin-panel-head {
    margin-bottom: 1rem;
  }

  .admin-panel h2,
  .admin-users-toolbar h2 {
    font-size: 1rem;
    font-weight: 750;
  }

  .admin-panel h3 {
    margin: 0;
    color: var(--app-text-strong);
    font-size: 0.95rem;
    font-weight: 750;
  }

  .admin-panel label {
    display: grid;
    gap: 0.35rem;
    color: var(--app-text-muted);
    font-size: 0.75rem;
    font-weight: 700;
  }

  .admin-panel input,
  .admin-panel select,
  .admin-panel textarea,
  .admin-search input,
  .admin-table-input {
    width: 100%;
    min-width: 0;
    border: 1px solid var(--app-border);
    border-radius: 0.45rem;
    background: var(--app-surface-strong);
    color: var(--app-text-strong);
    font-size: 0.875rem;
    outline: none;
  }

  .admin-panel input,
  .admin-panel select,
  .admin-panel textarea {
    padding: 0.62rem 0.7rem;
  }

  .admin-panel textarea {
    resize: vertical;
  }

  .admin-field-help {
    color: var(--app-text-faint);
    font-size: 0.72rem;
    font-weight: 600;
    line-height: 1.35;
  }

  .admin-s3-box {
    display: grid;
    gap: 0.8rem;
    padding: 0.85rem;
    border: 1px solid var(--app-border);
    border-radius: 0.5rem;
    background: var(--app-surface-soft);
  }

  .admin-s3-head {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-start;
    justify-content: space-between;
    gap: 0.75rem;
  }

  .admin-s3-head p {
    margin: 0.25rem 0 0;
    color: var(--app-text-muted);
    font-size: 0.78rem;
    line-height: 1.4;
  }

  .admin-s3-status {
    display: inline-flex;
    align-items: center;
    min-height: 1.65rem;
    padding: 0 0.65rem;
    border: 1px solid rgb(234 179 8 / 0.35);
    border-radius: 999px;
    background: rgb(234 179 8 / 0.1);
    color: #f59e0b;
    font-size: 0.72rem;
    font-weight: 800;
  }

  .admin-s3-status.ready {
    border-color: rgb(30 184 84 / 0.35);
    background: rgb(30 184 84 / 0.12);
    color: #1eb854;
  }

  .admin-s3-toggle {
    margin: 0;
  }

  .admin-s3-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }

  .admin-create,
  .admin-settings {
    display: grid;
    align-content: start;
    gap: 0.8rem;
  }

  .admin-form-row {
    display: grid;
    grid-template-columns: 1fr;
    gap: 0.75rem;
  }

  .admin-checks {
    display: flex;
    flex-wrap: wrap;
    gap: 0.75rem;
  }

  .admin-checks--grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .admin-checks label,
  .admin-inline-check {
    display: inline-flex;
    grid-template-columns: unset;
    align-items: center;
    gap: 0.45rem;
    color: var(--app-text);
    font-size: 0.8125rem;
    font-weight: 650;
  }

  .admin-checks input,
  .admin-inline-check input {
    width: 1rem;
    height: 1rem;
    accent-color: #1eb854;
  }

  .admin-users {
    margin-bottom: 2rem;
  }

  .admin-disk-cleanup {
    margin-bottom: 1rem;
  }

  .admin-disk-head {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-start;
    justify-content: space-between;
    gap: 1rem;
  }

  .admin-disk-head p {
    margin: 0.35rem 0 0;
    color: var(--app-text-muted);
    font-size: 0.8125rem;
    line-height: 1.45;
  }

  .admin-disk-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }

  .admin-disk-summary {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0.75rem;
    margin-top: 1rem;
  }

  .admin-disk-summary div,
  .admin-disk-list > div {
    border: 1px solid var(--app-border);
    border-radius: 0.5rem;
    background: var(--app-surface-soft);
  }

  .admin-disk-summary div {
    padding: 0.8rem;
  }

  .admin-disk-summary span,
  .admin-disk-list span {
    display: block;
    color: var(--app-text-muted);
    font-size: 0.68rem;
    font-weight: 750;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }

  .admin-disk-summary strong {
    display: block;
    margin-top: 0.35rem;
    color: var(--app-text-strong);
    font-size: 1.2rem;
  }

  .admin-disk-list {
    display: grid;
    gap: 0.55rem;
    margin-top: 0.85rem;
  }

  .admin-disk-list > div {
    display: grid;
    gap: 0.25rem;
    padding: 0.75rem;
  }

  .admin-disk-list strong {
    color: var(--app-text-strong);
    font-size: 0.95rem;
  }

  .admin-disk-list small {
    color: var(--app-text-muted);
    font-size: 0.78rem;
  }

  .admin-disk-list > div.removed {
    border-color: rgb(30 184 84 / 0.4);
    background: rgb(30 184 84 / 0.08);
  }

  .admin-disk-list > div.error {
    border-color: rgb(220 38 38 / 0.4);
    background: rgb(220 38 38 / 0.08);
  }

  .admin-users-toolbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    margin-bottom: 1rem;
  }

  .admin-search {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    min-width: min(100%, 18rem);
    padding: 0.5rem 0.75rem;
    border: 1px solid var(--app-border);
    border-radius: 0.5rem;
    background: var(--app-surface-strong);
    color: var(--app-text-faint);
  }

  .admin-search input {
    border: none;
    padding: 0;
    background: transparent;
  }

  .admin-table-wrap {
    overflow-x: auto;
  }

  .admin-table {
    width: 100%;
    min-width: 58rem;
    border-collapse: collapse;
    font-size: 0.875rem;
  }

  .admin-table th {
    padding: 0.65rem 0.75rem;
    border-bottom: 1px solid var(--app-border);
    background: var(--app-surface-soft);
    color: var(--app-text-muted);
    font-size: 0.68rem;
    font-weight: 750;
    letter-spacing: 0.06em;
    text-align: left;
    text-transform: uppercase;
  }

  .admin-table td {
    padding: 0.75rem;
    border-bottom: 1px solid var(--app-border);
    vertical-align: top;
  }

  .admin-table-input {
    display: block;
    margin-bottom: 0.45rem;
    padding: 0.48rem 0.55rem;
  }

  .admin-table-input--email {
    min-width: 15rem;
  }

  .admin-table-input--number {
    max-width: 7rem;
  }

  .admin-inline-check {
    margin-bottom: 0.55rem;
  }

  .admin-instance-count {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 2rem;
    height: 2rem;
    border-radius: 999px;
    background: rgb(30 184 84 / 0.13);
    color: #15803d;
    font-weight: 800;
  }

  .admin-table-actions {
    text-align: right;
  }

  @media (min-width: 768px) {
    .admin-kpis {
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }

    .admin-grid {
      grid-template-columns: minmax(18rem, 0.85fr) minmax(24rem, 1.15fr);
    }

    .admin-form-row {
      grid-template-columns: 1fr 0.7fr;
    }

    .admin-disk-summary {
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }
  }
</style>
