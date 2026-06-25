import {
  APP_URL,
  asyncExitHook,
  DOC_URL,
  INSTANCE_APP_HOOK_DIR,
  INSTANCE_APP_MIGRATIONS_DIR,
  InstanceFields,
  InstanceId,
  InstanceLogWriter,
  InstanceStatus,
  isSystemError,
  isUserError,
  LoggerService,
  mkContainerHomePath,
  mkInstanceUrl,
  mkSingleton,
  MothershipAdminClientService,
  now,
  PocketbaseService,
  proxyService,
  setInstanceTrafficReady,
  SingletonBaseConfig,
  SpawnConfig,
  stringify,
  tryFetch,
  UserFields,
  userError,
  VacuumLockService,
} from '@'
import Bottleneck from 'bottleneck'
import { globSync } from 'fs'
import { basename, join } from 'path'
import { AsyncReturnType } from 'type-fest'
import { MothershipMirrorService, type MirrorLiveInstance } from '../MothershipMirrorService'
import { instanceAppVersionFromPbVersion } from './instanceAppVersion'
import { reconcilePreservedContainers } from './reconcilePreservedContainers'

enum InstanceApiStatus {
  Starting = 'starting',
  Healthy = 'healthy',
  ShuttingDown = 'shutdown',
}

type InstanceApi = {
  internalUrl: string
  startRequest: () => () => void
  shutdown: () => void
  detach: () => void
  isLowering: () => boolean
  whenLowered: () => Promise<void>
}

export type InstanceServiceConfig = SingletonBaseConfig & {
  instanceApiTimeoutMs: number
  instanceApiCheckIntervalMs: number
}

export type InstanceServiceApi = AsyncReturnType<typeof instanceService>
export const instanceService = mkSingleton(async (config: InstanceServiceConfig) => {
  const instanceServiceLogger = (config.logger ?? LoggerService()).create('InstanceService')
  const { dbg, raw, error, warn } = instanceServiceLogger
  const { client } = await MothershipAdminClientService()

  const pbService = await PocketbaseService()

  const mirror = await MothershipMirrorService()

  const instanceApis: { [_: InstanceId]: Promise<InstanceApi> } = {}
  const warmStarts: { [_: InstanceId]: Promise<void> | undefined } = {}
  const warmStartFailedAt: { [_: InstanceId]: number | undefined } = {}
  const gatewayPending: { [_: InstanceId]: number } = {}
  const warmStartRetryMs = 30_000

  const bumpGatewayPending = (id: InstanceId) => {
    gatewayPending[id] = (gatewayPending[id] ?? 0) + 1
    return () => {
      gatewayPending[id] = Math.max(0, (gatewayPending[id] ?? 1) - 1)
    }
  }

  const vacuumLocks = await VacuumLockService(config)
  vacuumLocks.registerIsLive((id) => Boolean(instanceApis[id]))

  const markInstanceIdle = async (id: InstanceId, reason: string) => {
    try {
      await client.updateInstance(id, { status: InstanceStatus.Idle })
      dbg(`Marked ${id} idle (${reason})`)
    } catch {
      warn(
        `Could not update instance fields for ${id}; status will catch up if still running when mothership returns (mothership boot resets all instances to idle)`
      )
    }
  }

  const shutdownRunningInstance = async (id: InstanceId, reason: string) => {
    const pending = instanceApis[id]
    if (!pending) return
    dbg(`Shutting down ${id}: ${reason}`)
    const api = await pending
    api.shutdown()
  }

  const handlePowerOff = async (instance: InstanceFields) => {
    const { id, status } = instance
    if (status === InstanceStatus.Idle) return

    const pending = instanceApis[id]
    if (pending) {
      dbg(`Shutting down ${id}: power off`)
      const api = await pending
      api.shutdown()
      return
    }

    dbg(`No live instance for ${id} on power off; marking idle`)
    await markInstanceIdle(id, 'power off')
  }

  const resolveLiveStatus = async (
    pending: Promise<InstanceApi>
  ): Promise<InstanceStatus.Running | InstanceStatus.Starting> => {
    try {
      await Promise.race([
        pending,
        new Promise<never>((_, reject) => {
          setTimeout(() => reject(new Error(`still starting`)), 0)
        }),
      ])
      return InstanceStatus.Running
    } catch {
      return InstanceStatus.Starting
    }
  }

  const getLiveInstances = async (): Promise<MirrorLiveInstance[]> => {
    return Promise.all(
      Object.entries(instanceApis).map(async ([id, pending]) => ({
        id,
        status: await resolveLiveStatus(pending),
      }))
    )
  }

  mirror.onResynced(() => {
    getLiveInstances()
      .then((instances) => mirror.syncMirror({ instances }))
      .catch((e) => {
        error(`Error syncing mirror after mothership reconnect`, { e })
      })
  })

  const createInstanceApi = async (instance: InstanceFields, preserved = false): Promise<InstanceApi> => {
    const { id, subdomain, version } = instance
    const systemInstanceLogger = instanceServiceLogger.create(subdomain).breadcrumb(id).breadcrumb(version)
    const { dbg, warn, error, info, trace } = systemInstanceLogger
    const userInstanceLogger = InstanceLogWriter(instance.id, `exec`, systemInstanceLogger)

    dbg(preserved ? `Reattaching preserved container` : `Starting`)
    userInstanceLogger.info(
      preserved ? `Instance reconnectée après le redémarrage du daemon.` : `Démarrage de l'instance.`
    )

    let _shutdownReason: Error | undefined
    let internalUrl: string | undefined

    // Declare api variable early to avoid temporal dead zone
    let api: InstanceApi

    const clientLimiter = new Bottleneck({ maxConcurrent: 1 })
    const updateInstance = clientLimiter.wrap((id: InstanceId, fields: Partial<InstanceFields>) => {
      dbg(`Updating instance fields`, fields)
      return client
        .updateInstance(id, fields)
        .then(() => {
          dbg(`Updated instance fields`, fields)
        })
        .catch(() => {
          warn(
            `Could not update instance fields for ${id}; status will catch up if still running when mothership returns (mothership boot resets all instances to idle)`
          )
        })
    })
    const updateInstanceStatus = (id: InstanceId, status: InstanceStatus) => updateInstance(id, { status })

    let openRequestCount = 0
    let childProcess: Awaited<ReturnType<typeof pbService.spawn>> | undefined

    let shutdownInProgress = false
    const lowerDrawbridge = () => {
      if (shutdownInProgress) {
        dbg(`Shutdown already in progress`)
        return false
      }
      shutdownInProgress = true
      dbg(`Lowering drawbridge for ${id}`)
      delete instanceApis[id]
      return true
    }

    let shutdown: () => void
    let detach: () => void

    try {
      if (!preserved) {
        /** Mark the instance as starting */
        dbg(`Starting instance`)
        updateInstanceStatus(instance.id, InstanceStatus.Starting)
      }

      if (preserved) {
        childProcess = await pbService.attach({ instanceId: instance.id, logger: systemInstanceLogger })
      } else {
        /** Create spawn config */
        const instanceAppVersion = (() => {
          try {
            return instanceAppVersionFromPbVersion(instance.version)
          } catch {
            throw userError(`Invalid version: ${instance.version}`)
          }
        })()
        const spawnArgs: SpawnConfig = {
          subdomain: instance.subdomain,
          instanceId: instance.id,
          dev: instance.dev,
          extraBinds: [
            globSync(join(INSTANCE_APP_MIGRATIONS_DIR(instanceAppVersion), '*.js')).map(
              (file) => `${file}:${mkContainerHomePath(`pb_migrations/${basename(file)}`)}:ro`
            ),
            globSync(join(INSTANCE_APP_HOOK_DIR(instanceAppVersion), '*.js')).map(
              (file) => `${file}:${mkContainerHomePath(`pb_hooks/${basename(file)}`)}:ro`
            ),
          ].flat(),
          env: {
            ...instance.secrets,
            PH_APP_NAME: instance.subdomain,
            PH_INSTANCE_URL: mkInstanceUrl(instance),
          },
          version,
          logger: systemInstanceLogger,
        }

        /** Best-effort admin sync — spawn proceeds if mothership is unavailable */
        if (instance.syncAdmin) {
          const uid = instance.uid
          dbg(`Fetching token info for uid ${uid}`)
          try {
            const { email, tokenKey, passwordHash } = await client.getUserTokenInfo({ id: uid })
            dbg(`Token info is`, { email, tokenKey, passwordHash })
            spawnArgs.env!.ADMIN_SYNC = stringify({
              id: uid,
              email,
              tokenKey,
              passwordHash,
            })
          } catch {
            warn(`Could not fetch admin sync for ${id}; launching without ADMIN_SYNC (mothership may be unavailable)`)
            userInstanceLogger.info(
              `La synchro admin a été ignorée sur ce lancement car le plan de contrôle était indisponible. Si la connexion admin échoue, éteignez puis relancez l'instance.`
            )
          }
        }

        childProcess = await pbService.spawn(spawnArgs)
      }

      const { exitCode, stopped, started, url: internalUrl } = childProcess

      shutdown = () => {
        if (!lowerDrawbridge()) return
        dbg(`Shutting down instance ${id}`)
        userInstanceLogger.info(`Arrêt de l'instance.`)
        updateInstanceStatus(id, InstanceStatus.Idle)
        if (!stopped()) {
          dbg(`Stopping container ${id}`)
          childProcess!.kill().catch((err) => {
            error(`Error killing ${id}`, { err })
          })
        }
      }

      detach = () => {
        if (!lowerDrawbridge()) return
        dbg(`Detaching from ${id}, leaving Docker container running`)
      }

      const whenLowered = exitCode.then(() => undefined)
      exitCode.then((code) => {
        if (shutdownInProgress) return
        dbg(`Instance exited unexpectedly with code ${code}`)
        lowerDrawbridge()
        userInstanceLogger.info(`Instance arrêtée.`)
        updateInstanceStatus(id, InstanceStatus.Idle)
      })

      /** Health check */
      await tryFetch(`${internalUrl}/api/health`, {
        preflight: async () => {
          const current = await mirror.getInstance(id)
          if (current && !current.power) throw userError(`Instance éteinte pendant le démarrage`)
          if (stopped()) throw userError(`Conteneur arrêté ${id}`)
          return started()
        },
        logger: systemInstanceLogger,
      })

      // Now assign the api object
      api = {
        internalUrl,
        startRequest: () => {
          openRequestCount++
          trace(`started new request`)
          return () => {
            openRequestCount--
            trace(`ended request (${openRequestCount} still open)`)
          }
        },
        shutdown,
        detach,
        isLowering: () => shutdownInProgress,
        whenLowered: () => whenLowered,
      }

      dbg(`${internalUrl} is running`)
      updateInstanceStatus(instance.id, InstanceStatus.Running)

      return api
    } catch (e) {
      if (isUserError(e)) {
        dbg(`Spawn failed: ${e}`)
      } else {
        error(`Error spawning: ${e}`)
      }
      userInstanceLogger.error(`Error spawning: ${e}`)
      lowerDrawbridge()
      updateInstanceStatus(id, InstanceStatus.Idle)
      await childProcess?.kill().catch(() => {})
      throw e
    }
  }

  mirror.onInstanceDeleted((instanceId) => {
    shutdownRunningInstance(instanceId, 'instance deleted').catch((e) => {
      error(`Error shutting down ${instanceId} on delete`, { e })
    })
  })

  const mkInstanceApiPromise = (instance: InstanceFields, requestId: string, preserved = false) => {
    const start = now()
    return createInstanceApi(instance, preserved).catch((e) => {
      const duration = now() - start
      if (isUserError(e)) {
        dbg(`Container ${instance.id} failed to launch in ${duration}ms`)
      } else {
        warn(`Container ${instance.id} failed to launch in ${duration}ms`)
      }

      delete instanceApis[instance.id]

      if (isSystemError(e)) {
        throw e
      }

      throw userError(
        `Impossible de lancer le conteneur. Consultez les logs de votre instance depuis le dashboard ou contactez le support. [${requestId}]`
      )
    })
  }

  const ensureInstanceApi = async (instance: InstanceFields, requestId: string): Promise<InstanceApi> => {
    while (true) {
      if (!instanceApis[instance.id]) {
        instanceApis[instance.id] = mkInstanceApiPromise(instance, requestId)
      }

      const api = await instanceApis[instance.id]!

      if (!api.isLowering()) {
        return api
      }

      dbg(`Instance ${instance.id} is lowering, waiting before raising (${requestId})`)
      await api.whenLowered()
      delete instanceApis[instance.id]
    }
  }

  const canUserRunInstances = (user: UserFields) => {
    if (!user.verified) return false
    if (user.subscription_quantity === 0) return false
    if (user.suspension) return false
    return true
  }

  const canKeepInstanceWarm = (instance: InstanceFields, owner: UserFields) => {
    if (!instance.power) return false
    if (instance.suspension) return false
    return canUserRunInstances(owner)
  }

  const warmPoweredInstance = async (instance: InstanceFields, reason: string) => {
    if (!instance.power) return
    if (instanceApis[instance.id]) return
    if (warmStarts[instance.id]) return warmStarts[instance.id]

    const failedAt = warmStartFailedAt[instance.id] ?? 0
    if (failedAt && now() - failedAt < warmStartRetryMs) {
      dbg(`Skipping warm start for ${instance.id}; retry cooldown active`)
      return
    }

    const warmStart = (async () => {
      const owner = await mirror.getUser(instance.uid)
      if (!owner) {
        warn(`Cannot keep ${instance.id} warm: owner ${instance.uid} is not mirrored`)
        return
      }

      if (!canKeepInstanceWarm(instance, owner)) {
        dbg(`Not keeping ${instance.id} warm; instance or owner is not eligible`)
        return
      }

      try {
        dbg(`Keeping ${instance.id} warm (${reason})`)
        await ensureInstanceApi(instance, `warm:${reason}`)
        delete warmStartFailedAt[instance.id]
      } catch (e) {
        warmStartFailedAt[instance.id] = now()
        warn(`Warm start failed for ${instance.id}`, { e })
      }
    })().finally(() => {
      delete warmStarts[instance.id]
    })

    warmStarts[instance.id] = warmStart
    return warmStart
  }

  mirror.onInstanceUpserted((instance) => {
    if (!instance.power) {
      handlePowerOff(instance).catch((e) => {
        error(`Error handling power off for ${instance.id}`, { e })
      })
      return
    }

    warmPoweredInstance(instance, 'instance upserted').catch((e) => {
      error(`Error keeping ${instance.id} warm`, { e })
    })
  })

  mirror.onUserUpserted((user) => {
    const userInstances = mirror.getInstances().filter((instance) => instance.uid === user.id)

    if (!canUserRunInstances(user)) {
      userInstances.forEach((instance) => {
        shutdownRunningInstance(instance.id, 'owner no longer eligible').catch((e) => {
          error(`Error shutting down ${instance.id} after owner update`, { e })
        })
      })
      return
    }

    userInstances.forEach((instance) => {
      warmPoweredInstance(instance, 'owner upserted').catch((e) => {
        error(`Error keeping ${instance.id} warm after owner update`, { e })
      })
    })
  })

  asyncExitHook(async () => {
    setInstanceTrafficReady(false)
    dbg(`Detaching instance manager, leaving Docker containers running`)
    await Promise.all(Object.values(instanceApis).map(async (api) => (await api).detach()))
  })

  const getMothershipInstance = async (instanceId: InstanceId): Promise<InstanceFields | undefined> => {
    try {
      return await client.getInstance(instanceId)
    } catch {
      return undefined
    }
  }

  await reconcilePreservedContainers({
    getInstance: getMothershipInstance,
    pbService,
    isAlreadyManaged: (instanceId) => Boolean(instanceApis[instanceId]),
    adoptInstance: async (instance) => {
      instanceApis[instance.id] = mkInstanceApiPromise(instance, 'boot', true)
      await instanceApis[instance.id]
    },
    logger: instanceServiceLogger,
  })

  await mirror.bootSync({ resetIdle: true, instances: await getLiveInstances() })
  await Promise.all(mirror.getInstances().map((instance) => warmPoweredInstance(instance, 'boot')))
  ;(await proxyService()).use(async (req, res, next) => {
    const logger = (config.logger ?? LoggerService()).create(`InstanceRequest`)

    const { dbg, warn, error } = logger

    if (req.path === `/logs` || req.path.startsWith(`/logs/`)) {
      next()
      return
    }

    const { host, proxy } = res.locals

    const instance = await mirror.getInstanceByHost(host)
    if (!instance) {
      res.status(404).end(`${host} introuvable`)
      return
    }
    logger.breadcrumb(`i:${instance.id}`)
    const owner = await mirror.getUser(instance.uid)
    if (!owner) {
      throw new Error(`Le propriétaire de l'instance est invalide`)
    }
    logger.breadcrumb(`u:${owner.id}`)

    /*
        Suspension check
        */
    dbg(`Checking for suspension`)
    if (owner.suspension) {
      throw userError(owner.suspension)
    }
    if (instance.suspension) {
      throw userError(instance.suspension)
    }

    /*
        Active instance check
        */
    dbg(`Checking for active instances`)
    if (owner.subscription_quantity === 0) {
      throw userError(`Les instances ne fonctionneront pas tant que vous n'aurez pas <a href=${APP_URL(`access`)}>changé d'offre</a>`)
    }

    /*
        power check
        */
    dbg(`Checking for power`)
    if (!instance.power) {
      throw userError(`Cette instance est éteinte. Consultez ${DOC_URL(`power`)} pour plus d'informations.`)
    }

    /*
        Owner check
        */
    dbg(`Checking for verified account`)
    if (!owner.verified) {
      throw userError(`Connectez-vous sur ${APP_URL()} pour vérifier votre compte.`)
    }

    if (vacuumLocks.isLocked(instance.id)) {
      dbg(`Waiting for vacuum lock on ${instance.id}`)
      const unlocked = await vacuumLocks.waitUntilUnlocked(instance.id, {
        isAborted: () => req.aborted || res.writableEnded || res.headersSent,
      })
      if (!unlocked) {
        throw userError(
          `Cette instance est temporairement indisponible en raison d'une maintenance de base de données. Veuillez réessayer dans quelques minutes.`
        )
      }
    }

    const releaseGatewayPending = bumpGatewayPending(instance.id)

    try {
      const api = await ensureInstanceApi(instance, res.locals.requestId)

      const endRequest = api.startRequest()
      const onClose = () => {
        endRequest()
        releaseGatewayPending()
      }
      res.on('close', onClose)
      if (req.closed) {
        dbg(`Request already closed. ${res.locals.requestId}`)
      }

      dbg(`Forwarding proxy request for ${req.url} to instance ${api.internalUrl}`)

      await proxy.web(req, res, { target: api.internalUrl })
    } catch (e) {
      releaseGatewayPending()
      throw e
    }
  })

  setInstanceTrafficReady(true)
  instanceServiceLogger.info(`Instance traffic ready`)

  return {}
})
