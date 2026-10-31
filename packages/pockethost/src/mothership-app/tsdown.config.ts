import { defineConfig } from 'tsdown'

export default defineConfig([
  {
    entry: {
      'mothership.pb': 'src/hooks/index.ts',
      mothership: 'src/lib/index.ts',
    },
    format: ['cjs'],
    dts: false,
    clean: false,
    outDir: 'pb_hooks',
    shims: true,
    deps: { skipNodeModulesBundle: false, alwaysBundle: ['qrcode-generator'] },
    fixedExtension: false,
    target: 'node24',
    platform: 'node',
    minify: false,
    sourcemap: false,
  },
  {
    entry: { _ph_totp: 'src/instance-totp.ts' },
    format: ['cjs'],
    dts: false,
    clean: false,
    outDir: '../instance-app/v23/pb_hooks',
    deps: { skipNodeModulesBundle: false, alwaysBundle: ['qrcode-generator'] },
    fixedExtension: false,
    target: 'es2015',
    platform: 'neutral',
    minify: false,
    sourcemap: false,
  },
])
