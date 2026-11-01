import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { listRunningInstanceContainers } from './dockerInstance'
import { getRunningInstanceIds } from './runningInstanceIds'
const state = vi.hoisted(() => ({
  rows: [] as any[],
  infos: {} as Record<string, any>,
  cliFailure: false,
  cliCalls: [] as any[],
}))
const imageA = 'sha256:' + 'a'.repeat(64),
  imageB = 'sha256:' + 'b'.repeat(64),
  a = 'a'.repeat(15),
  b = 'b'.repeat(15),
  bad = 'c'.repeat(15)
vi.mock('../constants', () => ({
  DOCKER_INSTANCE_IMAGE_NAME: () => 'benallfree/pockethost-instance',
  MOTHERSHIP_CONTAINER_NAME: 'pockethost-mothership',
}))
vi.mock('dockerode', () => ({
  default: class {
    getImage() {
      return { inspect: async () => ({ Id: imageA }) }
    }
    async listContainers() {
      return state.rows
    }
    getContainer(id: string) {
      return { inspect: async () => state.infos[id] }
    }
  },
}))
vi.mock('child_process', () => ({
  execFileSync: (_command: string, args: string[]) => {
    state.cliCalls.push(args)
    if (state.cliFailure) throw Error('Docker unavailable')
    if (args[0] === 'image') return imageA + '\n'
    if (args[0] === 'ps') return Object.keys(state.infos).join('\n') + '\n'
    if (args[0] === 'inspect')
      return (
        Object.values(state.infos)
          .map((info: any) => [info.Name, info.Config.Image, info.Image].join('\t'))
          .join('\n') + '\n'
      )
    throw Error('unexpected command')
  },
}))
beforeEach(() => {
  vi.stubEnv('PH_INSTANCE_IMAGE_OVERRIDES', JSON.stringify({ [b]: imageB }))
  state.rows = []
  state.infos = {}
  state.cliCalls = []
  state.cliFailure = false
  const add = (id: string, name: string, image: string, reference: string) => {
    state.rows.push({ Id: id, Names: ['/' + name] })
    state.infos[id] = { Id: id, Name: '/' + name, Image: image, Config: { Image: reference } }
  }
  add('1'.repeat(64), a, imageA, 'benallfree/pockethost-instance:0.0.1')
  add('2'.repeat(64), b, imageB, imageB)
  add('3'.repeat(64), 'pockethost-mothership', imageA, 'benallfree/pockethost-instance')
  add('4'.repeat(64), bad, 'sha256:' + 'f'.repeat(64), 'unrelated/application:latest')
})
afterEach(() => vi.unstubAllEnvs())
describe('runtime image inventory and adoption provenance', () => {
  it('recognizes ID-only pinned B and default A, while excluding mothership and unrelated images', async () => {
    const rows = await listRunningInstanceContainers()
    expect(rows.map((x) => x.instanceId)).toEqual([a, b])
    expect(rows.every((x) => x.imageMatchesPolicy)).toBe(true)
    expect([...getRunningInstanceIds()]).toEqual([a, b])
  })
  it('flags a running B container on A for refusal/recreation but still protects it from vacuum', async () => {
    state.infos['2'.repeat(64)].Image = imageA
    state.infos['2'.repeat(64)].Config.Image = 'benallfree/pockethost-instance:latest'
    const rows = await listRunningInstanceContainers()
    expect(rows.find((x) => x.instanceId === b)?.imageMatchesPolicy).toBe(false)
    expect(getRunningInstanceIds().has(b)).toBe(true)
  })
  it('fails closed for malformed operator policy and unavailable Docker inventory', async () => {
    vi.stubEnv('PH_INSTANCE_IMAGE_OVERRIDES', 'not-json')
    await expect(listRunningInstanceContainers()).rejects.toThrow()
    expect(() => getRunningInstanceIds()).toThrow()
    expect(state.cliCalls).toEqual([])
    vi.stubEnv('PH_INSTANCE_IMAGE_OVERRIDES', '')
    state.cliFailure = true
    expect(() => getRunningInstanceIds()).toThrow(/refusing to assume/)
  })
})
