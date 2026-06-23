export const requireOperatorAdmin = (e: core.RequestEvent): models.Record => {
  const authRecord = e.auth

  if (!authRecord) {
    throw new UnauthorizedError('Authentification requise.')
  }

  if (!authRecord.getBool('superAdmin')) {
    throw new ForbiddenError('Acces superadmin requis.')
  }

  return authRecord
}
