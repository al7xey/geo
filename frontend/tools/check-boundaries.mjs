import { readdir, readFile } from 'node:fs/promises';
import { resolve, relative, dirname } from 'node:path';

const root = resolve('src');
const order = ['app', 'pages', 'widgets', 'features', 'entities', 'shared'];
const problems = [];
async function walk(dir) {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const file = resolve(dir, entry.name);
    if (entry.isDirectory()) await walk(file);
    else if (/\.tsx?$/.test(file) && !file.endsWith('.test.tsx') && !file.endsWith('.test.ts')) {
      const from = relative(root, file).replaceAll('\\', '/').split('/');
      const source = await readFile(file, 'utf8');
      if (/dangerouslySetInnerHTML|\beval\(/.test(source)) problems.push(`${file}: unsafe HTML/code execution`);
      for (const match of source.matchAll(/(?:from\s*|import\s*)['"]([^'"]+)['"]/g)) {
        if (!match[1].startsWith('.')) continue;
        const to = relative(root, resolve(dirname(file), match[1])).replaceAll('\\', '/').split('/');
        const a = order.indexOf(from[0]), b = order.indexOf(to[0]);
        if (a < 0 || b < 0) continue;
        if (b < a || (a === b && !['app', 'shared'].includes(from[0]) && from[1] !== to[1])) problems.push(`${file}: forbidden FSD import ${match[1]}`);
        if (a !== b && !/\/index(?:\.tsx?)?$/.test(match[1])) problems.push(`${file}: use the slice public index: ${match[1]}`);
      }
    }
  }
}
await walk(root);
if (problems.length) throw new Error(problems.join('\n'));
console.log('FSD boundaries and safe React rendering: OK');
