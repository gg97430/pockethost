/** Dashboard administrators may manage owned bases, explicitly attached bases, or every base as superadmin. */
export const canManageInstance = (instance: core.Record, user?: core.Record) =>
  !!user &&
  user.collection().name === 'users' &&
  !user.getString('suspension') &&
  (user.getBool('superAdmin') ||
    instance.getString('uid') === user.id ||
    (user.getStringSlice('managedInstances') || []).includes(instance.id))

export const instanceAccessQuery = (user: core.Record, instanceField = 'id', ownerField = 'uid') => {
  const params: Record<string, string> = { uid: user.id }
  if (user.collection().name !== 'users' || user.getString('suspension')) return { filter: '1=0', params }
  if (user.getBool('superAdmin')) return { filter: '1=1', params }
  const filters = [`${ownerField} = {:uid}`]
  for (const [index, id] of Array.from(user.getStringSlice('managedInstances') || []).entries()) {
    const key = `base${index}`
    params[key] = id
    filters.push(`${instanceField} = {:${key}}`)
  }
  return { filter: `(${filters.join(' || ')})`, params }
}
