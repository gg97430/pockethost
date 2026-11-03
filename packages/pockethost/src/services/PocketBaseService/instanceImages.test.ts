import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPocketbaseService } from './index'
const state = vi.hoisted(() => ({
  runs: [] as any[],
  images: [] as string[],
  removes: [] as string[],
  wrongStore: false,
  existing: null as any,
  memoryMiB: 4096,
  fixedMemory: false,
}))
const a = 'a'.repeat(15),
  b = 'b'.repeat(15),
  imageA = 'sha256:' + 'a'.repeat(64),
  imageB = 'sha256:' + 'b'.repeat(64)
vi.mock('../../constants', () => ({ DOCKER_INSTANCE_IMAGE_NAME: () => 'benallfree/pockethost-instance' }))
vi.mock('bottleneck', () => ({
  default: class {
    schedule(fn: any) {
      return Promise.resolve().then(fn)
    }
  },
}))
vi.mock('fs', () => ({ existsSync: () => true }))
vi.mock('./databaseMemoryReservation', () => ({
  databaseMemoryReservation: () => ({
    Memory: state.memoryMiB * 1024 ** 2,
    MemoryReservation: state.fixedMemory ? state.memoryMiB * 1024 ** 2 : 0,
    MemorySwap: -1,
  }),
}))
vi.mock('@', () => {
  const log: any = { dbg: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() }
  log.create = () => log
  log.breadcrumb = () => log
  return {
    APEX_DOMAIN: () => 'app4.example.test',
    createCleanupManager: () => ({ shutdown: vi.fn() }),
    getContainerPortBinding: () => 31000,
    instanceContainerName: (id: string) => id,
    InstanceLogWriter: () => log,
    isDockerContainerNotFound: () => false,
    isDockerContainerStopBenign: () => false,
    isPlatformDockerFailure: () => false,
    isSystemError: (e: any) => e?.type === 'system',
    isUserError: (e: any) => e?.type === 'user',
    LoggerService: () => log,
    mkContainerHomePath: (p = '') => '/home/pockethost' + (p ? '/' + p : ''),
    mkInstanceDataPath: (id: string) => '/data/instances/' + id,
    mkInternalUrl: (p: number) => 'http://127.0.0.1:' + p,
    mkSingleton: (fn: any) => fn,
    PH_CONTAINER_LAUNCH_WARN_MS: () => 100000,
    PH_CONTAINER_STOP_TIMEOUT_SEC: () => 5,
    PH_MAX_CONCURRENT_DOCKER_LAUNCHES: () => 4,
    PocketBaseBinaryService: () => ({
      versions: () => ['0.39.11'],
      maxSatisfyingVersion: () => '0.39.11',
      getBinaryPath: () => '/trusted/pocketbase',
    }),
    removeNamedContainer: async (_docker: any, name: string) => {
      state.removes.push(name)
    },
    stopInstanceContainer: vi.fn(),
    systemError: (text: any) => Object.assign(Error(String(text)), { type: 'system' }),
    userError: (text: any) => Object.assign(Error(String(text)), { type: 'user' }),
    withDockerContainerConflictRetry: (fn: any) => fn(),
  }
})
vi.mock('dockerode', async () => {
  const { EventEmitter } = await import('node:events')
  return {
    default: class {
      getImage(reference: string) {
        state.images.push(reference)
        return {
          inspect: async () => ({
            Id: state.wrongStore ? imageA : reference.startsWith('sha256:') ? reference : imageA,
          }),
        }
      }
      getContainer() {
        return { inspect: async () => state.existing, wait: () => new Promise(() => {}), stop: vi.fn(), kill: vi.fn() }
      }
      run(reference: string, _cmd: any, _streams: any, options: any) {
        state.runs.push({ reference, options })
        const emitter = new EventEmitter()
        queueMicrotask(() =>
          emitter.emit('start', {
            inspect: async () => ({
              Image: reference.startsWith('sha256:') ? reference : imageA,
              Config: { Image: reference },
            }),
            stop: vi.fn(),
            kill: vi.fn(),
          })
        )
        return emitter
      }
    },
  }
})
const log: any = { dbg: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() }
log.create = () => log
log.breadcrumb = () => log
beforeEach(() => {
  state.runs = []
  state.images = []
  state.removes = []
  state.wrongStore = false
  state.existing = null
  state.memoryMiB = 4096
  state.fixedMemory = false
  vi.stubEnv('PH_INSTANCE_IMAGE_OVERRIDES', '')
})
afterEach(() => vi.unstubAllEnvs())
describe('customer Docker provider image selection', () => {
  it('applies the operator fixed reservation at container creation, before startup', async () => {
    state.memoryMiB = 32768
    state.fixedMemory = true
    const service = await createPocketbaseService({ logger: log })
    await service.spawn({ instanceId: b, subdomain: 'b', logger: log, env: {} })
    expect(state.runs[0].options.HostConfig).toMatchObject({
      Memory: 32 * 1024 ** 3,
      MemoryReservation: 32 * 1024 ** 3,
      MemorySwap: -1,
    })
    expect(state.runs[0].options.Labels['pockethost.database.memory']).toBe('fixed')
  })
  it('uses the pinned host B image in both Docker run and create options, ignoring customer env', async () => {
    vi.stubEnv('PH_INSTANCE_IMAGE_OVERRIDES', JSON.stringify({ [b]: imageB }))
    const service = await createPocketbaseService({ logger: log })
    await service.spawn({
      instanceId: b,
      subdomain: 'b',
      logger: log,
      env: { PH_INSTANCE_IMAGE_OVERRIDES: JSON.stringify({ [b]: imageA }) },
    })
    expect(state.runs).toHaveLength(1)
    expect(state.runs[0].reference).toBe(imageB)
    expect(state.runs[0].options.Image).toBe(imageB)
  })
  it('does not accept a customer-only override when the host has none', async () => {
    const service = await createPocketbaseService({ logger: log })
    await service.spawn({
      instanceId: a,
      subdomain: 'a',
      logger: log,
      env: { PH_INSTANCE_IMAGE_OVERRIDES: JSON.stringify({ [a]: imageB }) },
    })
    expect(state.runs[0].reference).toBe('benallfree/pockethost-instance')
    expect(state.runs[0].options.Image).toBe('benallfree/pockethost-instance')
  })
  it('fails before remove/create on malformed operator config or missing/wrong B image', async () => {
    vi.stubEnv('PH_INSTANCE_IMAGE_OVERRIDES', '{"bad":null}')
    await expect(createPocketbaseService({ logger: log })).rejects.toThrow()
    expect(state.removes).toEqual([])
    expect(state.runs).toEqual([])
    vi.stubEnv('PH_INSTANCE_IMAGE_OVERRIDES', JSON.stringify({ [b]: imageB }))
    state.wrongStore = true
    const service = await createPocketbaseService({ logger: log })
    await expect(service.spawn({ instanceId: b, subdomain: 'b', logger: log })).rejects.toThrow()
    expect(state.removes).toEqual([])
    expect(state.runs).toEqual([])
  })
  it('reattaches only the correct pinned B reference and immutable ID', async () => {
    vi.stubEnv('PH_INSTANCE_IMAGE_OVERRIDES', JSON.stringify({ [b]: imageB }))
    const service = await createPocketbaseService({ logger: log })
    state.existing = { State: { Running: true }, Image: imageA, Config: { Image: imageB } }
    await expect(service.attach({ instanceId: b, logger: log })).rejects.toThrow()
    state.existing = {
      State: { Running: true },
      Image: imageB,
      Config: { Image: 'benallfree/pockethost-instance:latest' },
    }
    await expect(service.attach({ instanceId: b, logger: log })).rejects.toThrow()
    state.existing = { State: { Running: true }, Image: imageB, Config: { Image: imageB } }
    expect((await service.attach({ instanceId: b, logger: log })).started()).toBe(true)
  })
})
