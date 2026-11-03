<script lang="ts">
  import {
    client,
    type OperatorAdminOverview,
    type OperatorDiskCleanupResult,
    type OperatorSettings,
    type OperatorUser,
    type OperatorWebProjectAssignment,
  } from '$src/pocketbase-client'
  import { userStore } from '$util/stores'
  import UserWebProjectAccess from '$components/UserWebProjectAccess.svelte'
  import UserInstanceAccess from '$components/UserInstanceAccess.svelte'
  import RequestLimitsAdmin from '$components/RequestLimitsAdmin.svelte'

  type UserDraft = {
    email: string
    password: string
    verified: boolean
    superAdmin: boolean
    subscription: string
    subscription_quantity: number
    suspension: string
    managedInstances: string[]
    webProjects: OperatorWebProjectAssignment[]
  }

  const subscriptionOptions = ['free', 'premium', 'founder', 'flounder', 'legacy']

  let hasLoaded = false
  let isLoading = false
  let isSavingSettings = false
  let isCreatingUser = false
  let sendWelcomeEmail = false
  let isScanningDisk = false
  let isRunningDiskCleanup = false
  let isTestingBackupS3 = false
  let isTestingSMTP = false
  let savingUserId = ''
  let editingUserId = ''
  let adminSection: 'users' | 'settings' | 'limits' | 'maintenance' = 'users'
  let showCreateUser = false
  let userStatus: 'all' | 'active' | 'suspended' | 'unverified' = 'all'
  let userRole: 'all' | 'user' | 'superuser' = 'all'
  let errorMessage = ''
  let successMessage = ''
  let searchQuery = ''
  let userPage = 1
  let cleanupConfirmation = false
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
    managedInstances: [],
    webProjects: [],
  }

  $: canAccessAdmin = !!$userStore?.superAdmin && !$userStore?.suspension
  $: if (canAccessAdmin && !hasLoaded) {
    hasLoaded = true
    loadAdmin()
  }

  $: canManageWebAccess = Array.isArray(overview?.webProjects)
  $: canManageInstanceAccess = Array.isArray(overview?.instances)

  $: filteredUsers = users.filter((user) => {
    const target = `${user.email} ${user.username} ${user.suspension}`.toLowerCase()
    const matchesStatus =
      userStatus === 'all' ||
      (userStatus === 'active' && !user.suspension && user.verified) ||
      (userStatus === 'suspended' && !!user.suspension) ||
      (userStatus === 'unverified' && !user.verified)
    const matchesRole = userRole === 'all' || (userRole === 'superuser' ? user.superAdmin : !user.superAdmin)
    return target.includes(searchQuery.toLowerCase()) && matchesStatus && matchesRole
  })
  $: userPages = Math.max(1, Math.ceil(filteredUsers.length / 25))
  $: if (userPage > userPages) userPage = userPages
  $: visibleUsers = filteredUsers.slice((userPage - 1) * 25, userPage * 25)
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
    managedInstances: [...(user.managedInstances || [])],
    webProjects: (user.webProjects || []).map((project) => ({ ...project })),
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
    cleanupConfirmation = false
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
    if (!diskCleanup) return
    cleanupConfirmation = false
    isRunningDiskCleanup = true
    try {
      const result = await client().runOperatorDiskCleanup(diskCleanup.snapshot)
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
      const { managedInstances, webProjects, ...account } = newUser
      const { user, warning, welcomeEmailSent } = await client().createOperatorUser({
        ...account,
        sendWelcomeEmail,
        ...(canManageInstanceAccess ? { managedInstances } : {}),
        ...(canManageWebAccess ? { webProjects } : {}),
      })
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
        managedInstances: [],
        webProjects: [],
      }
      sendWelcomeEmail = false
      showSuccess(
        welcomeEmailSent
          ? 'Compte créé. Le mail contenant les identifiants a été accepté par le serveur SMTP.'
          : 'Compte créé.'
      )
      if (warning) errorMessage = warning
      showCreateUser = false
    } catch (error) {
      showError(error)
    } finally {
      isCreatingUser = false
    }
  }

  function editUser(user: OperatorUser) {
    userDrafts = { ...userDrafts, [user.id]: toUserDraft(user) }
    editingUserId = user.id
  }

  function cancelUserEdit(user: OperatorUser) {
    userDrafts = { ...userDrafts, [user.id]: toUserDraft(user) }
    editingUserId = ''
  }

  async function saveUser(userId: string) {
    savingUserId = userId
    try {
      const draft = userDrafts[userId]
      if (!draft) throw new Error('Brouillon utilisateur introuvable.')
      const { managedInstances, webProjects, ...account } = draft
      const payload = {
        ...account,
        ...(canManageInstanceAccess ? { managedInstances } : {}),
        ...(canManageWebAccess ? { webProjects } : {}),
        password: draft.password.trim() ? draft.password : undefined,
      }
      const { user } = await client().updateOperatorUser(userId, payload)
      users = users.map((item) => (item.id === user.id ? user : item))
      userDrafts = { ...userDrafts, [user.id]: toUserDraft(user) }
      const auth = client().client.authStore.record
      if (auth && user.id === auth.id)
        client().client.authStore.save(client().client.authStore.token, { ...auth, ...user })
      showSuccess('Utilisateur mis à jour.')
      editingUserId = ''
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
    if (!validateSMTPForm(true)) return
    isTestingSMTP = true
    try {
      const result = await client().testOperatorSMTP(settings, smtpTestEmail)
      smtpTestEmail = result.test.to
      showSuccess(
        (result.test.message || 'Email de test envoyé.') + ' Le test ne modifie pas les paramètres enregistrés.'
      )
    } catch (error) {
      showError(error)
    } finally {
      isTestingSMTP = false
    }
  }
  function validateSMTPForm(includeRecipient = false) {
    const inputs = document.querySelectorAll<HTMLInputElement>('.admin-smtp-box input')
    for (const input of inputs) {
      if (!includeRecipient && input.id === 'smtp-test-email') continue
      if (!input.reportValidity()) return false
    }
    return true
  }
  async function saveSMTP() {
    if (!settings || !validateSMTPForm()) return
    isSavingSettings = true
    try {
      const result = await client().updateOperatorSettings({ smtp: settings.smtp })
      settings = { ...settings, smtp: { ...result.settings.smtp, password: '' } }
      showSuccess('Paramètres SMTP enregistrés. Vous pouvez envoyer un email de test.')
    } catch (error) {
      showError(error)
    } finally {
      isSavingSettings = false
    }
  }
</script>

<svelte:head>
  <title>Administration - Kolab Hosting</title>
</svelte:head>

<div class="admin-console">
  {#if !canAccessAdmin}
    <section class="admin-forbidden">
      <wa-icon name="shield-halved"></wa-icon>
      <h1>Acces superadmin requis</h1>
      <p>Ce backoffice est reserve aux comptes operateurs.</p>
    </section>
  {:else}
    <header class="admin-head">
      <div>
        <span class="admin-eyebrow">Plateforme</span>
        <h1>Administration</h1>
        <p>Comptes, accès aux bases et configuration du serveur.</p>
      </div>
      <div class="admin-head-actions">
        <button class="admin-secondary-btn" type="button" onclick={loadAdmin} disabled={isLoading || !!editingUserId}>
          <wa-icon name="rotate" class:admin-spinning={isLoading}></wa-icon>
          Actualiser
        </button>
        <button
          class="admin-primary-btn"
          type="button"
          disabled={!!editingUserId}
          aria-expanded={showCreateUser && adminSection === 'users'}
          onclick={() => {
            adminSection = 'users'
            showCreateUser = !showCreateUser
          }}
        >
          <wa-icon name="plus"></wa-icon>
          Nouveau compte
        </button>
      </div>
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
          <span>Comptes vérifiés</span>
          <strong>{overview.stats.verifiedUsers}</strong>
        </div>
        <div class="admin-kpi">
          <span>Instances</span>
          <strong>{overview.stats.totalInstances}</strong>
        </div>
        <div class="admin-kpi">
          <span>Superusers</span>
          <strong>{overview.stats.superAdmins}</strong>
        </div>
      </section>

      <nav class="admin-section-nav" aria-label="Sections de l’administration">
        <button
          type="button"
          class:active={adminSection === 'users'}
          aria-current={adminSection === 'users' ? 'page' : undefined}
          onclick={() => (adminSection = 'users')}
        >
          <wa-icon name="users"></wa-icon> Comptes <span>{overview.stats.totalUsers}</span>
        </button>
        <button
          type="button"
          class:active={adminSection === 'settings'}
          aria-current={adminSection === 'settings' ? 'page' : undefined}
          onclick={() => (adminSection = 'settings')}
        >
          <wa-icon name="sliders"></wa-icon> Configuration
        </button>
        <button
          type="button"
          class:active={adminSection === 'limits'}
          aria-current={adminSection === 'limits' ? 'page' : undefined}
          onclick={() => (adminSection = 'limits')}
        >
          <wa-icon name="gauge"></wa-icon> Limites de requêtes
        </button>
        <button
          type="button"
          class:active={adminSection === 'maintenance'}
          aria-current={adminSection === 'maintenance' ? 'page' : undefined}
          onclick={() => (adminSection = 'maintenance')}
        >
          <wa-icon name="hard-drive"></wa-icon> Maintenance
        </button>
      </nav>

      {#if adminSection === 'limits'}
        <RequestLimitsAdmin instances={overview.instances || []} />
      {:else if adminSection === 'users'}
        {#if showCreateUser}
          <div class="admin-create-wrap">
            <form class="admin-panel admin-create" onsubmit={createUser}>
              <div class="admin-panel-head admin-panel-head--split">
                <div>
                  <h2>Créer un compte</h2>
                  <p>Définissez son rôle et les bases auxquelles il aura accès.</p>
                </div>
                <button
                  class="admin-icon-btn"
                  type="button"
                  aria-label="Fermer la création de compte"
                  onclick={() => (showCreateUser = false)}><wa-icon name="xmark"></wa-icon></button
                >
              </div>
              <label>
                Email
                <input type="email" bind:value={newUser.email} required autocomplete="off" />
              </label>
              <label>
                Mot de passe
                <input
                  type="password"
                  bind:value={newUser.password}
                  required
                  minlength="8"
                  autocomplete="new-password"
                />
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
              <label>
                Rôle
                <select bind:value={newUser.superAdmin} disabled={isCreatingUser}>
                  <option value={false}>Utilisateur — accès aux bases et hébergements</option>
                  <option value={true}>Superuser — administrateur de la plateforme</option>
                </select>
              </label>
              {#if canManageInstanceAccess}
                <UserInstanceAccess
                  instances={overview.instances}
                  bind:selected={newUser.managedInstances}
                  superAdmin={newUser.superAdmin}
                  disabled={isCreatingUser}
                />
              {/if}
              {#if canManageWebAccess}
                <UserWebProjectAccess
                  projects={overview.webProjects}
                  bind:selected={newUser.webProjects}
                  superAdmin={newUser.superAdmin}
                  disabled={isCreatingUser}
                />
              {/if}
              <div class="admin-checks">
                <label><input type="checkbox" bind:checked={newUser.verified} /> Vérifié</label>
              </div>
              <label>
                Suspension
                <input type="text" bind:value={newUser.suspension} placeholder="vide = actif" />
              </label>
              <div class="account-welcome-option">
                <label
                  ><input
                    type="checkbox"
                    bind:checked={sendWelcomeEmail}
                    disabled={isCreatingUser ||
                      !settings?.smtp.enabled ||
                      !settings?.smtp.host ||
                      !settings?.smtp.senderAddress}
                  />
                  Envoyer les identifiants par e-mail
                </label>
                <p>
                  Un mail de bienvenue Kolab sera envoyé à l’adresse saisie, avec le lien de connexion et le mot de
                  passe ci-dessus.
                </p>
                {#if !settings?.smtp.enabled || !settings?.smtp.host || !settings?.smtp.senderAddress}<p>
                    Configurez SMTP dans les paramètres pour activer l’envoi.
                  </p>{/if}
              </div>
              <button class="admin-primary-btn" type="submit" disabled={isCreatingUser}>
                <wa-icon name="user-plus"></wa-icon>
                {isCreatingUser ? 'Creation...' : 'Creer le compte'}
              </button>
            </form>
          </div>
        {/if}
        <section class="admin-panel admin-users" aria-labelledby="admin-users-title">
          <div class="admin-users-toolbar">
            <div class="admin-users-title">
              <h2 id="admin-users-title">
                Comptes utilisateurs <span class="admin-count">{filteredUsers.length}</span>
              </h2>
              <p>Consultez un compte, puis ouvrez sa fiche pour le modifier.</p>
            </div>
            <div class="admin-user-filters">
              <label class="admin-search">
                <wa-icon name="magnifying-glass" aria-hidden="true"></wa-icon>
                <span class="sr-only">Rechercher un compte</span>
                <input
                  type="search"
                  bind:value={searchQuery}
                  oninput={() => (userPage = 1)}
                  placeholder="Rechercher un compte…"
                  disabled={!!editingUserId}
                />
              </label>
              <label class="admin-filter">
                <span class="sr-only">Filtrer par état</span>
                <select disabled={!!editingUserId} bind:value={userStatus} onchange={() => (userPage = 1)}>
                  <option value="all">Tous les états</option>
                  <option value="active">Actifs</option>
                  <option value="unverified">Non vérifiés</option>
                  <option value="suspended">Suspendus</option>
                </select>
              </label>
              <label class="admin-filter">
                <span class="sr-only">Filtrer par rôle</span>
                <select disabled={!!editingUserId} bind:value={userRole} onchange={() => (userPage = 1)}>
                  <option value="all">Tous les rôles</option>
                  <option value="user">Utilisateurs</option>
                  <option value="superuser">Superusers</option>
                </select>
              </label>
            </div>
          </div>

          <div class="admin-table-wrap">
            <table class="admin-table">
              <thead>
                <tr>
                  <th scope="col">Compte</th>
                  <th scope="col">État</th>
                  <th scope="col">Rôle</th>
                  <th scope="col">Offre / quota</th>
                  <th scope="col">Bases</th>
                  <th><span class="sr-only">Actions du compte</span></th>
                </tr>
              </thead>
              <tbody>
                {#each visibleUsers as user (user.id)}
                  {@const draft = userDrafts[user.id]}
                  {#if draft}
                    <tr class:admin-row-editing={editingUserId === user.id}>
                      <td>
                        <div class="admin-account-cell">
                          <span class="admin-avatar" aria-hidden="true">{user.email.slice(0, 1).toUpperCase()}</span>
                          <div class="admin-account-identity">
                            <span class="admin-account-email"
                              >{user.email}
                              {#if user.id === $userStore?.id}<small class="admin-you">vous</small>{/if}</span
                            >
                            <span class="admin-account-id">{user.username || user.id}</span>
                          </div>
                        </div>
                      </td>
                      <td>
                        <span
                          class="admin-status"
                          class:suspended={!!user.suspension}
                          class:pending={!user.verified && !user.suspension}
                          title={user.suspension || ''}
                        >
                          <span aria-hidden="true"></span>{user.suspension
                            ? 'Suspendu'
                            : user.verified
                              ? 'Actif'
                              : 'Non vérifié'}
                        </span>
                      </td>
                      <td
                        ><span class="admin-role" class:superuser={user.superAdmin}
                          >{#if user.superAdmin}<wa-icon name="shield-halved" aria-hidden="true"
                            ></wa-icon>{/if}{user.superAdmin ? 'Superuser' : 'Utilisateur'}</span
                        ></td
                      >
                      <td
                        ><span class="admin-plan">{user.subscription || 'free'}</span><span class="admin-quota"
                          >{user.subscription_quantity} instances</span
                        ></td
                      >
                      <td
                        ><span class="admin-base-count"
                          >{user.superAdmin
                            ? 'Toutes'
                            : canManageInstanceAccess
                              ? overview.instances.filter(
                                  (instance) =>
                                    instance.uid === user.id || (user.managedInstances || []).includes(instance.id)
                                ).length
                              : user.instanceCount}</span
                        ></td
                      >
                      <td class="admin-table-actions">
                        <button
                          class="admin-secondary-btn admin-edit-btn"
                          type="button"
                          aria-expanded={editingUserId === user.id}
                          aria-controls={'admin-user-editor-' + user.id}
                          onclick={() => (editingUserId === user.id ? cancelUserEdit(user) : editUser(user))}
                          disabled={!!savingUserId || (!!editingUserId && editingUserId !== user.id)}
                          ><wa-icon name={editingUserId === user.id ? 'xmark' : 'pen-to-square'}
                          ></wa-icon>{editingUserId === user.id ? 'Annuler' : 'Modifier'}</button
                        >
                      </td>
                    </tr>
                    {#if editingUserId === user.id}
                      <tr class="admin-editor-row">
                        <td colspan="6">
                          <fieldset
                            class="admin-user-editor"
                            id={'admin-user-editor-' + user.id}
                            disabled={savingUserId === user.id}
                          >
                            <legend class="sr-only">Modifier le compte {user.email}</legend>
                            <div class="admin-editor-heading">
                              <div>
                                <span class="admin-eyebrow">Fiche du compte</span>
                                <h3>{user.email}</h3>
                              </div>
                              <span class="admin-draft-label">Édition en cours</span>
                            </div>
                            <div class="admin-editor-fields">
                              <label>Email<input type="email" bind:value={draft.email} autocomplete="off" /></label>
                              <label
                                >Nouveau mot de passe<input
                                  type="password"
                                  bind:value={draft.password}
                                  autocomplete="new-password"
                                  placeholder="Laisser vide pour conserver"
                                /></label
                              >
                              <label
                                >Rôle<select bind:value={draft.superAdmin}
                                  ><option value={false}>Utilisateur</option><option value={true}>Superuser</option
                                  ></select
                                ></label
                              >
                              <label
                                >Offre<select bind:value={draft.subscription}
                                  >{#each subscriptionOptions as option}<option value={option}>{option}</option
                                    >{/each}</select
                                ></label
                              >
                              <label
                                >Quota d’instances<input
                                  type="number"
                                  min="0"
                                  step="1"
                                  bind:value={draft.subscription_quantity}
                                /></label
                              >
                              <label
                                >Motif de suspension<input
                                  type="text"
                                  bind:value={draft.suspension}
                                  placeholder="Vide = compte actif"
                                /></label
                              >
                            </div>
                            <label class="admin-inline-check"
                              ><input type="checkbox" bind:checked={draft.verified} /> Adresse email vérifiée</label
                            >
                            {#if canManageInstanceAccess}
                              <UserInstanceAccess
                                instances={overview.instances}
                                bind:selected={draft.managedInstances}
                                userId={user.id}
                                superAdmin={draft.superAdmin}
                                disabled={savingUserId === user.id}
                              />
                            {/if}
                            {#if canManageWebAccess}
                              <UserWebProjectAccess
                                projects={overview.webProjects}
                                bind:selected={draft.webProjects}
                                superAdmin={draft.superAdmin}
                                disabled={savingUserId === user.id}
                              />
                            {/if}
                            <div class="admin-editor-actions">
                              <p>
                                {canManageInstanceAccess
                                  ? 'Le rôle et les accès ne changent qu’après enregistrement.'
                                  : 'Les modifications prennent effet après enregistrement.'}
                              </p>
                              <button
                                class="admin-secondary-btn"
                                type="button"
                                onclick={() => cancelUserEdit(user)}
                                disabled={savingUserId === user.id}>Annuler</button
                              ><button
                                class="admin-primary-btn"
                                type="button"
                                onclick={() => saveUser(user.id)}
                                disabled={savingUserId === user.id}
                                ><wa-icon name="floppy-disk"></wa-icon>{savingUserId === user.id
                                  ? 'Enregistrement…'
                                  : 'Enregistrer les modifications'}</button
                              >
                            </div>
                          </fieldset>
                        </td>
                      </tr>
                    {/if}
                  {/if}
                {:else}
                  <tr
                    ><td colspan="6"
                      ><div class="admin-empty">
                        <wa-icon name="users" aria-hidden="true"></wa-icon><strong>Aucun compte trouvé</strong>
                        <p>Modifiez votre recherche ou les filtres sélectionnés.</p>
                        <button
                          class="admin-secondary-btn"
                          type="button"
                          onclick={() => {
                            searchQuery = ''
                            userStatus = 'all'
                            userRole = 'all'
                            userPage = 1
                          }}>Réinitialiser les filtres</button
                        >
                      </div></td
                    ></tr
                  >
                {/each}
              </tbody>
            </table>
          </div>
          <nav class="admin-pagination" aria-label="Pagination des utilisateurs">
            <span
              >Page <strong>{userPage}</strong> sur {userPages}
              <span class="admin-pagination-total">· {filteredUsers.length} comptes</span></span
            >
            <div>
              <button
                type="button"
                class="admin-secondary-btn"
                disabled={userPage === 1 || !!editingUserId}
                onclick={() => userPage--}><wa-icon name="chevron-left" aria-hidden="true"></wa-icon>Précédent</button
              ><button
                type="button"
                class="admin-secondary-btn"
                disabled={userPage === userPages || !!editingUserId}
                onclick={() => userPage++}>Suivant<wa-icon name="chevron-right" aria-hidden="true"></wa-icon></button
              >
            </div>
          </nav>
        </section>
      {:else if adminSection === 'settings'}
        <div class="admin-settings-wrap">
          <form class="admin-panel admin-settings" onsubmit={saveSettings}>
            <div class="admin-panel-head">
              <h2>Configuration du serveur</h2>
              <p>Les changements prennent effet après leur enregistrement.</p>
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
            <div class="admin-s3-box admin-smtp-box">
              <div class="admin-s3-head">
                <div>
                  <h3>SMTP des emails</h3>
                  <p>Configuration utilisee pour les emails de verification, reset mot de passe et notifications.</p>
                </div>
                <span class:ready={smtpReady} class="admin-s3-status">
                  {smtpReady ? 'Champs remplis' : 'Incomplet'}
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
                  <input type="text" bind:value={settings.smtp.senderName} placeholder="Kolab Hosting" />
                </label>
                <label>
                  Expéditeur email (adresse seule)
                  <input type="email" bind:value={settings.smtp.senderAddress} placeholder="contact@monappli.re" />
                  <span class="admin-field-help"
                    >Exemple : contact@monappli.re. Placez le nom de la personne ou de l’entreprise dans « Expéditeur
                    nom ».</span
                  >
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
                TLS direct (généralement port 465)
              </label>
              <p class="admin-field-help">
                Port 587 : laissez TLS direct décoché pour utiliser STARTTLS si le relais le propose. Port 465 : activez
                TLS direct.
              </p>
              <label>
                Email de test
                <input id="smtp-test-email" type="email" bind:value={smtpTestEmail} placeholder="admin@monappli.re" />
              </label>
              <div class="admin-s3-actions">
                <button
                  class="admin-secondary-btn"
                  type="button"
                  onclick={testSMTP}
                  disabled={isTestingSMTP || isTestingBackupS3 || isSavingSettings || !settings.smtp.enabled}
                >
                  <wa-icon name={isTestingSMTP ? 'rotate' : 'paper-plane'}></wa-icon>
                  {isTestingSMTP ? 'Test en cours… (25 s maximum)' : 'Tester SMTP'}
                </button>
                <button
                  type="button"
                  class="admin-primary-btn"
                  onclick={saveSMTP}
                  disabled={isSavingSettings || isTestingSMTP || isTestingBackupS3}>Enregistrer SMTP</button
                >
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
            <button
              class="admin-primary-btn"
              type="submit"
              disabled={isSavingSettings || isTestingSMTP || isTestingBackupS3}
            >
              <wa-icon name="floppy-disk"></wa-icon>
              {isSavingSettings ? 'Enregistrement…' : 'Enregistrer tous les paramètres'}
            </button>
          </form>
        </div>
      {:else}
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
                onclick={() => (cleanupConfirmation = true)}
                disabled={isScanningDisk || isRunningDiskCleanup || !diskCleanup?.orphanCount}
              >
                <wa-icon name="trash"></wa-icon>
                {isRunningDiskCleanup ? 'Nettoyage...' : 'Nettoyer'}
              </button>
            </div>
          </div>

          {#if cleanupConfirmation && diskCleanup}
            <div class="admin-cleanup-confirm" role="alert">
              <p>
                Supprimer définitivement les {diskCleanup.orphanCount} dossiers affichés ({formatBytes(
                  diskCleanup.totalBytes
                )}) ?
              </p>
              <button type="button" class="admin-danger-btn" onclick={runDiskCleanup} disabled={isRunningDiskCleanup}
                >Confirmer le nettoyage</button
              >
              <button type="button" class="admin-secondary-btn" onclick={() => (cleanupConfirmation = false)}
                >Annuler</button
              >
            </div>
          {/if}
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
      {/if}
    {/if}
  {/if}
</div>

<style>
  .account-welcome-option {
    border: 1px solid var(--app-border);
    border-radius: 0.5rem;
    padding: 1rem;
    background: var(--app-surface-soft);
  }
  .account-welcome-option label {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    font-weight: 600;
  }
  .account-welcome-option p {
    margin: 0.6rem 0 0;
    color: var(--app-text-muted);
    font-size: 0.78rem;
    line-height: 1.6;
  }

  .admin-console {
    --admin-accent: var(--app-accent, #3b7cff);
    --admin-accent-soft: color-mix(in srgb, var(--admin-accent) 10%, transparent);
    color: var(--app-text);
    font-size: 0.8125rem;
  }
  .admin-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 1rem;
    margin-bottom: 1.5rem;
  }
  .admin-eyebrow {
    display: block;
    margin-bottom: 0.35rem;
    color: var(--app-text-faint);
    font-size: 0.625rem;
    font-weight: 650;
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }
  .admin-head h1 {
    margin: 0;
    color: var(--app-text-strong);
    font-size: 1.5rem;
    line-height: 1.25;
    font-weight: 650;
    letter-spacing: -0.035em;
  }
  .admin-head p,
  .admin-panel-head p,
  .admin-users-toolbar p,
  .admin-disk-head p {
    margin: 0.35rem 0 0;
    color: var(--app-text-muted);
    font-size: 0.75rem;
    line-height: 1.55;
  }
  .admin-head-actions,
  .admin-s3-actions,
  .admin-disk-actions {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.5rem;
  }
  .admin-callout {
    display: block;
    margin-bottom: 1rem;
  }
  .admin-forbidden,
  .admin-loading {
    display: flex;
    min-height: 20rem;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    text-align: center;
    color: var(--app-text-muted);
    gap: 0.5rem;
  }
  .admin-forbidden wa-icon {
    color: var(--admin-accent);
    font-size: 1.5rem;
    margin-bottom: 0.5rem;
  }
  .admin-forbidden h1 {
    margin: 0;
    color: var(--app-text-strong);
    font-size: 1rem;
    font-weight: 600;
  }
  .admin-forbidden p {
    margin: 0;
  }
  .admin-icon-btn,
  .admin-primary-btn,
  .admin-danger-btn,
  .admin-secondary-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 0.45rem;
    min-height: 2rem;
    padding: 0.4rem 0.7rem;
    border: 1px solid transparent;
    border-radius: 0.375rem;
    font: inherit;
    font-size: 0.75rem;
    font-weight: 550;
    line-height: 1.35;
    cursor: pointer;
    white-space: nowrap;
    transition:
      background-color 120ms ease,
      border-color 120ms ease,
      color 120ms ease;
  }
  .admin-icon-btn {
    width: 2rem;
    padding: 0;
    background: transparent;
    color: var(--app-text-muted);
    border-color: var(--app-border);
  }
  .admin-primary-btn {
    background: var(--admin-accent);
    border-color: var(--admin-accent);
    color: #fff;
  }
  .admin-primary-btn:hover:not(:disabled) {
    background: color-mix(in srgb, var(--admin-accent) 86%, #fff);
    border-color: color-mix(in srgb, var(--admin-accent) 86%, #fff);
  }
  .admin-secondary-btn {
    background: var(--app-surface);
    color: var(--app-text);
    border-color: var(--app-border);
  }
  .admin-secondary-btn:hover:not(:disabled),
  .admin-icon-btn:hover:not(:disabled) {
    background: var(--app-surface-hover);
    border-color: var(--app-border-strong);
  }
  .admin-danger-btn {
    color: var(--wa-color-danger, #ef4444);
    border-color: color-mix(in srgb, var(--wa-color-danger, #ef4444) 30%, transparent);
    background: color-mix(in srgb, var(--wa-color-danger, #ef4444) 8%, transparent);
  }
  button:disabled {
    opacity: 0.42;
    cursor: not-allowed;
  }
  button:focus-visible,
  select:focus-visible,
  input:focus-visible,
  textarea:focus-visible {
    outline: 2px solid var(--admin-accent);
    outline-offset: 2px;
  }
  .admin-kpis {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    border: 1px solid var(--app-border);
    border-radius: 0.5rem;
    background: var(--app-surface);
    margin-bottom: 1.5rem;
    overflow: hidden;
  }
  .admin-kpi {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    padding: 0.9rem 1.1rem;
    border-right: 1px solid var(--app-border);
    border-bottom: 1px solid var(--app-border);
  }
  .admin-kpi:nth-child(even) {
    border-right: 0;
  }
  .admin-kpi:nth-last-child(-n + 2) {
    border-bottom: 0;
  }
  .admin-kpi span {
    color: var(--app-text-muted);
    font-size: 0.6875rem;
    font-weight: 500;
  }
  .admin-kpi strong {
    color: var(--app-text-strong);
    font-size: 1.4rem;
    line-height: 1;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.03em;
  }
  .admin-section-nav {
    display: flex;
    gap: 1.25rem;
    border-bottom: 1px solid var(--app-border);
    margin-bottom: 1.25rem;
    overflow-x: auto;
    overflow-y: hidden;
  }
  .admin-section-nav button {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    flex-shrink: 0;
    border: 0;
    border-bottom: 2px solid transparent;
    margin-bottom: -1px;
    padding: 0.7rem 0 0.8rem;
    color: var(--app-text-muted);
    background: transparent;
    font-size: 0.75rem;
    font-weight: 550;
    cursor: pointer;
  }
  .admin-section-nav button:hover {
    color: var(--app-text-strong);
  }
  .admin-section-nav button.active {
    border-color: var(--admin-accent);
    color: var(--admin-accent);
  }
  .admin-section-nav button span,
  .admin-count {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 1.35rem;
    padding: 0.1rem 0.35rem;
    border-radius: 0.25rem;
    border: 1px solid var(--app-border);
    color: var(--app-text-muted);
    background: var(--app-surface-soft);
    font-size: 0.625rem;
    font-weight: 500;
    font-variant-numeric: tabular-nums;
  }
  .admin-panel {
    padding: 1rem;
    border: 1px solid var(--app-border);
    border-radius: 0.5rem;
    background: var(--app-surface);
  }
  .admin-panel h2 {
    margin: 0;
    color: var(--app-text-strong);
    font-size: 0.875rem;
    font-weight: 600;
    letter-spacing: -0.01em;
  }
  .admin-panel h3 {
    margin: 0;
    color: var(--app-text-strong);
    font-size: 0.8125rem;
    font-weight: 600;
  }
  .admin-panel-head {
    margin-bottom: 0.35rem;
  }
  .admin-panel-head--split {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 1rem;
  }
  .admin-create-wrap {
    margin-bottom: 1rem;
  }
  .admin-create {
    max-width: 58rem;
  }
  .admin-settings-wrap {
    max-width: 64rem;
  }
  .admin-create,
  .admin-settings {
    display: grid;
    align-content: start;
    gap: 0.9rem;
  }
  .admin-panel label {
    display: grid;
    min-width: 0;
    gap: 0.375rem;
    color: var(--app-text-muted);
    font-size: 0.6875rem;
    font-weight: 550;
  }
  .admin-panel input:not([type='checkbox']),
  .admin-panel select,
  .admin-panel textarea {
    width: 100%;
    min-width: 0;
    min-height: 2.125rem;
    padding: 0.5rem 0.6rem;
    border: 1px solid var(--app-border-strong, var(--app-border));
    border-radius: 0.35rem;
    background: var(--app-surface-strong);
    color: var(--app-text-strong);
    font: inherit;
    font-size: 0.75rem;
    font-weight: 400;
    transition:
      border-color 120ms ease,
      box-shadow 120ms ease;
  }
  .admin-panel input:not([type='checkbox']):focus,
  .admin-panel select:focus,
  .admin-panel textarea:focus {
    border-color: var(--admin-accent);
    box-shadow: 0 0 0 2px color-mix(in srgb, var(--admin-accent) 12%, transparent);
  }
  .admin-panel input::placeholder,
  .admin-panel textarea::placeholder {
    color: var(--app-text-faint);
    opacity: 1;
  }
  .admin-panel textarea {
    resize: vertical;
    line-height: 1.55;
  }
  .admin-form-row,
  .admin-editor-fields {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 0.8rem;
  }
  .admin-field-help {
    margin: 0;
    color: var(--app-text-faint);
    font-size: 0.6875rem;
    font-weight: 400;
    line-height: 1.5;
  }
  .admin-s3-box {
    display: grid;
    gap: 0.85rem;
    padding: 1rem;
    border: 1px solid var(--app-border);
    border-radius: 0.4rem;
    background: var(--app-surface-soft);
  }
  .admin-s3-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 0.65rem;
  }
  .admin-s3-head p {
    margin: 0.3rem 0 0;
    color: var(--app-text-muted);
    font-size: 0.6875rem;
    line-height: 1.5;
  }
  .admin-s3-status {
    display: inline-flex;
    flex-shrink: 0;
    padding: 0.2rem 0.4rem;
    border: 1px solid color-mix(in srgb, #d6a13b 30%, transparent);
    border-radius: 0.25rem;
    color: #c99125;
    background: color-mix(in srgb, #d6a13b 8%, transparent);
    font-size: 0.625rem;
    font-weight: 550;
  }
  .admin-s3-status.ready {
    color: var(--admin-accent);
    border-color: color-mix(in srgb, var(--admin-accent) 30%, transparent);
    background: var(--admin-accent-soft);
  }
  .admin-checks {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.75rem 1rem;
  }
  .admin-checks--grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0.8rem 1rem;
    padding-block: 0.25rem;
  }
  .admin-checks label,
  .admin-panel .admin-inline-check {
    display: inline-flex;
    grid-template-columns: unset;
    align-items: center;
    gap: 0.5rem;
    color: var(--app-text);
    font-size: 0.75rem;
    font-weight: 400;
    cursor: pointer;
    line-height: 1.5;
  }
  .admin-checks input,
  .admin-inline-check input {
    appearance: auto;
    padding: 0;
    margin: 0;
    flex: 0 0 1rem;
    width: 1rem;
    height: 1rem;
    accent-color: var(--admin-accent);
    cursor: pointer;
  }
  .admin-s3-toggle {
    margin: 0;
  }
  .admin-users {
    padding: 0;
    overflow: hidden;
  }
  .admin-users-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 1rem;
    padding: 0.9rem 1rem;
    border-bottom: 1px solid var(--app-border);
  }
  .admin-users-title h2 {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }
  .admin-users-title p {
    font-size: 0.6875rem;
  }
  .admin-user-filters {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex-wrap: wrap;
    flex: 0 1 auto;
    min-width: 0;
    max-width: 100%;
  }
  .admin-panel .admin-filter {
    width: auto;
    flex: 0 0 auto;
  }
  .admin-panel .admin-search {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    width: 14rem;
    min-width: 0;
    flex: 1 1 12rem;
    padding: 0.4rem 0.55rem;
    border: 1px solid var(--app-border-strong);
    border-radius: 0.35rem;
    background: var(--app-surface-strong);
    color: var(--app-text-faint);
  }
  .admin-panel .admin-search:focus-within {
    border-color: var(--admin-accent);
  }
  .admin-panel .admin-search input {
    padding: 0;
    border: 0;
    min-height: 1.25rem;
    background: transparent;
    box-shadow: none;
    font-size: 0.6875rem;
  }
  .admin-panel .admin-search input:focus {
    box-shadow: none;
    outline: none;
  }
  .admin-panel .admin-filter select {
    min-height: 2.125rem;
    padding: 0.4rem 1.5rem 0.4rem 0.55rem;
    font-size: 0.6875rem;
  }
  .admin-table-wrap {
    position: relative;
    width: 100%;
    overflow-x: auto;
  }
  .admin-table {
    width: 100%;
    min-width: 48rem;
    border-collapse: collapse;
    font-size: 0.75rem;
    text-align: left;
  }
  .admin-table th {
    padding: 0.65rem 1rem;
    border-bottom: 1px solid var(--app-border);
    background: var(--app-surface-soft);
    color: var(--app-text-faint);
    font-size: 0.625rem;
    font-weight: 550;
    letter-spacing: 0.05em;
    white-space: nowrap;
  }
  .admin-table td {
    padding: 0.75rem 1rem;
    border-bottom: 1px solid var(--app-border);
    vertical-align: middle;
  }
  .admin-table tbody tr:last-child td {
    border-bottom: 0;
  }
  .admin-table tbody tr:hover:not(.admin-editor-row) {
    background: var(--app-surface-soft);
  }
  .admin-table tbody tr.admin-row-editing {
    background: var(--admin-accent-soft);
  }
  .admin-account-cell {
    display: flex;
    align-items: center;
    gap: 0.65rem;
  }
  .admin-avatar {
    display: grid;
    place-items: center;
    width: 1.8rem;
    height: 1.8rem;
    flex: 0 0 1.8rem;
    border: 1px solid var(--app-border);
    border-radius: 0.4rem;
    background: var(--app-surface-soft);
    color: var(--app-text-muted);
    font-size: 0.6875rem;
    font-weight: 550;
  }
  .admin-account-identity {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    min-width: 0;
  }
  .admin-account-email {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    color: var(--app-text-strong);
    font-weight: 500;
    overflow-wrap: anywhere;
  }
  .admin-account-id {
    color: var(--app-text-faint);
    font-family: var(--font-mono, monospace);
    font-size: 0.625rem;
  }
  .admin-you {
    color: var(--app-text-muted);
    font-size: 0.6rem;
    border: 1px solid var(--app-border);
    border-radius: 0.2rem;
    padding: 0.05rem 0.25rem;
    font-weight: 400;
  }
  .admin-status {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    color: var(--app-text);
    font-size: 0.6875rem;
    white-space: nowrap;
  }
  .admin-status > span {
    width: 0.35rem;
    height: 0.35rem;
    border-radius: 50%;
    background: var(--admin-accent);
  }
  .admin-status.pending {
    color: #c99125;
  }
  .admin-status.pending > span {
    background: #c99125;
  }
  .admin-status.suspended {
    color: var(--wa-color-danger, #ef4444);
  }
  .admin-status.suspended > span {
    background: currentColor;
  }
  .admin-role {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    font-size: 0.6875rem;
    color: var(--app-text-muted);
  }
  .admin-role.superuser {
    color: var(--admin-accent);
  }
  .admin-plan {
    display: block;
    color: var(--app-text);
    font-size: 0.6875rem;
    text-transform: capitalize;
  }
  .admin-quota {
    display: block;
    margin-top: 0.2rem;
    color: var(--app-text-faint);
    font-size: 0.625rem;
    font-variant-numeric: tabular-nums;
  }
  .admin-base-count {
    font-variant-numeric: tabular-nums;
    color: var(--app-text-muted);
    font-size: 0.75rem;
  }
  .admin-table-actions {
    text-align: right;
    white-space: nowrap;
  }
  .admin-edit-btn {
    min-height: 1.8rem;
    font-size: 0.6875rem;
    background: transparent;
  }
  .admin-editor-row > td {
    padding: 0;
    background: var(--app-surface-soft);
  }
  .admin-user-editor {
    margin: 0;
    min-width: 0;
    border: 0;
    display: grid;
    gap: 1rem;
    padding: 1.25rem;
    border-left: 2px solid var(--admin-accent);
  }
  .admin-editor-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 0.75rem;
  }
  .admin-editor-heading h3 {
    color: var(--app-text-strong);
    font-size: 0.8125rem;
    font-weight: 550;
    overflow-wrap: anywhere;
  }
  .admin-draft-label {
    color: var(--app-text-faint);
    font-size: 0.625rem;
  }
  .admin-editor-actions {
    display: flex;
    justify-content: flex-end;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.5rem;
    border-top: 1px solid var(--app-border);
    padding-top: 1rem;
  }
  .admin-editor-actions p {
    margin: 0 auto 0 0;
    color: var(--app-text-faint);
    font-size: 0.6875rem;
  }
  .admin-pagination {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    padding: 0.75rem 1rem;
    border-top: 1px solid var(--app-border);
    color: var(--app-text-muted);
    font-size: 0.6875rem;
    font-variant-numeric: tabular-nums;
  }
  .admin-pagination > div {
    display: flex;
    gap: 0.4rem;
  }
  .admin-pagination strong {
    color: var(--app-text);
    font-weight: 550;
  }
  .admin-pagination .admin-secondary-btn {
    min-height: 1.8rem;
    font-size: 0.6875rem;
  }
  .admin-empty {
    display: flex;
    min-height: 12rem;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    text-align: center;
    gap: 0.6rem;
    padding: 1rem;
    color: var(--app-text-faint);
  }
  .admin-empty > wa-icon {
    font-size: 1.5rem;
    margin-bottom: 0.25rem;
  }
  .admin-empty strong {
    color: var(--app-text);
    font-size: 0.8125rem;
    font-weight: 550;
  }
  .admin-empty p {
    margin: 0 0 0.25rem;
    font-size: 0.75rem;
  }
  .admin-disk-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 1rem;
  }
  .admin-cleanup-confirm {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-top: 1rem;
    padding: 0.85rem;
    border: 1px solid color-mix(in srgb, var(--wa-color-danger, #ef4444) 30%, transparent);
    border-radius: 0.4rem;
    background: color-mix(in srgb, var(--wa-color-danger, #ef4444) 5%, transparent);
  }
  .admin-cleanup-confirm p {
    flex-basis: 100%;
    margin: 0 0 0.3rem;
    font-size: 0.75rem;
  }
  .admin-disk-summary {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0;
    margin-top: 1rem;
    border: 1px solid var(--app-border);
    border-radius: 0.4rem;
    overflow: hidden;
  }
  .admin-disk-summary > div {
    padding: 0.85rem;
    border-right: 1px solid var(--app-border);
  }
  .admin-disk-summary > div:nth-child(even) {
    border-right: 0;
  }
  .admin-disk-summary span,
  .admin-disk-list span {
    display: block;
    color: var(--app-text-faint);
    font-size: 0.625rem;
    font-weight: 500;
  }
  .admin-disk-summary strong {
    display: block;
    margin-top: 0.45rem;
    color: var(--app-text-strong);
    font-size: 1.125rem;
    font-weight: 550;
    font-variant-numeric: tabular-nums;
  }
  .admin-disk-list {
    display: grid;
    margin-top: 1rem;
    border: 1px solid var(--app-border);
    border-radius: 0.4rem;
    overflow: hidden;
  }
  .admin-disk-list > div {
    display: grid;
    grid-template-columns: 7rem minmax(0, 1fr) auto;
    align-items: center;
    gap: 0.75rem;
    padding: 0.7rem 0.85rem;
    border-bottom: 1px solid var(--app-border);
  }
  .admin-disk-list > div:last-child {
    border-bottom: 0;
  }
  .admin-disk-list strong {
    color: var(--app-text);
    font-family: var(--font-mono, monospace);
    font-size: 0.6875rem;
    font-weight: 400;
    overflow-wrap: anywhere;
  }
  .admin-disk-list small {
    color: var(--app-text-muted);
    font-size: 0.625rem;
  }
  .admin-disk-list > div.removed {
    background: var(--admin-accent-soft);
  }
  .admin-disk-list > div.error {
    background: color-mix(in srgb, var(--wa-color-danger, #ef4444) 5%, transparent);
  }
  .admin-spinning {
    animation: admin-spin 1s linear infinite;
  }
  @keyframes admin-spin {
    to {
      transform: rotate(360deg);
    }
  }
  @media (min-width: 640px) {
    .admin-form-row {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .admin-editor-fields {
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }
    .admin-kpis {
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }
    .admin-kpi {
      border-right: 1px solid var(--app-border);
      border-bottom: 0;
    }
    .admin-kpi:nth-child(even) {
      border-right: 1px solid var(--app-border);
    }
    .admin-kpi:last-child {
      border-right: 0;
    }
    .admin-disk-summary {
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }
    .admin-disk-summary > div:nth-child(even) {
      border-right: 1px solid var(--app-border);
    }
    .admin-disk-summary > div:last-child {
      border-right: 0;
    }
  }
  @media (max-width: 639px) {
    .admin-head-actions {
      width: 100%;
    }
    .admin-users-toolbar {
      align-items: stretch;
    }
    .admin-user-filters {
      width: 100%;
    }
    .admin-panel .admin-search {
      flex: 1 1 100%;
    }
    .admin-panel .admin-filter {
      flex: 1;
    }
    .admin-checks--grid {
      grid-template-columns: 1fr;
    }
    .admin-pagination-total {
      display: none;
    }
    .admin-pagination .admin-secondary-btn {
      padding-inline: 0.45rem;
    }
    .admin-disk-list > div {
      grid-template-columns: 1fr;
      gap: 0.3rem;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .admin-spinning {
      animation: none;
    }
  }
</style>
