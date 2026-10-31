import { describe, expect, it } from 'vitest'
import { webCiDockerfile, webCiDockerignore, webCiRunnerLabels, webCiWorkflow } from './webCi'
const value = {
  appId: 'a'.repeat(15),
  imageRepository: 'ghcr.io/owner/site',
  deployUrl: 'https://app.monappli.re/api/web-apps/ci/deploy',
  branch: 'main',
  dockerfile: 'Dockerfile.pockethost-ci',
}
describe('reviewable CI workflow proposal', () => {
  it('never embeds a token and waits idempotently while GHCR credentials remain alive', () => {
    const text = webCiWorkflow({ ...value, token: 'never-write-real-secret' } as typeof value)
    expect(text).not.toContain('never-write-real-secret')
    expect(text).toContain('${{ secrets.POCKETHOST_DEPLOY_TOKEN }}')
    expect(text).toContain('registry: ghcr.io')
    expect(text).toContain('while time.monotonic() < deadline')
    expect(text).toContain('"requestId"')
    expect(text).toContain('"password": os.environ["GHCR_TOKEN"]')
    expect(text).toContain('NoRedirect')
    expect(text).not.toContain('pull_request_target')
    expect(text).toContain('cache-to: type=gha,mode=max')
    expect(text.match(/uses: [\w/-]+@[a-f0-9]{40}/g)).toHaveLength(4)
    expect(text).toContain('runs-on: "ubuntu-latest"')
    expect(text).not.toContain('auto-hébergé')
  })
  it('sends an explicit User-Agent so Cloudflare does not block the default Python-urllib client', () => {
    const text = webCiWorkflow(value)
    expect(text).toContain('"User-Agent": "kolab-hosting-ci/1"')
    expect(text).not.toMatch(/print\([^)]*(token|PHCI_TOKEN|GHCR_TOKEN)/)
  })
  it('accepts a validated self-hosted runner label list and warns in the workflow', () => {
    const text = webCiWorkflow({ ...value, runnerLabels: 'self-hosted, linux  x64.build_1' })
    expect(text).toContain('runs-on: ["self-hosted","linux","x64.build_1"]')
    expect(text).toContain('ne doit jamais tourner sur les serveurs')
    expect(webCiRunnerLabels('')).toEqual(['ubuntu-latest'])
    expect(webCiRunnerLabels(' ubuntu-24.04 ')).toEqual(['ubuntu-24.04'])
    for (const runnerLabels of ['a b c d e f', 'self-hosted,"x"', 'linux\ninjected: yes', '${{ github.actor }}', 'é'])
      expect(() => webCiWorkflow({ ...value, runnerLabels })).toThrow()
  })
  it('rejects unsafe endpoint paths and shell-like repository Dockerfile or branch strings', () => {
    for (const change of [
      { deployUrl: 'http://app.example/api/web-apps/ci/deploy' },
      { deployUrl: 'https://user:password@app.example/api/web-apps/ci/deploy' },
      { deployUrl: 'https://app.example/api/other' },
      { imageRepository: 'ghcr.io/owner/site\ninjected: bad' },
      { dockerfile: 'Dockerfile;touch /tmp/x' },
      { dockerfile: '../Dockerfile' },
      { branch: 'main"\ninjected: yes' },
    ])
      expect(() => webCiWorkflow({ ...value, ...change })).toThrow()
  })
  it('uses secret mounts and removes the temporary dotenv before committing the build layer', () => {
    expect(webCiDockerfile).toContain('--mount=type=secret')
    expect(webCiDockerfile).toContain('finally{fs.rmSync(name,{force:true})}')
    expect(webCiDockerfile).toContain('USER 10001:10001')
    expect(webCiDockerfile).toContain('pockethost.framework=nextjs pockethost.next.directory=/app/.next')
    expect(webCiDockerignore.split('\n')).toContain('.env.*')
  })
})
