import {
  createEvent,
  EDGE_APEX_DOMAIN,
  ensureInstanceDirectoryStructure,
  InstanceFields,
  InstanceId,
  InstanceStatus,
  LoggerService,
  mkSingleton,
  PocketBase,
  SingletonBaseConfig,
  UserFields,
  UserId,
} from '@'
import { instanceRoutingHostnames, normalizeInstanceRequestHost } from '../../common/instanceHostnames'

export type MirrorLiveInstance = {
  id: InstanceId
  status: InstanceStatus.Starting | InstanceStatus.Running
}

export type MirrorSyncOptions = {
  instances?: MirrorLiveInstance[]
  resetIdle?: boolean
}

export type MirrorDumpResponse = {
  users: UserFields[]
  instances: InstanceFields[]
}

export type MirrorSyncResponse = MirrorDumpResponse & {
  updated: number
}

export type MothershipMirrorServiceConfig = SingletonBaseConfig & {
  client: PocketBase
}

export const MothershipMirrorService = mkSingleton(async (config: MothershipMirrorServiceConfig) => {
  const logger = (config.logger ?? LoggerService()).create(`MothershipMirrorService`)
  const { dbg, error } = logger

  const [onInstanceUpserted, fireInstanceUpserted] = createEvent<InstanceFields>()
  const [onInstanceDeleted, fireInstanceDeleted] = createEvent<InstanceId>()
  const [onUserUpserted, fireUserUpserted] = createEvent<UserFields>()
  const [onUserDeleted, fireUserDeleted] = createEvent<UserId>()
  const [onResynced, fireResynced] = createEvent<undefined>()

  const client = config.client

  const mirror: {
    users: { [_: UserId]: UserFields }
    instancesById: { [_: InstanceId]: InstanceFields }
    instancesByHost: { [_: string]: InstanceFields }
  } = { users: {}, instancesById: {}, instancesByHost: {} }

  const upsertInstance = (record: InstanceFields) => {
    dbg(`upsertInstance ${record.id}`)
    const hosts = instanceRoutingHostnames(record, EDGE_APEX_DOMAIN())
    for (const host of hosts) {
      const previous = mirror.instancesByHost[host]
      if (previous && previous.id !== record.id) throw new Error(`Conflicting instance hostname: ${host}`)
    }
    deleteInstance(record.id)
    mirror.instancesById[record.id] = record
    for (const host of hosts) mirror.instancesByHost[host] = record
    ensureInstanceDirectoryStructure(record.id, logger)
  }

  const deleteInstance = (id: InstanceId) => {
    const oldInstance = mirror.instancesById[id]
    if (!oldInstance) return
    delete mirror.instancesById[id]
    for (const host of instanceRoutingHostnames(oldInstance, EDGE_APEX_DOMAIN())) {
      if (mirror.instancesByHost[host]?.id === id) delete mirror.instancesByHost[host]
    }
  }

  const upsertUser = (record: UserFields) => {
    dbg(`upsertUser ${record.id}`)
    deleteUser(record.id)
    mirror.users[record.id] = record
  }

  const deleteUser = (id: UserId) => {
    const oldUser = mirror.users[id]
    if (oldUser) {
      delete mirror.users[oldUser.id]
    }
  }

  const applyMirrorDump = (users: UserFields[], instances: InstanceFields[]) => {
    users.forEach((user) => upsertUser(user))
    instances.forEach((instance) => upsertInstance(instance))
  }

  const syncMirror = async (options: MirrorSyncOptions = {}) => {
    const { instances = [], resetIdle = false } = options
    dbg(`syncMirror`, { live: instances.length, resetIdle })
    const {
      users,
      instances: mirrorInstances,
      updated,
    } = await client.send<MirrorSyncResponse>(`/api/mirror`, {
      method: `POST`,
      body: { instances, resetIdle },
    })
    applyMirrorDump(users, mirrorInstances)
    dbg(`synced mirror: ${mirrorInstances.length} instances, ${users.length} users, ${updated} live`)
  }

  let bootSyncDone = false
  let resolveReady!: () => void
  let rejectReady!: (reason: unknown) => void
  const whenReady = new Promise<void>((resolve, reject) => {
    resolveReady = resolve
    rejectReady = reject
  })

  const bootSync = async (options: MirrorSyncOptions = {}) => {
    if (bootSyncDone) {
      return syncMirror(options)
    }
    bootSyncDone = true
    try {
      await syncMirror(options)
      resolveReady()
    } catch (e) {
      bootSyncDone = false
      rejectReady(e)
      throw e
    }
  }

  let sseConnectedOnce = false
  client.realtime
    .subscribe(`PB_CONNECT`, () => {
      if (!sseConnectedOnce) {
        sseConnectedOnce = true
        return
      }
      dbg(`mothership SSE reconnected, syncing mirror with live instances`)
      fireResynced(undefined)
    })
    .catch(error)

  client
    .collection(`instances`)
    .subscribe<InstanceFields>(`*`, (e) => {
      const { action, record } = e
      if (action === `create` || action === `update`) {
        dbg(`instance`, { action, record })
        upsertInstance(record)
        fireInstanceUpserted(record)
      }
      if (action === `delete`) {
        dbg(`instance`, { action, record })
        deleteInstance(record.id)
        fireInstanceDeleted(record.id)
      }
    })
    .catch(error)

  client
    .collection(`users`)
    .subscribe<UserFields>(`*`, (e) => {
      const { action, record } = e
      if (action === `create` || action === `update`) {
        dbg(`user`, { action, record })
        upsertUser(record)
        fireUserUpserted(record)
      }
      if (action === `delete`) {
        dbg(`user`, { action, record })
        deleteUser(record.id)
        fireUserDeleted(record.id)
      }
    })
    .catch(error)

  const api = {
    async getInstance(id: InstanceId) {
      return mirror.instancesById[id]
    },
    async getInstanceByHost(host: string) {
      const normalized = normalizeInstanceRequestHost(host)
      return normalized ? mirror.instancesByHost[normalized] : undefined
    },
    async getUser(id: UserId) {
      return mirror.users[id]
    },
    onInstanceUpserted,
    onInstanceDeleted,
    onUserDeleted,
    onUserUpserted,
    onResynced,
    bootSync,
    syncMirror,
    whenReady,
    getInstances: () => Object.values(mirror.instancesById),
    getUsers: () => Object.values(mirror.users),
  }
  return api
})
