import { afterEach, describe, expect, it, vi } from 'vitest'
import { containerMatchesInstanceImage, hostInstanceImage, inspectHostInstanceImage } from './instanceImages'
vi.mock('../constants', () => ({ DOCKER_INSTANCE_IMAGE_NAME: () => 'benallfree/pockethost-instance' }))
const b = 'b'.repeat(15),
  imageB = 'sha256:' + 'b'.repeat(64),
  imageA = 'sha256:' + 'a'.repeat(64)
afterEach(() => vi.unstubAllEnvs())
describe('host image policy and immutable adoption', () => {
  it('reads only the host environment; no customer environment is a policy input', () => {
    vi.stubEnv('PH_INSTANCE_IMAGE_OVERRIDES', JSON.stringify({ [b]: imageB }))
    const customerEnv = { PH_INSTANCE_IMAGE_OVERRIDES: JSON.stringify({ [b]: imageA }) }
    expect(hostInstanceImage(b)).toBe(imageB)
    expect(customerEnv.PH_INSTANCE_IMAGE_OVERRIDES).not.toBe(process.env.PH_INSTANCE_IMAGE_OVERRIDES)
    vi.stubEnv('PH_INSTANCE_IMAGE_OVERRIDES', '')
    expect(hostInstanceImage(b)).toBe('benallfree/pockethost-instance')
  })
  it('resolves the exact Docker image and fails before spawning if the image store disagrees', async () => {
    vi.stubEnv('PH_INSTANCE_IMAGE_OVERRIDES', JSON.stringify({ [b]: imageB }))
    const inspect = vi.fn().mockResolvedValue({ Id: imageB }),
      getImage = vi.fn(() => ({ inspect }))
    expect(await inspectHostInstanceImage({ getImage } as any, b)).toEqual({ reference: imageB, imageId: imageB })
    expect(getImage).toHaveBeenCalledWith(imageB)
    inspect.mockResolvedValue({ Id: imageA })
    await expect(inspectHostInstanceImage({ getImage } as any, b)).rejects.toThrow()
  })
  it('requires both the pinned creation reference and immutable image; no apparent alias or prefix match', () => {
    const expected = { reference: imageB, imageId: imageB }
    expect(containerMatchesInstanceImage({ Image: imageB, Config: { Image: imageB } } as any, expected)).toBe(true)
    for (const info of [
      { Image: imageA, Config: { Image: imageB } },
      { Image: imageB, Config: { Image: 'benallfree/pockethost-instance:latest' } },
      { Image: imageB, Config: { Image: imageB + ':suffix' } },
    ])
      expect(containerMatchesInstanceImage(info as any, expected)).toBe(false)
    expect(
      containerMatchesInstanceImage({ Image: imageA, Config: { Image: 'legacy-alias:0.0.1' } } as any, {
        reference: 'benallfree/pockethost-instance',
        imageId: imageA,
      })
    ).toBe(true)
  })
})
