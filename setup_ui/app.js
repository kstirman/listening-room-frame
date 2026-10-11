'use strict';
const $ = id => document.getElementById(id);
let token = '', loaded = false, lastResult = '', savedExtras = {}, dirty = false;
async function api(path, data) {
  const response = await fetch('/api/' + path, {method:data===undefined?'GET':'POST',
    headers:{'Authorization':'Bearer '+token,'Content-Type':'application/json'},
    ...(data===undefined?{}:{body:JSON.stringify(data)})});
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || 'Request failed');
  return value;
}
function message(text) {$('message').textContent = text;}
function sourceFields() {for (const source of ['sonos','roon','upnp']) $(source+'-fields').hidden=$('source').value!==source;}
function option(select,value,label) {const item=document.createElement('option');item.value=value;item.textContent=label;select.append(item);}
function hydrate(config) {
  for(const key of ['tv','tv_model','source','roon_state_dir','renderer_control_url']) if(config[key]) $(key).value=config[key];
  $('local_art_hosts').value=(config.local_art_hosts||[]).join(', ');
  if(config.sonos_uid) {option($('rooms'),config.sonos_uid,config.sonos_room_name||'Saved room · discover to see its name');$('rooms').value=config.sonos_uid;}
  savedExtras=Object.fromEntries(['minim_control','library_root','library_resource_prefix'].filter(k=>k in config).map(k=>[k,config[k]]));
  sourceFields();
}
async function refresh() {
  try {
    const state=await api('status');
    if(!loaded){hydrate(state.config);loaded=true;if(!state.busy)lastResult=String(state.operation_id);}
    $('status').textContent=state.running?'Following playback':state.busy?'Working: '+state.operation:'Display stopped';
    $('lamp').classList.toggle('on',state.running);
    $('playing').textContent=state.playback?.title ? `${state.playback.transport}: ${state.playback.title}` : state.running?'Waiting for playback information…':'Your music continues through your existing speakers.';
    const stale=state.playback && Date.now()/1000-state.playback.updated_at>20;
    if(state.running && state.playback?.transport==='UNAVAILABLE')$('playing').textContent='Device connection unavailable. Check the connection or stop and retry.';
    if(state.running && stale)$('playing').textContent='Playback update delayed. Check the connection or stop and retry.';
    for (const id of ['discover','probe','save','check','test','start','restore']) $(id).disabled=state.busy || (dirty && ['check','test','start'].includes(id));
    $('stop').disabled=!state.busy;
    $('startup').disabled=!state.startup_supported || state.startup_installed;
    if(!state.startup_supported)$('startup-note').textContent='Automatic startup is currently supported on Linux. Keep the setup process running on this computer.';
    else if(state.startup_installed)$('startup-note').textContent='Startup service is installed. Verify it after reboot; boot without login requires user lingering.';
    if(state.failure)message(state.failure);
    const resultKey=String(state.operation_id);
    if(!state.busy && state.result && Object.keys(state.result).length && lastResult!==resultKey){
      lastResult=resultKey;
      if(!state.result.ok)message(state.result.message);
      else {
        const data=state.result.data;
        if(state.operation==='discover'){
          const selected=$('rooms').value;$('rooms').replaceChildren();option($('rooms'),'','Choose a room');
          for(const room of data.rooms)option($('rooms'),room.uid,room.name);
          if(selected && !data.rooms.some(r=>r.uid===selected))option($('rooms'),selected,'Saved room · not found');
          $('rooms').value=selected;
          $('tvs').replaceChildren();option($('tvs'),'','Choose a TV');
          for(const tv of data.tvs)option($('tvs'),JSON.stringify(tv),tv.name+' · '+tv.host);
          $('discovery').textContent=`Found ${data.tvs.length} TV(s) and ${data.rooms.length} room(s). ${data.warning||''}`;
          message('Discovery finished. Choose a TV and room, then save.');
        } else if(state.operation==='probe'){$('tv').value=data.host;$('tv_model').value=data.model;dirty=true;message('TV found: '+data.name+'. Save, then check its connection.');}
        else if(state.operation==='check')message(data.message+'\nPlayback: '+data.playback+(data.title?' · '+data.title:''));
        else message(data.message||'Step complete.');
      }
    }
  } catch(error){message('Setup connection unavailable: '+error.message);$('status').textContent='Setup disconnected';}
}
async function action(path,data={}) {try{const result=await api(path,data);if(path==='config')dirty=false;message(result.message);await refresh();}catch(error){message(error.message);}}
for(const event of ['input','change'])$('config').addEventListener(event,()=>{dirty=true;message('Unsaved choices. Save configuration before starting the display.');for(const id of ['check','test','start'])$(id).disabled=true;});
$('source').addEventListener('change',sourceFields);
$('tvs').addEventListener('change',()=>{if($('tvs').value){const tv=JSON.parse($('tvs').value);$('tv').value=tv.host;$('tv_model').value=tv.model;}});
$('config').addEventListener('submit',async event=>{event.preventDefault();await action('config', {...savedExtras,source:$('source').value,tv:$('tv').value.trim(),tv_model:$('tv_model').value.trim(),sonos_uid:$('rooms').value,sonos_room_name:$('rooms').selectedOptions[0]?.textContent||'',roon_state_dir:$('roon_state_dir').value.trim(),renderer_control_url:$('renderer_control_url').value.trim(),local_art_hosts:$('local_art_hosts').value.split(',').map(s=>s.trim()).filter(Boolean)});});
for(const id of ['discover','check','test','start','stop','restore','startup'])$(id).addEventListener('click',()=>action(id));
$('probe').addEventListener('click',()=>action('probe',{host:$('tv').value.trim()}));
$('refresh').addEventListener('click',refresh);
(async()=>{try{const session=await fetch('/api/session').then(r=>r.json());token=session.token;await refresh();setInterval(refresh,2500);}catch(error){message(error.message);}})();
