import { logger } from '@'
import { Command } from 'commander'

type Options = {
  notify: boolean
}

export const HealthCommand = () => {
  const cmd = new Command(`health`)
    .addCommand(
      new Command(`check`)
        .description(`Check infrastructure, databases and web hosting; publish operator monitoring and alerts`)
        .option('--no-notify', 'Update monitoring without sending external notifications')
        .action(async (options: Options) => {
          logger().context({ cli: 'health:check' })
          const { info } = logger()
          info(`Starting`)
          const { checkHealth } = await import(`./checkHealth`)
          await checkHealth(options)
        })
    )
    .action(() => {
      cmd.help()
    })
  return cmd
}
