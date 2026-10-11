'use strict';
const $=id=>document.getElementById(id);
let token='', loaded=false, dirty=false, currentStep=0, lastResult='', savedExtras={}, latest={}, imageRevision='', imageURL='', imageBusy=false;
const covers=new Map();
async function api(path,data){
 const response=await fetch('/api/'+path,{method:data===undefined?'GET':'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},...(data===undefined?{}:{body:JSON.stringify(data)})});
 const value=await response.json();if(!response.ok)throw new Error(value.error||'Request failed');return value;
}
function message(text){$('message').textContent=text;$('message').hidden=!text;}
function showNow(){ $('setup').hidden=true;$('listening').hidden=false; }
function showSetup(){ $('setup').hidden=false;$('listening').hidden=true;step(currentStep); }
function step(number){currentStep=Math.max(0,Math.min(4,number));document.querySelectorAll('[data-panel]').forEach(el=>el.hidden=Number(el.dataset.panel)!==currentStep);document.querySelectorAll('[data-step]').forEach(el=>{if(Number(el.dataset.step)===currentStep)el.setAttribute('aria-current','step');else el.removeAttribute('aria-current');});$('back').disabled=currentStep===0;$('next').hidden=currentStep===4;$('step-count').textContent=String(currentStep+1).padStart(2,'0')+' / 05';$('next').textContent=currentStep===2?'Continue for now →':'Next →';}
function sourceFields(){for(const source of ['sonos','roon','upnp'])$(source+'-fields').hidden=$('source').value!==source;}
function option(select,value,label){const el=document.createElement('option');el.value=value;el.textContent=label;select.append(el);}
function hydrate(config){for(const key of ['tv','tv_model','source','roon_state_dir','renderer_control_url'])if(config[key])$(key).value=config[key];$('local_art_hosts').value=(config.local_art_hosts||[]).join(', ');if(config.sonos_uid){option($('rooms'),config.sonos_uid,config.sonos_room_name||'Saved room · find devices to see its name');$('rooms').value=config.sonos_uid;}savedExtras=Object.fromEntries(['minim_control','library_root','library_resource_prefix'].filter(k=>k in config).map(k=>[k,config[k]]));sourceFields();}
function choices(){return {...savedExtras,source:$('source').value,tv:$('tv').value.trim(),tv_model:$('tv_model').value.trim(),sonos_uid:$('rooms').value,sonos_room_name:$('rooms').selectedOptions[0]?.textContent||'',roon_state_dir:$('roon_state_dir').value.trim(),renderer_control_url:$('renderer_control_url').value.trim(),local_art_hosts:$('local_art_hosts').value.split(',').map(s=>s.trim()).filter(Boolean)};}
function changed(){dirty=true;message('Your choices have changed. Save them in “Say hello” before starting.');controls(latest);}
function controls(state){
 for(const id of ['discover','probe','save','check','test','preview','setup-preview','start','restore'])$(id).disabled=!!state.busy || (dirty&&['check','test','preview','setup-preview','start'].includes(id));
 $('stop').hidden=!state.busy;$('stop').textContent=state.running?'Stop & return to original art':'Cancel this step';$('start').hidden=!!state.running;$('preview').hidden=!state.config?.source;$('begin').hidden=!!state.config?.source;
 if(!state.config?.source)$('start').hidden=true;
 $('startup').disabled=!state.startup_supported||state.startup_installed;
 $('startup-note').textContent=!state.startup_supported?'Automatic startup currently supports Linux. On this computer, keep the setup process running.':state.startup_installed?'The startup service is installed. Verify it after reboot; boot without login needs user lingering.':'Enable the Linux user service for future sessions. Boot without login needs user lingering and a reboot check.';
}
function timeLabel(value){return new Date(value*1000).toLocaleTimeString([],{hour:'numeric',minute:'2-digit',second:'2-digit'});}
async function previewImage(p){
 if(!p.revision||p.revision===imageRevision||imageBusy)return;
 imageBusy=true;
 try{const response=await fetch('/api/image/current',{headers:{Authorization:'Bearer '+token}});if(!response.ok)return;const revision=response.headers.get('X-Image-Revision');if(revision!==p.revision)return;const blob=await response.blob();const next=URL.createObjectURL(blob);$('tv-image').src=next;$('tv-image').hidden=false;$('empty-image').hidden=true;if(imageURL)URL.revokeObjectURL(imageURL);imageURL=next;imageRevision=p.revision;}finally{imageBusy=false;}
}
async function recent(items){
 const signature=items.map(item=>item.image).join('|');if($('recent').dataset.signature===signature)return;
 $('recent').dataset.signature=signature;
 const figures=[];
 for(const item of items){
  let url=covers.get(item.image);if(!url){try{const r=await fetch('/api/cover/'+item.image,{headers:{Authorization:'Bearer '+token}});if(!r.ok)continue;url=URL.createObjectURL(await r.blob());covers.set(item.image,url);}catch{continue;}}
  const figure=document.createElement('figure'),image=document.createElement('img'),caption=document.createElement('figcaption'),artist=document.createElement('span');image.src=url;image.alt=item.title;image.loading='lazy';caption.textContent=item.title;artist.textContent=item.artist;caption.append(artist);const button=document.createElement('button');button.className='sleeve-button';button.type='button';button.setAttribute('aria-label','View sleeve: '+item.title);button.append(image);button.addEventListener('click',()=>{$('sleeve-image').src=url;$('sleeve-title').textContent=item.title;$('sleeve-artist').textContent=item.artist||'';$('sleeve-dialog').showModal();});figure.append(button,caption);figures.push(figure);
 }
 if($('recent').dataset.signature!==signature)return;
 $('recent').replaceChildren(...figures);$('recent-empty').hidden=figures.length>0;$('collection-count').textContent=figures.length?String(figures.length).padStart(2,'0')+' / RECENT COVERS':'YOUR COLLECTION STARTS HERE';
 for(const [name,url] of covers)if(!items.some(i=>i.image===name)){URL.revokeObjectURL(url);covers.delete(name);}
}
function presentation(state){
 const p=state.presentation||{},play=state.playback||{},configured=!!state.config?.source;
 $('room-name').textContent=state.config?.sonos_room_name||(state.config?.source==='roon'?'Your Roon output':configured?'Your listening room':'Your room');
 $('source-label').textContent=configured?({sonos:'Sonos',roon:'Roon',upnp:'UPnP renderer'}[state.config.source]):'Choose a music source to begin';
 const fresh=play.updated_at&&Date.now()/1000-play.updated_at<20;
 $('transport').textContent=state.running?(fresh?(play.transport==='PLAYING'?'ON THE RECORD':play.transport==='UNAVAILABLE'?'CONNECTION NEEDS ATTENTION':'WAITING FOR MUSIC'):'WAITING FOR AN UPDATE'):state.busy?'PREPARING YOUR ROOM':p.revision?'LAST PREPARED TRACK':'READY WHEN YOU ARE';
 if(play.title){$('track').textContent=play.title;$('artist').textContent=play.artist||'';$('album').textContent=play.album||'';}
 else if(configured){$('track').textContent='Put something on.';$('artist').textContent='Play a track in your chosen room, then preview its artwork here.';$('album').textContent='';}
 $('prepared-dot').classList.toggle('active',!!p.revision);$('sent-dot').classList.toggle('active',p.status==='confirmed');
 $('prepared-label').textContent=p.prepared_at?'Prepared at '+timeLabel(p.prepared_at):'No image prepared yet';
 $('sent-label').textContent=p.status==='confirmed'?'TV selection confirmed at '+timeLabel(p.confirmed_at):p.status==='released'?'App control released; previous preview retained':state.running?'Waiting for TV selection confirmation':'Not sent to the Frame';
 $('image-caption').textContent=p.revision?(p.kind==='collage'?'Recent-listening collage':'Actual generated artwork'):'Your next listen belongs here.';
 $('evidence-note').textContent='This is the app’s generated image, not a camera view. A confirmation records the TV’s response at that time.';
 $('running-note').textContent=state.running?'Following music. Stop returns control to your original artwork.':state.busy?'Working on '+state.operation+'…':'Previewing leaves the TV untouched.';
 $('music-led').classList.toggle('active',!!(state.running&&fresh&&play.transport==='PLAYING'));
 $('art-led').classList.toggle('active',!!p.revision);
 $('tv-led').classList.toggle('active',p.status==='confirmed');
 for(const id of ['music-led','art-led','tv-led']){$(id).setAttribute('role','img');$(id).setAttribute('aria-label',$(id).classList.contains('active')?'On':'Off');}
 previewImage(p).catch(()=>{});recent(state.recent||[]).catch(()=>{});
}
async function refresh(){
 try{const state=await api('status');latest=state;if(!loaded){hydrate(state.config);loaded=true;if(!state.busy)lastResult=String(state.operation_id);if(state.config?.source)showNow();else showSetup();}controls(state);presentation(state);$('connection').textContent='LOCAL CONNECTION';
 if(state.failure)message(state.failure);
 const key=String(state.operation_id);
 if(!state.busy&&state.result&&Object.keys(state.result).length&&lastResult!==key){lastResult=key;if(!state.result.ok)message(state.result.message);else{const data=state.result.data;
 if(state.operation==='discover'){const selected=$('rooms').value;$('rooms').replaceChildren();option($('rooms'),'','Choose a room');for(const room of data.rooms)option($('rooms'),room.uid,room.name);if(selected&&!data.rooms.some(r=>r.uid===selected))option($('rooms'),selected,'Saved room · not found');$('rooms').value=selected;$('tvs').replaceChildren();option($('tvs'),'','Choose a discovered TV');for(const tv of data.tvs)option($('tvs'),JSON.stringify(tv),tv.name+' · '+tv.host);$('discovery').textContent=`Found ${data.tvs.length} TV(s) and ${data.rooms.length} room(s). ${data.warning||''}`;if(!data.tvs.length)$('manual').open=true;message(data.tvs.length?'Devices found. Choose your TV, then your room.':'Your TV wasn’t discovered. Enter its address below; your rooms are available in step 2.');}
 else if(state.operation==='probe'){$('tv').value=data.host;$('tv_model').value=data.model;dirty=true;message('Found '+data.name+'. Next, choose the room you want to follow.');}
 else if(state.operation==='check')message(data.message+'\nMusic: '+data.playback+(data.title?' · '+data.title:''));
 else if(state.operation==='preview'){message(data.message);$('preview-result').textContent=data.message;if(data.prepared)showNow();}
 else message(data.message||'Step complete.');controls(state);}}
 }catch(error){message('We lost the local setup connection. Check that the setup process is running.');$('connection').textContent='DISCONNECTED';}
}
async function action(path,data={}){try{const result=await api(path,data);if(path==='config')dirty=false;message(result.message);await refresh();}catch(error){message(error.message);}}
$('close-sleeve').addEventListener('click',()=>$('sleeve-dialog').close());
$('home').addEventListener('click',event=>{event.preventDefault();showNow();});$('settings').addEventListener('click',showSetup);$('begin').addEventListener('click',showSetup);
for(const el of document.querySelectorAll('#setup input,#setup select')){el.addEventListener('input',changed);el.addEventListener('change',changed);}
$('source').addEventListener('change',sourceFields);$('tvs').addEventListener('change',()=>{if($('tvs').value){const tv=JSON.parse($('tvs').value);$('tv').value=tv.host;$('tv_model').value=tv.model;changed();}});
for(const el of document.querySelectorAll('[data-step]'))el.addEventListener('click',()=>step(Number(el.dataset.step)));
$('back').addEventListener('click',()=>step(currentStep-1));$('next').addEventListener('click',()=>{if(currentStep===0&&(!$('tv').value.trim()||!$('tv_model').value.trim()))return message('Choose a discovered TV or check its address before continuing.');if(currentStep===1&&$('source').value==='sonos'&&!$('rooms').value)return message('Choose a Sonos room before continuing.');if(currentStep>=2&&dirty)return message('Save your choices before continuing.');message('');step(currentStep+1);});
$('save').addEventListener('click',()=>action('config',choices()));for(const id of ['discover','check','test','start','stop','restore','startup','preview'])$(id).addEventListener('click',()=>action(id));$('setup-preview').addEventListener('click',()=>action('preview'));$('probe').addEventListener('click',()=>action('probe',{host:$('tv').value.trim()}));
$('saw-test').addEventListener('click',()=>{$('test-answer').textContent='Thanks—that is your visual confirmation. Next we’ll check the music.';});$('test-help').addEventListener('click',()=>{$('test-answer').textContent='Check Art Mode and the TV’s Allow prompt. Retry Check connection. If another artwork service is running, stop that service before retrying the test.';});
$('finish').addEventListener('click',()=>{if(dirty||!latest.config?.source){step(2);message('Save your choices before opening your room.');}else{message('');showNow();}});
(async()=>{try{const session=await fetch('/api/session').then(r=>r.json());token=session.token;await refresh();setInterval(refresh,2500);}catch{message('Could not connect to the local setup process.');}})();
