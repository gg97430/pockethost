import { listVersions } from '$util/versions'
import { readOperatorSettings } from '../../operatorAdmin/operatorSettings'
import { error } from '../error'

const suggestUniqueAuthRecordUsername = (collection: string, baseUsername: string) => {
  let username = baseUsername
  for (let i = 0; i < 10; i++) {
    try {
      const total = $app.countRecords(
        collection,
        $dbx.exp('LOWER([[username]])={:username}', { username: username.toLowerCase() })
      )
      if (total === 0) break
    } catch {}
    username = baseUsername + $security.randomStringWithAlphabet(3 + i, '123456789')
  }
  return username
}

export const HandleSignupConfirm = (e: core.RequestEvent) => {
  const settings = readOperatorSettings()
  if (!settings.publicSignupEnabled) {
    throw new BadRequestError('La creation publique de compte est desactivee.')
  }

  const parsed = (() => {
    const rawBody = readerToString(e.request.body)
    try {
      const parsed = JSON.parse(rawBody)
      return parsed
    } catch (e) {
      throw new BadRequestError(`Impossible d'analyser la requête JSON. Corps reçu : ${rawBody}`, e)
    }
  })()
  const email = parsed.email?.trim().toLowerCase()
  const password = parsed.password?.trim()
  const desiredInstanceName = parsed.instanceName?.trim()
  const version = parsed.version?.trim() || listVersions()[0]

  if (!email) {
    throw error(`email`, 'required', "L'email est obligatoire")
  }

  if (!password) {
    throw error(`password`, `required`, 'Le mot de passe est obligatoire')
  }

  if (!desiredInstanceName) {
    throw error(`instanceName`, `required`, `Le nom de l'instance est obligatoire`)
  }

  const userExists = (() => {
    try {
      $app.findFirstRecordByData('users', 'email', email)
      return true
    } catch {
      return false
    }
  })()

  if (userExists) {
    throw error(`email`, `exists`, `Ce compte utilisateur existe déjà. Essayez une réinitialisation du mot de passe.`)
  }

  $app.runInTransaction((txApp) => {
    const usersCollection = $app.findCollectionByNameOrId('users')
    const instanceCollection = $app.findCollectionByNameOrId('instances')

    const user = new Record(usersCollection)
    try {
      const username = suggestUniqueAuthRecordUsername(
        'users',
        'user' + $security.randomStringWithAlphabet(5, '123456789')
      )
      user.set('username', username)
      user.set('email', email)
      user.set('subscription', settings.defaultSubscription)
      user.set('subscription_quantity', settings.defaultUserQuota)
      if (settings.autoVerifyUsers) {
        user.set('verified', true)
      }
      user.setPassword(password)
      txApp.save(user)
    } catch (e) {
      throw error(`email`, `fail`, `Impossible de créer l'utilisateur : ${e}`)
    }

    try {
      const instance = new Record(instanceCollection)
      instance.set('subdomain', desiredInstanceName)
      instance.set('uid', user.get('id'))
      instance.set('status', 'idle')
      instance.set('power', settings.defaultInstancePower)
      instance.set('syncAdmin', settings.defaultSyncAdmin)
      instance.set('autoVacuum', settings.defaultAutoVacuum)
      instance.set('dev', settings.defaultInstanceDevMode)
      instance.set('version', version)
      txApp.save(instance)
    } catch (e) {
      if (`${e}`.match(/ UNIQUE /)) {
        throw error(`instanceName`, `exists`, `Ce nom d'instance vient d'être pris. Essayez-en un autre.`)
      }
      throw error(`instanceName`, `fail`, `Impossible de créer l'instance : ${e}`)
    }

    if (!settings.autoVerifyUsers) {
      $mails.sendRecordVerification($app, user)
    }
  })

  return e.json(200, { status: 'ok' })
}
