// Runs the node:test suite.
//
// Not `node --test test/` and not a shell glob: Node 20 rejects a quoted glob and Node 26 rejects a
// bare directory, and an unquoted glob depends on the shell (cmd.exe on Windows does not expand
// one). Enumerating the files here works the same on every supported Node and every platform.
import { readdir } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const testDir = join(root, 'test');

const files = (await readdir(testDir))
	.filter((name) => name.endsWith('.test.js'))
	.sort()
	.map((name) => join(testDir, name));

if (files.length === 0) {
	console.error('test: no *.test.js files found in test/');
	process.exit(1);
}

const child = spawn(process.execPath, ['--test', ...files], { stdio: 'inherit', cwd: root });
child.on('exit', (code, signal) => process.exit(signal ? 1 : (code ?? 1)));
