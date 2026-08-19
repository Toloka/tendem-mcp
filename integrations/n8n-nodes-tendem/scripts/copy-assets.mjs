// Copies node icons and codex metadata into dist/, since tsc only emits .js/.d.ts.
// Dependency-free and cross-platform on purpose.
import { cp, mkdir, readdir } from 'node:fs/promises';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const assetPattern = /\.(svg|png|node\.json)$/;

async function collect(dir) {
	const found = [];
	for (const entry of await readdir(dir, { withFileTypes: true })) {
		const full = join(dir, entry.name);
		if (entry.isDirectory()) found.push(...(await collect(full)));
		else if (assetPattern.test(entry.name)) found.push(full);
	}
	return found;
}

const assets = await collect(join(root, 'nodes'));

for (const source of assets) {
	const target = join(root, 'dist', relative(root, source));
	await mkdir(dirname(target), { recursive: true });
	await cp(source, target);
}

console.log(`copy-assets: copied ${assets.length} asset(s) into dist/`);
