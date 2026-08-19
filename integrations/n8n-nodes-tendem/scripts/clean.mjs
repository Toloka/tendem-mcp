import { rm } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
await rm(join(root, 'dist'), { recursive: true, force: true });
console.log('clean: removed dist/');
