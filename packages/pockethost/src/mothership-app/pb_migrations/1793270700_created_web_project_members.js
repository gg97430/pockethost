/// <reference path="../src/types/types.d.ts" />
// Applications web : rôles par projet. Accès uniquement via les routes personnalisées (règles nulles).
// Aucune adhésion implicite : les projets existants restent gérés par les superAdmins.
migrate(
  (app) => {
    const projects = app.findCollectionByNameOrId('web_projects'),
      users = app.findCollectionByNameOrId('users')
    app.save(
      new Collection({
        name: 'web_project_members',
        type: 'base',
        listRule: null,
        viewRule: null,
        createRule: null,
        updateRule: null,
        deleteRule: null,
        fields: [
          {
            name: 'project',
            type: 'relation',
            required: true,
            collectionId: projects.id,
            cascadeDelete: true,
            maxSelect: 1,
          },
          { name: 'user', type: 'relation', required: true, collectionId: users.id, cascadeDelete: true, maxSelect: 1 },
          {
            name: 'role',
            type: 'select',
            required: true,
            values: ['owner', 'admin', 'developer', 'viewer'],
            maxSelect: 1,
          },
          { name: 'invitedBy', type: 'relation', collectionId: users.id, cascadeDelete: false, maxSelect: 1 },
          { name: 'created', type: 'autodate', onCreate: true, onUpdate: false },
          { name: 'updated', type: 'autodate', onCreate: true, onUpdate: true },
        ],
        indexes: [
          'CREATE UNIQUE INDEX idx_web_project_member ON web_project_members (project,user)',
          'CREATE INDEX idx_web_project_member_user ON web_project_members (user)',
        ],
      })
    )
  },
  (app) => app.delete(app.findCollectionByNameOrId('web_project_members'))
)
