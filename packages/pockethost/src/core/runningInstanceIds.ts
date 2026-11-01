import { execFileSync } from 'child_process'
import { INSTANCE_IMAGE_SHA } from '../common/instanceImages'
import { DOCKER_INSTANCE_IMAGE_NAME } from '../constants'
import { isCustomerInstanceContainerName } from './dockerInstance'
import { hostInstanceImageOverrides } from './instanceImages'
import { systemError } from './phError'

export const getRunningInstanceIds = (): Set<string> => {
  const ids = new Set<string>()
  // Parse outside the Docker-availability catch: malformed operator policy must never become an empty/default policy.
  const overrides = hostInstanceImageOverrides()
  const defaultReference = DOCKER_INSTANCE_IMAGE_NAME()
  try {
    const defaultId = execFileSync('docker', ['image', 'inspect', defaultReference, '--format', '{{.Id}}'], {
      encoding: 'utf8',
      stdio: ['pipe', 'pipe', 'ignore'],
    }).trim()
    if (!INSTANCE_IMAGE_SHA.test(defaultId)) throw new Error('Invalid default image identity')
    const knownImageIds = new Set([defaultId, ...Object.values(overrides)])
    const containers = execFileSync('docker', ['ps', '--no-trunc', '-q'], {
      encoding: 'utf8',
      stdio: ['pipe', 'pipe', 'ignore'],
    })
      .trim()
      .split('\n')
      .filter(Boolean)
    if (!containers.length) return ids
    if (containers.length > 2000 || containers.some((id) => !/^[a-f0-9]{64}$/.test(id)))
      throw new Error('Unexpected Docker inventory')
    const lines = execFileSync(
      'docker',
      ['inspect', '--type', 'container', '--format', '{{.Name}}\t{{.Config.Image}}\t{{.Image}}', ...containers],
      { encoding: 'utf8', stdio: ['pipe', 'pipe', 'ignore'] }
    )
      .trim()
      .split('\n')
      .filter(Boolean)
    for (const line of lines) {
      const [rawName, reference, actualId] = line.split('\t')
      const name = rawName?.replace(/^\//, '')
      if (!name || !isCustomerInstanceContainerName(name)) continue
      const legacyNamed =
        reference === defaultReference ||
        reference?.startsWith(defaultReference + ':') ||
        reference?.startsWith(defaultReference + '@')
      // A running mismatched image is still running: never let vacuum mistake it for a stopped database.
      if (knownImageIds.has(actualId || '') || legacyNamed) ids.add(name)
    }
  } catch {
    throw systemError('Docker inventory unavailable; refusing to assume that PocketBase instances are stopped.')
  }
  return ids
}
