import { startTerminalBroker } from './terminalBroker'
startTerminalBroker().catch(() => {
  console.error('Web terminal broker unavailable: verify the private control and rootless engine configuration.')
  process.exitCode = 1
})
