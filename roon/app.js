'use strict';
const fs = require('node:fs');
const path = require('node:path');
const {ZoneState, hash} = require('./state');
function atomic(file, data) {
  fs.writeFileSync(file + '.tmp', data, {mode: 0o600});
  fs.renameSync(file + '.tmp', file);
}
// Injectable I/O keeps image races, timeouts and core changes testable.
class Publisher {
  constructor(state, directory) { this.state = state; this.directory = directory; this.generation = 0; this.core = null; this.cached = ''; this.pending = null; }
  pair(core) { this.generation++; this.core = core; this.cached = ''; this.pending = null; this.state.pair(core.core_id); this.publish(); }
  lost(core) { if (core !== this.core) return; this.generation++; this.core = null; this.pending = null; this.cached = ''; this.state.clear(); this.publish(); }
  publish() {
    const s = this.state.snapshot();
    const key = s.image_key;
    delete s.image_key;
    const cacheKey = key ? hash(this.state.coreId + '\0' + key) : '';
    s.cover = cacheKey && this.cached === cacheKey ? cacheKey + '.jpg' : '';
    // Do not display an old cover while a new image is being fetched.
    atomic(path.join(this.directory, 'now-playing.json'), JSON.stringify(s));
    if (!key || !this.core || s.status !== 'PLAYING' || this.cached === cacheKey) return;
    if (this.pending && Date.now() - this.pending.started < 15000) return;
    const request = {started: Date.now(), generation: this.generation, cacheKey};
    this.pending = request;
    this.core.services.RoonApiImage.get_image(key, {scale: 'fit', width: 1600, height: 1600, format: 'image/jpeg'}, (error, type, bytes) => {
      if (this.pending !== request || this.generation !== request.generation) return;
      this.pending = null;
      const current = this.state.snapshot();
      if (error || type !== 'image/jpeg' || !Buffer.isBuffer(bytes) || bytes.length > 20000000 || current.image_key !== key) return;
      atomic(path.join(this.directory, cacheKey + '.jpg'), bytes);
      this.cached = cacheKey;
      // The Python display owns its own history copies; keep one staging cover.
      for (const file of fs.readdirSync(this.directory))
        if (/^[a-f0-9]{64}\.jpg$/.test(file) && file !== cacheKey + '.jpg') fs.unlinkSync(path.join(this.directory, file));
      this.publish();
    });
  }
}
function main({discover = true} = {}) {
  process.umask(0o077);
  const args = process.argv.slice(2);
  const allowed = new Set(['--state-dir', '--output-id', '--email']);
  const opts = {};
  for (let i = 0; i < args.length; i += 2) {
    if (!allowed.has(args[i]) || !args[i+1]) throw new Error('Usage: node app.js --state-dir PATH [--output-id ID]');
    opts[args[i]] = args[i+1];
  }
  if (!opts['--state-dir'] || !opts['--email']) throw new Error('--state-dir and --email are required');
  const dir = path.resolve(opts['--state-dir']);
  fs.mkdirSync(dir, {recursive: true, mode: 0o700});
  // The official library persists pairing in config.json relative to cwd.
  process.chdir(dir);
  const RoonApi = require('node-roon-api');
  const Transport = require('node-roon-api-transport');
  const Image = require('node-roon-api-image');
  const state = new ZoneState(opts['--output-id']);
  const publisher = new Publisher(state, dir);
  const roon = new RoonApi({
    extension_id: 'com.listeningroom.frame', display_name: 'Listening Room Frame',
    display_version: '0.1.0', publisher: 'Listening Room Frame',
    email: opts['--email'], website: 'https://github.com/kstirman/listening-room-frame', log_level: 'none',
    core_paired(core) {
      publisher.pair(core);
      console.log('Roon paired. Available outputs are listed in now-playing.json.');
      core.services.RoonApiTransport.subscribe_zones((command, data) => {
        if (publisher.core !== core) return;
        state.update(command, data); publisher.publish();
      });
    },
    core_unpaired(core) { publisher.lost(core); }
  });
  roon.init_services({required_services: [Transport, Image]});
  publisher.publish();
  const timer = setInterval(() => publisher.publish(), 5000);
  for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => {
    clearInterval(timer); state.clear(); publisher.publish(); process.exit(0);
  });
  if (discover) roon.start_discovery();
}
if (require.main === module) main();
module.exports = {Publisher, main};
