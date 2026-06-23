/// <reference path="../src/types/types.d.ts" />
migrate(
  (app) => {
    const collection = new Collection({
      id: 'n4sshkeys9v1k2m',
      name: 'ssh_keys',
      type: 'base',
      system: false,
      fields: [
        {
          id: 'skuser01',
          name: 'user',
          type: 'relation',
          system: false,
          hidden: false,
          required: true,
          presentable: false,
          collectionId: 'systemprofiles0',
          cascadeDelete: true,
          minSelect: 0,
          maxSelect: 1,
        },
        {
          id: 'sklabel1',
          name: 'label',
          type: 'text',
          system: false,
          hidden: false,
          required: true,
          presentable: true,
          min: 1,
          max: 100,
          pattern: '',
        },
        {
          id: 'skpubkey',
          name: 'public_key',
          type: 'text',
          system: false,
          hidden: false,
          required: true,
          presentable: false,
          min: 40,
          max: 500,
          pattern: '^ssh-ed25519 ',
        },
        {
          id: 'skfprint',
          name: 'fingerprint',
          type: 'text',
          system: false,
          hidden: false,
          required: true,
          presentable: false,
          min: 10,
          max: 100,
          pattern: '^SHA256:',
        },
        {
          id: 'skallins',
          name: 'all_instances',
          type: 'bool',
          system: false,
          hidden: false,
          required: false,
          presentable: false,
        },
        {
          id: 'skinstds',
          name: 'instances',
          type: 'relation',
          system: false,
          hidden: false,
          required: false,
          presentable: false,
          collectionId: 'etae8tuiaxl6xfv',
          cascadeDelete: false,
          minSelect: 0,
          maxSelect: 0,
        },
      ],
      indexes: [
        'CREATE INDEX `idx_ssh_keys_user` ON `ssh_keys` (`user`)',
        'CREATE INDEX `idx_ssh_keys_fingerprint` ON `ssh_keys` (`fingerprint`)',
        'CREATE UNIQUE INDEX `idx_ssh_keys_user_fingerprint` ON `ssh_keys` (`user`, `fingerprint`)',
      ],
      listRule: 'user = @request.auth.id',
      viewRule: 'user = @request.auth.id',
      createRule: '@request.auth.id != "" && user = @request.auth.id',
      updateRule: 'user = @request.auth.id',
      deleteRule: 'user = @request.auth.id',
    })

    return app.save(collection)
  },
  (app) => {
    const collection = app.findCollectionByNameOrId('n4sshkeys9v1k2m')

    return app.delete(collection)
  }
)
