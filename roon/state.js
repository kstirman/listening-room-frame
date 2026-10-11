'use strict';
const crypto = require('node:crypto');
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
// Transport subscriptions deliver full zone objects; seek-only changes do not
// alter the display. Never choose a room implicitly.
class ZoneState {
  constructor(outputId = '') { this.outputId = outputId; this.clear(); }
  clear() { this.coreId = ''; this.zones = new Map(); }
  pair(coreId) { this.clear(); this.coreId = coreId; }
  update(command, data = {}) {
    if (command === 'Subscribed') this.zones.clear();
    if (command === 'Unsubscribed') { this.zones.clear(); return; }
    for (const id of data.zones_removed || []) this.zones.delete(id);
    for (const zone of [...(data.zones || []), ...(data.zones_added || []), ...(data.zones_changed || [])])
      this.zones.set(zone.zone_id, zone);
  }
  snapshot() {
    const matches = [...this.zones.values()].filter(z => (z.outputs || []).some(o => o.output_id === this.outputId));
    const z = matches.length === 1 ? matches[0] : null;
    const n = z?.now_playing;
    const lines = n?.three_line || n?.two_line || n?.one_line || {};
    const text = ['line1', 'line2', 'line3'].map(k => typeof lines[k] === 'string' ? lines[k] : '');
    const imageKey = typeof n?.image_key === 'string' ? n.image_key : '';
    return {
      version: 1, updated_at: Date.now() / 1000,
      status: z?.state === 'playing' && n ? 'PLAYING' : z?.state === 'paused' ? 'PAUSED_PLAYBACK' : 'STOPPED',
      // Roon exposes display text, not canonical album IDs or classical roles.
      album_key: imageKey ? 'roon:' + hash(JSON.stringify([this.coreId, imageKey, text[2]])) : '',
      track_key: 'roon:' + hash(JSON.stringify([this.coreId, imageKey, ...text])),
      lines: text, image_key: imageKey,
      outputs: [...this.zones.values()].flatMap(z => (z.outputs || []).map(o => ({id: o.output_id, name: o.display_name, zone: z.display_name})))
    };
  }
}
module.exports = {ZoneState, hash};
