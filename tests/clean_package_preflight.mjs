// Actual installed Pi local-package resolver. No agent, session, model or auth construction.
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, lstatSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';
import net from 'node:net';
import tls from 'node:tls';
import http from 'node:http';
import https from 'node:https';
import dgram from 'node:dgram';
import { syncBuiltinESMExports } from 'node:module';

// Refuse accidental network use in this inspected child; not an OS sandbox.
let networkAttempts = 0;
const denyNetwork = () => { networkAttempts++; throw new Error('network forbidden in public loader proof'); };
net.Socket.prototype.connect = denyNetwork;
net.connect = net.createConnection = denyNetwork;
tls.connect = denyNetwork;
http.request = http.get = https.request = https.get = denyNetwork;
dgram.createSocket = denyNetwork;
globalThis.fetch = denyNetwork;
syncBuiltinESMExports();

assert.equal(process.argv.length, 4, 'expected installed Pi root and owned fixture');
const [piDir, fixture] = process.argv.slice(2).map(p => resolve(p));
const pkg = join(fixture, 'relocated package');
const cwd = join(fixture, 'clean project');
const agentDir = join(fixture, 'agent');
assert.equal(process.env.HOME, join(fixture, 'home'));
assert.equal(process.env.PI_CODING_AGENT_DIR, agentDir);
assert.equal(process.env.PI_OFFLINE, '1');
const manifest = JSON.parse(readFileSync(join(pkg, 'package.json'), 'utf8'));
assert.deepEqual(manifest.pi, { extensions: ['./extensions/razr.ts'], skills: ['./skills'] });
assert.deepEqual(Object.keys(manifest).sort(), ['description','files','keywords','name','peerDependencies','pi','private','version']);
assert.deepEqual(manifest.peerDependencies, {
  '@earendil-works/pi-ai': '*',
  '@earendil-works/pi-coding-agent': '*',
  typebox: '*',
});
assert.equal(manifest.private, true);
assert.deepEqual(manifest.files, ['extensions','skills','runtime','scripts','adapter','README.md']);
const skillStat = lstatSync(join(pkg, 'skills/razr/SKILL.md'));
assert.ok(skillStat.isFile() && !skillStat.isSymbolicLink(), 'missing/linked declared skill');
const installed = JSON.parse(readFileSync(join(piDir, 'package.json'), 'utf8'));
assert.ok(installed.name.endsWith('/pi-coding-agent'));
const hash = raw => createHash('sha256').update(raw).digest('hex');
function snapshot(root, relative = '') {
  const result = {};
  for (const name of readdirSync(join(root, relative)).sort()) {
    const part = relative ? relative + '/' + name : name;
    const path = join(root, part);
    const stat = lstatSync(path);
    assert.equal(stat.isSymbolicLink(), false, 'linked loader fixture');
    if (stat.isDirectory()) Object.assign(result, snapshot(root, part));
    else {
      assert.ok(stat.isFile(), 'non-regular loader fixture');
      result[part] = { sha256: hash(readFileSync(path)), mode: stat.mode & 0o777 };
    }
  }
  return result;
}
const roots = { project: cwd, package: pkg, agent: agentDir, home: join(fixture,'home'), xdg: join(fixture,'xdg') };
const before = Object.fromEntries(Object.entries(roots).map(([k,p]) => [k,snapshot(p)]));
assert.deepEqual(before.agent, {});
assert.deepEqual(before.home, {});
assert.deepEqual(before.xdg, {});
// Public SDK export; missing installed layout/capability is an explicit failure.
const { DefaultResourceLoader, SettingsManager } = await import(pathToFileURL(join(piDir, 'dist/index.js')));
const settings = SettingsManager.inMemory({ packages: [{ source: pkg }], extensions: [], skills: [], prompts: [], themes: [] });
const loader = new DefaultResourceLoader({
  cwd, agentDir, settingsManager: settings,
  noPromptTemplates: true, noThemes: true, noContextFiles: true,
});
await loader.reload();
assert.deepEqual(settings.drainErrors(), []);
assert.deepEqual(loader.getExtensions().errors, []);
assert.equal(loader.getExtensions().extensions.length, 1);
assert.equal(loader.getExtensions().extensions[0].path, join(pkg, 'extensions/razr.ts'));
const commandMap = loader.getExtensions().extensions[0].commands;
assert.deepEqual([...commandMap.keys()].sort(), [
  'razr-context', 'razr-memory-inspect', 'razr-recall', 'razr-remember', 'razr-status',
]);
const notifications = [];
const commandContext = {
  cwd, mode: 'print', signal: new AbortController().signal,
  ui: { notify: (message, level) => notifications.push({ message, level }) },
};
await commandMap.get('razr-memory-inspect').handler('', commandContext);
await commandMap.get('razr-recall').handler('', commandContext);
await commandMap.get('razr-context').handler(JSON.stringify({
  schema_version: 1, request_id: 'ignored', objective: 'smoke', repository_root: '.',
  include: { source: true, tests: false, config: false, git: false, workflow: false, memory: false },
  limits: { max_files: 2, max_bytes: 1000, max_memory_records: 1 },
}), commandContext);
await commandMap.get('razr-status').handler('', commandContext);
await commandMap.get('razr-context').handler('null', commandContext);
await commandMap.get('razr-recall').handler('[]', commandContext);
await commandMap.get('razr-remember').handler('null', commandContext);
assert.ok(notifications.some(item => item.level === 'error'), 'invalid/unadopted handlers must refuse');
const contextNotice = notifications.find(item => item.level === 'info' && item.message.includes('packet_digest'));
assert.ok(contextNotice, 'context handler must return the M03 packet');
assert.match(JSON.parse(contextNotice.message).request_id, /^ctx-[0-9a-f]{32}$/);
assert.equal(readdirSync(cwd).includes('.razr'), false, 'handler smoke must not implicitly adopt');
assert.deepEqual(snapshot(cwd), before.project, 'handler smoke must not mutate project');
assert.deepEqual(snapshot(agentDir), before.agent, 'handler smoke must not mutate Pi config');
assert.deepEqual(loader.getSkills().diagnostics, []);
const skills = loader.getSkills().skills;
assert.equal(skills.length, 1);
assert.equal(skills[0].name, 'razr');
assert.equal(skills[0].filePath, join(pkg, 'skills/razr/SKILL.md'));
assert.deepEqual(loader.getPrompts().diagnostics, []);
assert.equal(loader.getPrompts().prompts.length, 0);
assert.deepEqual(loader.getThemes().diagnostics, []);
assert.equal(loader.getThemes().themes.length, 0);
assert.equal(loader.getAgentsFiles().agentsFiles.length, 0);
const after = Object.fromEntries(Object.entries(roots).map(([k,p]) => [k,snapshot(p)]));
assert.deepEqual(after, before);
for (const name of ['razr.yaml','.razr','AGENTS.md']) assert.equal(readdirSync(cwd).includes(name), false);
assert.equal(networkAttempts, 0);
console.log(JSON.stringify({
  status: 'PASS', kind: 'actual-installed-local-package-resolver-no-model',
  loaded: { skills: 1, extensions: 1, prompts: 0, themes: 0, contextFiles: 0 },
  adopted: false, unchanged: true, networkAttempts, modelInvocations: 0,
  piVersionDiagnostic: installed.version ?? null,
  skill_sha256: hash(readFileSync(skills[0].filePath)), before, after,
  limitations: 'Actual resource resolution only; no agent/client construction, interactive workflow or independent critique.',
}, null, 2));
