// Bundle entry.js with esbuild (run by vendor_univer.py inside its work dir).
//   node build.mjs <outdir> <metafile>
// One change to Univer's code on the way: engine-render's table of
// hyphenation-pattern loaders is emptied. Those ~70 patterns (TeX
// hyph-utf8, under several licences including LPPL and LGPL) are only for
// automatic hyphenation of document text, which a sheet never turns on; with
// the table empty Univer simply has no pattern (its own "unknown language"
// path) and none of them is bundled.
import { build } from 'esbuild';
import { readFile, writeFile } from 'node:fs/promises';

const [outdir, metafile] = process.argv.slice(2);
const LOADERS = /const PATTERN_LOADERS = \{[\s\S]*?\n\};/;

const noHyphenation = {
  name: 'no-hyphenation-patterns',
  setup(b) {
    b.onLoad({ filter: /@univerjs.engine-render.lib.es.index\.js$/ }, async (args) => {
      const src = await readFile(args.path, 'utf8');
      if (!LOADERS.test(src)) throw new Error('engine-render: PATTERN_LOADERS not found (Univer changed?)');
      return { contents: src.replace(LOADERS, 'const PATTERN_LOADERS = {};'), loader: 'js' };
    });
  },
};

const result = await build({
  entryPoints: ['entry.js'],
  bundle: true,
  format: 'esm',
  splitting: true,
  minify: true,
  target: 'es2020',
  define: { 'process.env.NODE_ENV': '"production"' },
  outdir,
  entryNames: 'univer',
  chunkNames: 'chunks/[name]-[hash]',
  legalComments: 'none',
  metafile: true,
  logLevel: 'warning',
  plugins: [noHyphenation],
});
await writeFile(metafile, JSON.stringify(result.metafile));
