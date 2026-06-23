import { browser } from '$app/environment'
import { INSTANCE_URL } from '$lib/appEnv'
import { fetchEventSource } from '$lib/fetchEventSource'
import { createGenericSyncEvent } from '$util/events'
import {
  type AuthModel,
  BaseAuthStore,
  ClientResponseError,
  type CreateInstancePayload,
  CreateInstancePayloadSchema,
  type CreateInstanceResult,
  type DeleteInstancePayload,
  DeleteInstancePayloadSchema,
  type DeleteInstanceResult,
  type InstanceFields,
  type InstanceId,
  PocketBase,
  RestCommands,
  RestMethods,
  type UntrustedInstanceLogFields,
  type UpdateInstancePayload,
  UpdateInstancePayloadSchema,
  type UpdateInstanceResult,
  assertExists,
  createRestHelper,
} from 'pockethost/common'

export type AuthToken = string
export type AuthStoreProps = {
  token: AuthToken
  model: AuthModel | null
  isValid: boolean
}

export type PocketbaseClientConfig = {
  url: string
}
export type PocketbaseClient = ReturnType<typeof createPocketbaseClient>

export type OperatorSettings = {
  publicSignupEnabled: boolean
  autoVerifyUsers: boolean
  defaultUserQuota: number
  defaultSubscription: 'free' | 'premium' | 'founder' | 'flounder' | 'legacy'
  defaultInstancePower: boolean
  defaultInstanceDevMode: boolean
  defaultSyncAdmin: boolean
  defaultAutoVacuum: boolean
  supportEmail: string
  maintenanceMessage: string
  notes: string
}

export type OperatorUser = {
  id: string
  email: string
  username: string
  name: string
  verified: boolean
  superAdmin: boolean
  subscription: string
  subscription_interval: string
  subscription_quantity: number
  suspension: string
  created: string
  updated: string
  instanceCount: number
}

export type OperatorAdminOverview = {
  settings: OperatorSettings
  users: OperatorUser[]
  stats: {
    totalUsers: number
    verifiedUsers: number
    superAdmins: number
    totalInstances: number
    suspendedUsers: number
  }
}

export type InstanceBackup = {
  id: string
  user: string
  instance: string
  kind: 'manual' | 'pre-restore'
  status: 'running' | 'ready' | 'failed'
  filename: string
  remoteKey: string
  sizeBytes: number
  compressedBytes: number
  checksum: string
  error: string
  remoteError: string
  manifest: unknown
  created: string
  updated: string
}

export type InstanceOverviewBackup = Pick<
  InstanceBackup,
  | 'id'
  | 'kind'
  | 'status'
  | 'filename'
  | 'remoteKey'
  | 'sizeBytes'
  | 'compressedBytes'
  | 'error'
  | 'remoteError'
  | 'created'
  | 'updated'
>

export type InstanceOverview = {
  instance: InstanceFields
  backups: {
    count: number
    readyCount: number
    runningCount: number
    failedCount: number
    totalCompressedBytes: number
    latest: InstanceOverviewBackup | null
  }
  storage: {
    instanceBytes: number | null
  }
  collectedAt: string
}

export const createPocketbaseClient = (config: PocketbaseClientConfig) => {
  const { url } = config

  const client = new PocketBase(url)

  const { authStore } = client

  const user = () => authStore.model as AuthStoreProps['model']

  const isLoggedIn = () => authStore.isValid

  const logOut = () => authStore.clear()

  /**
   * This will register a new user into Pocketbase, and email them a verification link
   *
   * @param email {string} The email of the user
   * @param password {string} The password of the user
   */
  const createUser = async (email: string, password: string) => {
    // Build the new user object and any additional properties needed
    const data = {
      email,
      password,
      passwordConfirm: password,
    }

    // Create the user
    const record = await client.collection('users').create(data)

    // Send the verification email
    await resendVerificationEmail()

    return record
  }

  /**
   * This will let a user confirm their new account via a token in their email
   *
   * @param token {string} The token from the verification email
   */
  const confirmVerification = async (token: string) => {
    return await client.collection('users').confirmVerification(token)
  }

  /**
   * This will reset an unauthenticated user's password by sending a verification link to their email, and includes an
   * optional error handler
   *
   * @param email {string} The email of the user
   */
  const requestPasswordReset = async (email: string) => {
    return await client.collection('users').requestPasswordReset(email)
  }

  /**
   * This will let an unauthenticated user save a new password after verifying their email
   *
   * @param token {string} The token from the verification email
   * @param password {string} The new password of the user
   */
  const requestPasswordResetConfirm = async (token: string, password: string) => {
    return await client.collection('users').confirmPasswordReset(token, password, password)
  }

  /** Sends an email change confirmation link to the new email address. Requires the user to be authenticated. */
  const requestEmailChange = async (newEmail: string) => {
    await client.collection('users').requestEmailChange(newEmail)
  }

  /** Confirms an email change with the token from the confirmation email and the current password. */
  const confirmEmailChange = async (token: string, password: string) => {
    await client.collection('users').confirmEmailChange(token, password)
    client.authStore.clear()
  }

  /**
   * This will log a user into Pocketbase, and includes an optional error handler
   *
   * @param {string} email The email of the user
   * @param {string} password The password of the user
   */
  const authViaEmail = async (email: string, password: string) => {
    return await client.collection('users').authWithPassword(email, password)
  }

  const refreshAuthToken = () => client.collection('users').authRefresh()

  const restMixin = createRestHelper({ client })
  const { mkRest } = restMixin

  const createInstance = mkRest<CreateInstancePayload, CreateInstanceResult>(
    RestCommands.Instance,
    RestMethods.Post,
    CreateInstancePayloadSchema
  )

  const updateInstance = mkRest<UpdateInstancePayload, UpdateInstanceResult>(
    RestCommands.Instance,
    RestMethods.Put,
    UpdateInstancePayloadSchema
  )

  const deleteInstance = mkRest<DeleteInstancePayload, DeleteInstanceResult>(
    RestCommands.Instance,
    RestMethods.Delete,
    DeleteInstancePayloadSchema
  )

  const duplicateInstance = (id: InstanceId) =>
    client.send<{ instance: InstanceFields }>(`/api/instance/${id}/duplicate`, {
      method: 'POST',
    })

  const getInstanceOverview = (id: InstanceId) =>
    client.send<InstanceOverview>(`/api/instance/${id}/overview`, {
      method: 'GET',
    })

  const createInstanceBackup = (id: InstanceId) =>
    client.send<{ backup: InstanceBackup }>(`/api/instance/${id}/backups`, {
      method: 'POST',
    })

  const listInstanceBackups = (id: InstanceId) =>
    client.send<{ backups: InstanceBackup[] }>(`/api/instance/${id}/backups`, {
      method: 'GET',
    })

  const restoreInstanceBackup = (id: InstanceId, backupId: string) =>
    client.send<{ status: 'ok' }>(`/api/instance/${id}/backups/${backupId}/restore`, {
      method: 'POST',
    })

  const deleteInstanceBackup = (id: InstanceId, backupId: string) =>
    client.send<{ status: 'ok' }>(`/api/instance/${id}/backups/${backupId}`, {
      method: 'DELETE',
    })

  const downloadInstanceBackup = async (id: InstanceId, backup: Pick<InstanceBackup, 'id' | 'filename'>) => {
    if (!browser) throw new Error('Téléchargement disponible uniquement dans le navigateur.')

    const response = await fetch(`${url}/api/instance/${id}/backups/${backup.id}/download`, {
      headers: {
        Authorization: client.authStore.token,
      },
    })

    if (!response.ok) {
      const message = await response
        .json()
        .then((data) => data?.message || data?.error || response.statusText)
        .catch(() => response.statusText)
      throw new Error(message)
    }

    const blob = await response.blob()
    const objectUrl = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = objectUrl
    anchor.download = backup.filename || 'instance-backup.tar.gz'
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(objectUrl)
  }

  const getInstanceById = (id: InstanceId): Promise<InstanceFields | undefined> =>
    client.collection('instances').getOne<InstanceFields>(id)

  const getInstanceBySubdomain = (subdomain: InstanceFields['subdomain']): Promise<InstanceFields | undefined> =>
    client.collection('instances').getFirstListItem<InstanceFields>(`subdomain='${subdomain}'`)

  const getAllInstancesById = async () =>
    (await client.collection('instances').getFullList()).reduce(
      (c, v) => {
        c[v.id] = v as unknown as InstanceFields
        return c
      },
      {} as { [_: InstanceId]: InstanceFields }
    )

  const getOperatorAdminOverview = () => client.send<OperatorAdminOverview>('/api/admin/overview', {})

  const createOperatorUser = (data: {
    email: string
    password: string
    verified: boolean
    superAdmin: boolean
    subscription: string
    subscription_quantity: number
    suspension: string
  }) =>
    client.send<{ user: OperatorUser }>('/api/admin/users', {
      method: 'POST',
      body: data,
    })

  const updateOperatorUser = (
    id: string,
    data: Partial<{
      email: string
      password: string
      verified: boolean
      superAdmin: boolean
      subscription: string
      subscription_quantity: number
      suspension: string
    }>
  ) =>
    client.send<{ user: OperatorUser }>(`/api/admin/users/${id}`, {
      method: 'PATCH',
      body: data,
    })

  const updateOperatorSettings = (data: OperatorSettings) =>
    client.send<{ settings: OperatorSettings }>('/api/admin/settings', {
      method: 'PUT',
      body: data,
    })

  const parseError = (e: Error): string[] => {
    if (!(e instanceof ClientResponseError)) return [`${e}`]
    if (e.data.message && Object.keys(e.data.data).length === 0) return [e.data.message]
    return Object.values(e.data.data as Record<string, { message?: string } | undefined>)
      .map((v) => v?.message)
      .filter((v): v is string => !!v)
  }

  const resendVerificationEmail = async () => {
    const user = client.authStore.model
    assertExists(user, `Login required`)
    await client.collection('users').requestVerification(user.email)
  }

  const getAuthStoreProps = (): AuthStoreProps => {
    const { isSuperuser, model, token, isValid } = client.authStore

    if (isSuperuser) throw new Error(`Superuser models not supported`)
    if (model && !model.email) throw new Error(`Expected model to be a user here`)
    return {
      token,
      model,
      isValid,
    }
  }

  /**
   * Use synthetic event for authStore changers, so we can broadcast just the props we want and not the actual authStore
   * object.
   */
  const [onAuthChange, fireAuthChange] = createGenericSyncEvent<BaseAuthStore>()

  /** This section is for initialization */
  {
    /** Listen for native authStore changes and convert to synthetic event */
    client.authStore.onChange(() => {
      fireAuthChange(client.authStore)
    })

    /**
     * Refresh the auth token immediately upon creating the client. The auth token may be out of date, or fields in the
     * user record may have changed in the backend.
     */
    if (browser) {
      refreshAuthToken()
        .catch((error) => {
          client.authStore.clear()
        })
        .finally(() => {
          fireAuthChange(client.authStore)
        })
    }

    /**
     * Listen for auth state changes and subscribe to realtime _user events. This way, when the verified flag is
     * flipped, it will appear that the authstore model is updated.
     *
     * Polling is a stopgap til v.0.8. Once 0.8 comes along, we can do a realtime watch on the user record and update
     * auth accordingly.
     */
    const unsub = onAuthChange((authStore) => {
      const { model, isSuperuser } = authStore
      if (!model) return
      if (isSuperuser) return
      if (model.verified) {
        unsub()
        return
      }
      setTimeout(refreshAuthToken, 1000)

      // TODO - THIS DOES NOT WORK, WE HAVE TO POLL INSTEAD. FIX IN V0.8
      // unsub = subscribe<User>(`users/${model.id}`, (user) => {
      //   fireAuthChange({ ...authStore, model: user })
      // })
    })
  }

  const watchInstanceLog = (
    instance: InstanceFields,
    update: (log: UntrustedInstanceLogFields) => void,
    nInitial = 100
  ): (() => void) => {
    const auth = client.authStore.exportToCookie()

    const controller = new AbortController()
    const signal = controller.signal
    let stopped = false
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined

    function scheduleReconnect() {
      if (stopped) return
      reconnectTimer = setTimeout(continuallyFetchFromEventSource, 1000)
    }

    function continuallyFetchFromEventSource() {
      if (stopped) return

      const streamUrl = INSTANCE_URL(instance, `logs`)

      void fetchEventSource(streamUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: client.authStore.token,
        },
        openWhenHidden: true,
        body: JSON.stringify({
          instanceId: instance.id,
          n: nInitial,
          auth,
        }),
        onmessage: (event) => {
          const {} = event
          const log = JSON.parse(event.data) as UntrustedInstanceLogFields

          update(log)
        },
        onopen: async (response) => {
          if (!response.ok) {
            throw new Error(`Log stream failed: ${response.status} ${response.statusText}`)
          }
        },
        onerror: (e) => {
          if (!stopped) console.error(`Log stream error (${streamUrl}):`, e)
        },
        onclose: () => {
          scheduleReconnect()
        },
        signal,
      }).catch((error) => {
        if (stopped || signal.aborted) return
        console.error(`Log stream failed (${streamUrl}):`, error)
        scheduleReconnect()
      })
    }
    continuallyFetchFromEventSource()

    return () => {
      stopped = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      controller.abort()
    }
  }

  return {
    client,
    watchInstanceLog,
    getAuthStoreProps,
    parseError,
    getInstanceById,
    getInstanceBySubdomain,
    createInstance,
    authViaEmail,
    createUser,
    requestPasswordReset,
    requestPasswordResetConfirm,
    requestEmailChange,
    confirmEmailChange,
    confirmVerification,
    logOut,
    onAuthChange,
    isLoggedIn,
    user,
    getAllInstancesById,
    getOperatorAdminOverview,
    createOperatorUser,
    updateOperatorUser,
    updateOperatorSettings,
    resendVerificationEmail,
    updateInstance,
    deleteInstance,
    duplicateInstance,
    getInstanceOverview,
    createInstanceBackup,
    listInstanceBackups,
    restoreInstanceBackup,
    deleteInstanceBackup,
    downloadInstanceBackup,
  }
}
