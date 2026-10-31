import type { UserId } from '@'

export type VfsScope = {
  userId: UserId
  /** Null = all bases the account may administer. */
  instanceIds: string[] | null
  managedInstances?: string[]
  superAdmin?: boolean
}

export function instanceAllowedByScope(scope: VfsScope | undefined, instanceId: string, userId: string): boolean {
  if (!scope) {
    return true
  }

  if (scope.userId !== userId && !scope.superAdmin && !scope.managedInstances?.includes(instanceId)) {
    return false
  }

  if (scope.instanceIds === null) {
    return true
  }

  return scope.instanceIds.includes(instanceId)
}
