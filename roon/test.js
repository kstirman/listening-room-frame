'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {ZoneState} = require('./state');
const {Publisher} = require('./app');
const zone = (id='zone1', image='art1', title='Track 1') => ({zone_id:id, display_name:'Room', state:'playing', outputs:[{output_id:'speaker',display_name:'Speaker'}],now_playing:{image_key:image,three_line:{line1:title,line2:'Artist',line3:'Album'}}});
test('explicit output selection follows regrouping; changes preserve album identity', () => {
 const s = new ZoneState('speaker'); s.pair('core'); s.update('Subscribed',{zones:[zone()]});
 const a=s.snapshot(); assert.equal(a.status,'PLAYING');
 s.update('Changed',{zones_removed:['zone1'],zones_added:[zone('group','art1','Track 2')]});
 const b=s.snapshot(); assert.equal(a.album_key,b.album_key); assert.notEqual(a.track_key,b.track_key);
 s.update('Changed',{zones_removed:['group']}); assert.equal(s.snapshot().status,'STOPPED');
});
test('unselected, ambiguous, paused, no art and disconnected states', () => {
 const s = new ZoneState();s.pair('core');s.update('Subscribed',{zones:[zone()]});assert.equal(s.snapshot().status,'STOPPED');
 s.outputId='speaker';s.update('Changed',{zones_added:[zone('other')]});assert.equal(s.snapshot().status,'STOPPED');
 s.update('Subscribed',{zones:[{...zone(),state:'paused'}]});assert.equal(s.snapshot().status,'PAUSED_PLAYBACK');
 s.update('Changed',{zones_changed:[zone('zone1','')]});assert.equal(s.snapshot().album_key,'');
 s.update('Unsubscribed');assert.equal(s.snapshot().status,'STOPPED');
});
test('image fetch rejects late track and disconnected core callbacks; retries errors', () => {
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'roon-test-'));
 try {
  const s=new ZoneState('speaker'),calls=[];
  const core={core_id:'core',services:{RoonApiImage:{get_image:(key,opts,cb)=>calls.push({key,cb})}}};
  const p=new Publisher(s,dir);p.pair(core);s.update('Subscribed',{zones:[zone()]});p.publish();
  s.update('Changed',{zones_changed:[zone('zone1','art2')]});p.publish();
  calls[0].cb(false,'image/jpeg',Buffer.from('old'));assert.equal(p.cached,'');p.publish();
  calls[1].cb('NetworkError');p.publish();assert.equal(calls.length,3);
  calls[2].cb(false,'image/jpeg',Buffer.from('new'));
  const snapshot=JSON.parse(fs.readFileSync(path.join(dir,'now-playing.json')));assert.match(snapshot.cover,/^[a-f0-9]{64}\.jpg$/);
  assert.equal(fs.readFileSync(path.join(dir,snapshot.cover),'utf8'),'new');
  s.update('Changed',{zones_changed:[zone('zone1','art3')]});p.publish();p.lost(core);
  calls[3].cb(false,'image/jpeg',Buffer.from('late'));assert.equal(JSON.parse(fs.readFileSync(path.join(dir,'now-playing.json'))).status,'STOPPED');
 } finally {fs.rmSync(dir,{recursive:true,force:true});}
});
test('timed out image request can retry; old completion cannot replace newer one', () => {
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'roon-test-'));
 try {
  const s=new ZoneState('speaker'),calls=[];
  const p=new Publisher(s,dir);p.pair({core_id:'c',services:{RoonApiImage:{get_image:(k,o,cb)=>calls.push(cb)}}});
  s.update('Subscribed',{zones:[zone()]});p.publish();p.pending.started-=16000;p.publish();
  calls[0](false,'image/jpeg',Buffer.from('old'));assert.equal(p.cached,'');
  calls[1](false,'image/jpeg',Buffer.from('new'));assert.notEqual(p.cached,'');
 } finally {fs.rmSync(dir,{recursive:true,force:true});}
});
