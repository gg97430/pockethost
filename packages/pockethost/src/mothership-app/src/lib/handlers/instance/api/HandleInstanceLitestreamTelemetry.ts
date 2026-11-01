import { operationJson, operationsAccess, recordInstanceAction } from './instanceOperationsAccess'
import { LITESTREAM_TELEMETRY } from './litestreamTelemetry'

const telemetry = (id: string, action: 'read' | 'verify') => {
  const root = $os.getenv('DATA_ROOT') || `${$app.dataDir()}`.replace(/\/mothership\/pb_data\/?$/, '')
  const lsroot = $os.getenv('LITESTREAM_ROOT') || `${root}/litestream`
  // Never traverse an instance-controlled directory when writing the helper.
  const script = String.raw`
import sys,pathlib,os,tempfile
p=pathlib.Path(sys.argv[1]).absolute()
if p.resolve()!=p:raise ValueError('Invalid path')
p.mkdir(parents=True,exist_ok=True,mode=0o700)
f=p/'telemetry-worker.py'
if f.is_symlink():raise ValueError('Invalid file')
fd,name=tempfile.mkstemp(prefix='.telemetry-',dir=p)
with os.fdopen(fd,'w') as out:out.write(sys.argv[2])
os.replace(name,f)
os.chmod(f,0o600)
`
  try {
    $os.cmd('python3', '-c', script, lsroot, LITESTREAM_TELEMETRY).combinedOutput()
    return JSON.parse(
      toString($os.cmd('python3', `${lsroot}/telemetry-worker.py`, root, lsroot, id, action).combinedOutput())
    )
  } catch {
    throw new BadRequestError('Le contrôle Litestream est indisponible. Réessayez dans quelques instants.')
  }
}
export const HandleInstanceLitestreamTelemetry = (e: core.RequestEvent) => {
  const { instance } = operationsAccess(e)
  return operationJson(e, telemetry(instance.id, 'read'))
}
export const HandleInstanceLitestreamVerify = (e: core.RequestEvent) => {
  const { instance, user } = operationsAccess(e)
  const value = telemetry(instance.id, 'verify')
  if (value.error) throw new BadRequestError(value.error)
  recordInstanceAction(instance, user, 'litestream.restore-check.started')
  return operationJson(e, value, 202)
}
