import type Docker from 'dockerode'
import type { ContainerInspectInfo } from 'dockerode'
import { INSTANCE_IMAGE_SHA, parseInstanceImageOverrides, resolveInstanceImage } from '../common/instanceImages'
import { DOCKER_INSTANCE_IMAGE_NAME } from '../constants'

/** Never read a SpawnConfig.env, instance secret or any request field for this policy. */
export const hostInstanceImageOverrides = () => parseInstanceImageOverrides(process.env.PH_INSTANCE_IMAGE_OVERRIDES)
export const hostInstanceImage = (instanceId: string) =>
  resolveInstanceImage(instanceId, DOCKER_INSTANCE_IMAGE_NAME(), process.env.PH_INSTANCE_IMAGE_OVERRIDES)

export type ResolvedInstanceImage = { reference: string; imageId: string }
export const inspectHostInstanceImage = async (docker: Docker, instanceId: string): Promise<ResolvedInstanceImage> => {
  const reference = hostInstanceImage(instanceId)
  const info = await docker.getImage(reference).inspect()
  if (!INSTANCE_IMAGE_SHA.test(info.Id) || (INSTANCE_IMAGE_SHA.test(reference) && reference !== info.Id))
    throw new Error('Configured PocketBase image is unavailable or has an unexpected identity.')
  return { reference, imageId: info.Id }
}
export const containerMatchesInstanceImage = (info: ContainerInspectInfo, expected: ResolvedInstanceImage) =>
  info.Image === expected.imageId &&
  (!INSTANCE_IMAGE_SHA.test(expected.reference) || info.Config?.Image === expected.reference)
