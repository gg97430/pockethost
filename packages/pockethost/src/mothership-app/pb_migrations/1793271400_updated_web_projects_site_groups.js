/// <reference path="../src/types/types.d.ts" />
// Applications web : un projet devient un groupe de sites. multiRepository = true : ses sites peuvent venir de dépôts
// différents (le dépôt de chaque application reste contrôlé par canSetWebRepository) et `repository` ne sert plus
// qu'aux previews. Projets existants conservés à false (mono-dépôt, comportement inchangé). Aucune adhésion ni aucun
// rattachement n'est modifié : aucune application ne change de projet ni de droits.
migrate(
  (app) => {
    const projects = app.findCollectionByNameOrId('web_projects')
    projects.fields.add(new BoolField({ name: 'multiRepository' }))
    app.save(projects)
    app.db().newQuery('UPDATE web_projects SET multiRepository = FALSE').execute()
  },
  (app) => {
    const groups = app.findRecordsByFilter('web_projects', 'multiRepository = true', '', 1, 0)
    if (groups.length)
      throw new Error(
        'Des groupes de sites existent : supprimez-les ou rendez-les mono-dépôt avant de retirer multiRepository.'
      )
    const projects = app.findCollectionByNameOrId('web_projects')
    projects.fields.removeByName('multiRepository')
    app.save(projects)
  }
)
