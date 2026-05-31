// Bundle the dashboard JSX into a single self-contained IIFE.
// Called by build_artifact.py as: node esbuild_bundle.mjs <in.jsx> <out.js>
//
// Why a script (vs the old `npx esbuild` CLI): we need `minify` + a production
// `process.env.NODE_ENV` define so React/Recharts ship their PRODUCTION builds
// instead of the dev builds. Passing the quoted define through the Windows
// shell is brittle; the JS API takes it cleanly. Still fully inlined — no CDN,
// no runtime network — so the offline/self-contained property is preserved.
import { build } from 'esbuild';
import { writeFileSync } from 'node:fs';

const [, , inFile, outFile] = process.argv;
if (!inFile || !outFile) {
  console.error('usage: node esbuild_bundle.mjs <in.jsx> <out.js>');
  process.exit(2);
}

const result = await build({
  entryPoints: [inFile],
  bundle: true,
  format: 'iife',
  target: 'es2017',
  platform: 'browser',
  minify: true,
  define: { 'process.env.NODE_ENV': '"production"' },
  legalComments: 'none',
  write: false,
});

writeFileSync(outFile, result.outputFiles[0].text, 'utf8');
