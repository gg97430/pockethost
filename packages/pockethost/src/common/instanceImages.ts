/** Operator-owned image policy. This module is pure and can run in Node and PocketBase's JS VM. */
export const INSTANCE_IMAGE_ID = /^[a-z0-9]{15}$/
export const INSTANCE_IMAGE_SHA = /^sha256:[a-f0-9]{64}$/
export const INSTANCE_IMAGE_OVERRIDES_MAX_LENGTH = 32768
export const INSTANCE_IMAGE_OVERRIDES_MAX_ENTRIES = 256
export type InstanceImageOverrides = Record<string, string>
const invalid = () => new Error('Invalid operator PH_INSTANCE_IMAGE_OVERRIDES configuration.')

export const parseInstanceImageOverrides = (raw: unknown): InstanceImageOverrides => {
  const result: InstanceImageOverrides = Object.create(null)
  if (raw === undefined || raw === '') return result
  if (typeof raw !== 'string' || raw.length > INSTANCE_IMAGE_OVERRIDES_MAX_LENGTH) throw invalid()
  let value: unknown
  try {
    value = JSON.parse(raw)
  } catch {
    throw invalid()
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalid()
  const entries = Object.entries(value)
  if (entries.length > INSTANCE_IMAGE_OVERRIDES_MAX_ENTRIES) throw invalid()
  // JSON.parse accepts duplicate keys; rejecting them avoids a silently overwritten operator assignment.
  if ((raw.match(/"(?:\\.|[^"\\])*"\s*:/g) || []).length !== entries.length) throw invalid()
  for (const [id, image] of entries) {
    if (!INSTANCE_IMAGE_ID.test(id) || typeof image !== 'string' || !INSTANCE_IMAGE_SHA.test(image)) throw invalid()
    result[id] = image
  }
  return result
}

export const resolveInstanceImage = (instanceId: string, defaultImage: string, rawOverrides?: unknown) => {
  if (!INSTANCE_IMAGE_ID.test(instanceId)) throw new Error('Invalid PocketBase instance ID.')
  const overrides = parseInstanceImageOverrides(rawOverrides)
  return overrides[instanceId] || defaultImage
}

/** The private DB provider receives only assignments for the already-authorized database IDs. */
export const selectedInstanceImages = (ids: string[], rawOverrides?: unknown): InstanceImageOverrides => {
  const overrides = parseInstanceImageOverrides(rawOverrides)
  const selected: InstanceImageOverrides = Object.create(null)
  if (ids.length > 500 || ids.some((id) => !INSTANCE_IMAGE_ID.test(id))) throw new Error('Invalid database IDs.')
  for (const id of ids) if (overrides[id]) selected[id] = overrides[id]
  return selected
}
