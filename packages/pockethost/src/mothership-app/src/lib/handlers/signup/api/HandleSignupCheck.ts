import { error } from '../error'
import { isAvailable } from '../isAvailable'
import { generate } from '../random-words'

export const HandleSignupCheck = (e: core.RequestEvent) => {
  const instanceName = (() => {
    const name = (e.request.url.query().get('name') || '').trim()
    if (name) {
      if (name.match(/^[a-z][a-z0-9-]{2,39}$/) === null) {
        throw error(
          `instanceName`,
          `invalid`,
          `Le nom d'instance doit commencer par une lettre, contenir entre 3 et 40 caractères, et utiliser seulement a-z, 0-9 et le tiret (-).`
        )
      }
      if (isAvailable(name)) {
        return name
      }
      throw error(`instanceName`, `exists`, `Le nom d'instance ${name} n'est pas disponible.`)
    } else {
      let i = 0
      while (true) {
        i++
        if (i > 100) {
          return +new Date()
        }
        const slug = generate(2).join(`-`)
        if (isAvailable(slug)) return slug
      }
    }
  })()
  return e.json(200, { instanceName })
}
