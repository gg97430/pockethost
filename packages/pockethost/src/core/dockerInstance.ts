import Docker, { ContainerInspectInfo } from 'dockerode'
import { INSTANCE_IMAGE_ID, INSTANCE_IMAGE_SHA } from '../common/instanceImages'
import { DOCKER_INSTANCE_IMAGE_NAME, MOTHERSHIP_CONTAINER_NAME } from '../constants'
import { containerMatchesInstanceImage, hostInstanceImage, hostInstanceImageOverrides } from './instanceImages'
import {
  isDockerContainerConflict,
  isDockerContainerNotFound,
  isDockerContainerStopBenign,
  systemError,
} from './phError'

const DOCKER_CONTAINER_REMOVAL_WAIT_MS = 5000
const DOCKER_CONTAINER_REMOVAL_POLL_MS = 100

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

export const instanceContainerName = (instanceId: string) => instanceId

const pocketbaseMountDestination = '/home/pockethost/pocketbase'

export const isCustomerInstanceContainerName = (name: string) =>
  name !== MOTHERSHIP_CONTAINER_NAME && INSTANCE_IMAGE_ID.test(name)

export const resolveInstanceIdFromInspect = (info: ContainerInspectInfo): string | undefined => {
  const name = info.Name?.replace(/^\//, '')
  if (!name || !isCustomerInstanceContainerName(name)) return undefined
  return name
}

export const getContainerPortBinding = (info: ContainerInspectInfo): number | undefined => {
  const ports = info.NetworkSettings?.Ports?.['8090/tcp']
  const hostPort = ports?.[0]?.HostPort
  if (!hostPort) return undefined
  const portBinding = parseInt(hostPort, 10)
  return Number.isNaN(portBinding) ? undefined : portBinding
}

export const getContainerBoundBinaryPath = (info: ContainerInspectInfo): string | undefined => {
  for (const mount of info.Mounts ?? []) {
    if (mount.Destination === pocketbaseMountDestination) {
      return mount.Source
    }
  }
  return undefined
}

export const containerMatchesBinaryPath = (info: ContainerInspectInfo, expectedBinPath: string) => {
  const bound = getContainerBoundBinaryPath(info)
  return bound === expectedBinPath
}

export type RunningInstanceContainer = {
  instanceId: string
  containerId: string
  inspect: ContainerInspectInfo
  imageMatchesPolicy: boolean
}

export const listRunningInstanceContainers = async (): Promise<RunningInstanceContainer[]> => {
  const overrides = hostInstanceImageOverrides()
  const docker = new Docker()
  const defaultReference = DOCKER_INSTANCE_IMAGE_NAME()
  const defaultImage = await docker.getImage(defaultReference).inspect()
  if (!INSTANCE_IMAGE_SHA.test(defaultImage.Id)) throw new Error('Invalid default PocketBase image identity.')
  const knownImageIds = new Set([defaultImage.Id, ...Object.values(overrides)])
  const listed = await docker.listContainers({ filters: { status: ['running'] } })
  const results: RunningInstanceContainer[] = []

  for (const summary of listed) {
    if (!summary.Names?.some((name) => isCustomerInstanceContainerName(name.replace(/^\//, '')))) continue
    const inspect = await docker.getContainer(summary.Id).inspect()
    const instanceId = resolveInstanceIdFromInspect(inspect)
    if (!instanceId) continue
    const configured = inspect.Config?.Image || ''
    const legacyNamed =
      configured === defaultReference ||
      configured.startsWith(defaultReference + ':') ||
      configured.startsWith(defaultReference + '@')
    if (!knownImageIds.has(inspect.Image) && !legacyNamed) continue
    const reference = hostInstanceImage(instanceId)
    const expected = { reference, imageId: overrides[instanceId] || defaultImage.Id }
    results.push({
      instanceId,
      containerId: summary.Id,
      inspect,
      imageMatchesPolicy: containerMatchesInstanceImage(inspect, expected),
    })
  }
  return results
}

export const stopInstanceContainer = async (instanceId: string, stopTimeoutSec: number) => {
  const docker = new Docker()
  const container = docker.getContainer(instanceContainerName(instanceId))
  await container.stop({ signal: 'SIGINT', t: stopTimeoutSec }).catch(async (e) => {
    if (isDockerContainerStopBenign(e)) return
    await container.kill().catch((killErr) => {
      if (!isDockerContainerStopBenign(killErr)) throw killErr
    })
  })
}

export const waitUntilNamedContainerRemoved = async (
  docker: Docker,
  name: string,
  timeoutMs = DOCKER_CONTAINER_REMOVAL_WAIT_MS
): Promise<void> => {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      await docker.getContainer(name).inspect()
      await sleep(Math.min(DOCKER_CONTAINER_REMOVAL_POLL_MS, deadline - Date.now()))
    } catch (e) {
      if (isDockerContainerNotFound(e)) return
      throw e
    }
  }
  throw systemError(`Timed out waiting for Docker to remove container ${name}`)
}

export const removeNamedContainer = async (docker: Docker, name: string, stopTimeoutSec: number): Promise<void> => {
  const container = docker.getContainer(name)
  try {
    const info = await container.inspect()
    if (info.State.Running) {
      await container.stop({ signal: 'SIGINT', t: stopTimeoutSec }).catch((e) => {
        if (isDockerContainerStopBenign(e) || isDockerContainerConflict(e)) return
        throw e
      })
    }
    await container.remove({ force: true })
  } catch (e) {
    if (isDockerContainerNotFound(e)) return
    if (isDockerContainerConflict(e)) {
      await waitUntilNamedContainerRemoved(docker, name)
      return
    }
    throw e
  }
}

export const withDockerContainerConflictRetry = async <T>(
  fn: () => Promise<T>,
  waitOpts: { docker: Docker; name: string; timeoutMs?: number }
): Promise<T> => {
  const timeoutMs = waitOpts.timeoutMs ?? DOCKER_CONTAINER_REMOVAL_WAIT_MS
  const deadline = Date.now() + timeoutMs
  let lastErr: unknown

  while (true) {
    try {
      return await fn()
    } catch (e) {
      lastErr = e
      if (!isDockerContainerConflict(e)) throw e
      const remaining = deadline - Date.now()
      if (remaining <= 0) break
      await waitUntilNamedContainerRemoved(waitOpts.docker, waitOpts.name, remaining)
    }
  }

  throw lastErr instanceof Error ? lastErr : new Error(String(lastErr))
}
