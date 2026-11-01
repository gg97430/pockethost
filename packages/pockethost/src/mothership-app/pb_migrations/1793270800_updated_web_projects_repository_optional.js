/// <reference path="../src/types/types.d.ts" />
// Applications web : un projet peut regrouper des moteurs non source (image, worker, CI) sans dépôt GitHub.
// allowedRepositories : liste gérée par un superAdmin des dépôts « propriétaire/dépôt » qu'un admin de projet peut
// choisir. Vide => seul un superAdmin peut définir ou changer le dépôt d'une application du projet.
migrate(
  (app) => {
    const projects = app.findCollectionByNameOrId('web_projects')
    projects.fields.getByName('repository').required = false
    projects.fields.add(new JSONField({ name: 'allowedRepositories', maxSize: 20000 }))
    app.save(projects)
  },
  (app) => {
    const projects = app.findCollectionByNameOrId('web_projects')
    const empty = app.findRecordsByFilter('web_projects', 'repository = ""', '', 1, 0)
    if (empty.length)
      throw new Error(
        'Des projets web sans dépôt existent : rattachez-leur un dépôt ou supprimez-les avant de rendre le dépôt obligatoire.'
      )
    projects.fields.getByName('repository').required = true
    projects.fields.removeByName('allowedRepositories')
    app.save(projects)
  }
)
