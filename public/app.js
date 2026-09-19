/* PROXIMA // 1024 experience controller.
 * Owns onboarding, actual job progress, scene transitions and data rendering.
 * The server alone owns simulation state; browser animation never advances it. */
import {VoyageScene} from '/scene.js';

// === State and DOM boundaries ===
const $=id=>document.getElementById(id), esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=v=>Number(v).toLocaleString('en-US'), sleep=ms=>new Promise(r=>setTimeout(r,ms));
const COLORS={support:'#39c6cb',question:'#eab44c',oppose:'#df5860',unpolled:'#567883'}, GEN=['#39c6cb','#eab44c','#f2e9cf','#df5860','#90aee5'];
const HEALTH={susceptible:'#567883',healthy:'#567883',infected:'#eab44c',critical:'#df5860',recovered:'#39c6cb',dead:'#f2e9cf'};
const state={token:null,catalog:null,step:0,view:'wizard',campaign:null,busy:false,job:null,live:null,responses:{},flashes:{},dotHits:[],frameYears:new Set(),frameChain:Promise.resolve(),shown:null,fieldMode:'response',healthFrame:null,healthRoster:[],outbreakFrames:[],outbreakDays:new Set(),outcomePlaying:false,options:{destination:'proxima_b',drive:'prop_fusion_pulse_daedalus',ship:'ship_aurora_ark',crew:1024,reserve_days:240,medicine_years:8,speed:.05,launch_year:2200,ethos:'mission_scientists',scenario:'normal'}};
Object.assign(state,{deliberationTab:'astra',councilGroup:'medical',council:null,selectedMember:null,memberLimit:60,councilComposerOpen:true,lastPresentedCouncil:null});
let scene;
try {scene=new VoyageScene($('space'));scene.ready.catch(()=>{$('scene-fallback').hidden=false;});scene.onLabels=labels=>{if(state.view!=='voyage')return;$('star-labels').replaceChildren(...labels.map(x=>{const el=document.createElement('span');el.textContent=x.label;el.style.left=x.x+'%';el.style.top=x.y+'%';return el;}));};} catch {$('scene-fallback').hidden=false;}
function show(id){state.view=id;document.querySelectorAll('.view').forEach(v=>v.hidden=v.id!==id);$('resume-row').hidden=id!=='wizard'||!$('resume-row').children.length;if(id==='wizard'){$('wizard-visual').prepend($('scene'));$('star-labels').replaceChildren();}if(id==='voyage'){$('route-visual').prepend($('scene'));scene?.set('route',{destination:state.campaign.mission.destination,ship:state.campaign.ship.class,progress:traveled()/state.campaign.mission.distance_ly});}requestAnimationFrame(()=>{scene?.resize();drawAll();});}
function traveled(frame){return frame?.distance_traveled_ly??state.campaign.play.distance_traveled_ly??Math.min(state.campaign.mission.distance_ly,state.campaign.stats.year*state.campaign.mission.velocity_c);}
function notice(message){$('notice-text').textContent=message;$('notice').hidden=false;$('notice-restore')?.remove();}
// Issue #1 recovery affordance: when polling gives up, offer an explicit restore
// of the server's last saved state instead of an eternal spinner.
function noticeRestore(message){notice(message);$('notice-text').insertAdjacentHTML('afterend','<button id="notice-restore" class="quiet">Restore last saved state</button>');const b=$('notice-restore');b.onclick=async()=>{b.disabled=true;try{const cid=state.campaign?.id||localStorage.getItem('proxima-v2-campaign');if(cid)applyCampaign(await api('/api/campaign/'+cid));$('notice').hidden=true;b.remove();renderCurrent();}catch(e){b.disabled=false;notice(e.message);}};}
// frameChain hardening (issue #1 root cause): an exception inside any queued
// playback step used to leave the chain permanently REJECTED, so every later
// direct await of state.frameChain threw — and on the boot()-reconnect/replay paths
// nothing reset the busy flag, stranding the spinner while the server sat idle.
// chainFrames makes every appended step non-rejecting; awaitFrames adds a hard
// time cap so playback can delay completion but never block it.
function chainFrames(fn){state.frameChain=state.frameChain.then(fn).catch(e=>console.error('Frame playback error (state intact):',e));}
async function awaitFrames(cap=30000){await Promise.race([state.frameChain.then(()=>{},()=>{}),sleep(cap)]);}
function setBusy(value,phase=''){state.busy=value;document.body.classList.toggle('working',value);$('operation').hidden=!value;$('operation-text').textContent=phase;for(const id of ['next','back','home','cryo','wake','new-voyage','return-cryo','board','chat-send','consult-send','suggest'])$(id).disabled=value;$('decide').disabled=value||state.campaign?.play.mode!=='awake';$('decision').readOnly=value||state.campaign?.play.mode!=='awake';$('council-question').readOnly=value;$('replay-outcome').disabled=value;$('consult-send').disabled=value||!state.campaign?.outbreak?.active;$('council-groups').querySelectorAll('button').forEach(b=>b.disabled=value);}
async function api(path,data,retry=true){let response;try{response=await fetch(path,{method:data?'POST':'GET',headers:data?{'Content-Type':'application/json','X-Proxima-Token':state.token}:{},body:data?JSON.stringify(data):undefined});}catch{throw new Error('The local ship server is not reachable. Your last saved year is safe. Reopen this page when the server is running.');}if(response.status===403&&data&&retry){const b=await api('/api/bootstrap');state.token=b.token;return api(path,data,false);}const result=await response.json();if(!response.ok){const e=new Error(result.error||'The ship operation could not be completed.');e.status=response.status;throw e;}return result;}
function selected(group,key){return state.catalog[group].find(x=>x.id===state.options[key]);}
function years(){return Math.ceil(selected('destinations','destination').distance_ly/state.options.speed);}

// === Six visual steps; every slider is wired to an engine input ===
const STEPS=[['Destination','You are the admiral.','You sleep in cryogenesis. Generations live aboard. When the ship cannot choose, it wakes you.'],['Method','How will you cross?','Choose a speculative drive and when its infrastructure might be ready.'],['Architecture','A ship. And a society.','These people need a world to live in, not just a vehicle.'],['Manifest','What comes with you?','People, food and medicine. Your margins become their future.'],['Speed','How much time will pass?','You may wake to descendants of the people you left behind.'],['Community','Who takes the leap?','A founding culture—not a predetermined ending.']];
const DRIVE={prop_fusion_pulse_daedalus:['Fusion pulse','Discrete fusion pulses · Daedalus lineage','PULSE'],prop_fusion_continuous:['Continuous fusion','A sustained magnetic-confinement drive','FUSION'],prop_antimatter_catalyzed:['Antimatter-catalyzed','A small catalyst. Enormous infrastructure.','CATALYST']};
const SHIP={ship_modular_cluster:['Modular cluster','Compact, connected pressure habitats','MIN 200'],ship_stanford_torus:['Stanford torus','One rotating world around a central spine','MIN 500'],ship_aurora_ark:['Generation ark','Two habitat rings. A world built to endure.','MIN 1,000']};
const ETHOS={mission_scientists:['Mission scientists','A shared purpose: discovery and continuity.','✳'],mixed_voluntary:['Volunteer mosaic','Different reasons to leave. A future to negotiate.','⌘'],religious_refugees:['Faith & refuge','A shared tradition and a place to begin again.','◇']};
function choice(key,id,title,sub,tag='',mark='◌'){return `<button class="choice ${state.options[key]===id?'selected':''}" data-option="${key}" data-value="${id}" aria-pressed="${state.options[key]===id}"><span class="choice-mark">${state.options[key]===id?'●':mark}</span><span><strong>${esc(title)}</strong><small>${esc(sub)}</small></span><em>${esc(tag)}</em></button>`;}
function range(key,label,min,max,step,unit=''){return `<div class="range-field"><label for="option-${key}">${label}<output id="value-${key}">${formatRange(key)}${unit}</output></label><input id="option-${key}" data-range="${key}" type="range" min="${min}" max="${max}" step="${step}" value="${state.options[key]}"><div class="range-ends"><span>${min}${unit}</span><span>${max}${unit}</span></div></div>`;}
function formatRange(key){return key==='speed'?Math.round(state.options[key]*100)+'% of light':num(state.options[key]);}
function crewControl(ship){const min=Math.max(200,ship.min_crew),presets=[1024,5000,10000,50000].filter(n=>n>=min);return `<div class="range-field crew-control"><label for="option-crew">Founding crew <output id="value-crew">${num(state.options.crew)}</output></label><input id="option-crew" data-crew-input type="number" inputmode="numeric" min="${min}" max="50000" step="1" value="${state.options.crew}"><div class="range-ends"><span>${num(min)} minimum for this architecture</span><span>50,000 maximum</span></div><div class="crew-presets">${presets.map(n=>`<button type="button" data-crew-preset="${n}" aria-pressed="${state.options.crew===n}">${num(n)}</button>`).join('')}</div><small class="range-note">Jev evaluates each crew member. Large manifests take longer and cost more per order.</small></div>`;}
function renderStep(){if(!state.catalog)return;const i=state.step,o=state.options,d=selected('destinations','destination'),ship=selected('ships','ship'),drive=selected('drives','drive');
  $('steps').innerHTML=STEPS.map((s,j)=>`<button class="step-tab ${i===j?'active':''}" data-step="${j}" ${i===j?'aria-current="step"':''}><span>0${j+1}</span>${s[0].toUpperCase()}</button>`).join('');
  $('step-count').textContent=`0${i+1} / 06`;$('step-title').textContent=STEPS[i][1];$('step-deck').textContent=STEPS[i][2];$('back').disabled=i===0;$('next').textContent=i===5?'Generate my voyage →':`Choose ${['a drive','a drive','the architecture','the manifest','the speed','the community'][i+1]||'a drive'} →`;
  let controls='';
  if(i===0)controls=`<button id="long-watch" class="long-watch ${o.scenario==='outbreak'?'selected':''}"><span><strong>The long watch</strong><small>TRAPPIST-1e · generation ark · a seeded outbreak</small></span><em>${num(Math.ceil(state.catalog.destinations.find(d=>d.id==='trappist_1_e').distance_ly/.03))}<small>year passage</small></em></button>`+state.catalog.destinations.filter(x=>x.id!=='tau_ceti_e').map(x=>choice('destination',x.id,x.name,x.id==='proxima_b'?'Our nearest planetary neighbor':x.id==='ross_128_b'?'A quieter red-dwarf system':'One world in a compact family of seven',x.distance_ly+' LY')).join('');
  if(i===1)controls=state.catalog.drives.map(x=>choice('drive',x.id,...DRIVE[x.id].slice(0,2),Math.round(x.max_velocity_c*100)+'% c')).join('')+range('launch_year','Departure year',drive.earliest_year,2500,10);
  if(i===2)controls=state.catalog.ships.map(x=>choice('ship',x.id,...SHIP[x.id])).join('');
  if(i===3)controls=crewControl(ship)+range('reserve_days','Food reserve',120,360,10,' days')+range('medicine_years','Medical reserve',4,12,1,' years');
  if(i===4)controls=`<div class="speed-number" id="speed-years">${years()}<small>CRUISE YEARS</small></div>`+range('speed','Cruise velocity',.02,Math.min(.15,drive.max_velocity_c),.01)+`<p class="range-note">Cruise-only estimate. Acceleration and braking are not modeled.</p>`;
  if(i===5)controls=Object.entries(ETHOS).map(([id,[name,sub,icon]])=>choice('ethos',id,name,sub,'',icon)).join('')+`<div class="scenario-choice"><span>Opening scenario</span><div><button data-scenario="normal" aria-pressed="${o.scenario!=='outbreak'}">Open voyage</button><button data-scenario="outbreak" aria-pressed="${o.scenario==='outbreak'}">Seeded outbreak</button></div><small>${o.scenario==='outbreak'?'A plague will break out. Its victims and outcome are not predetermined.':'The ship’s actual conditions determine the first crisis.'}</small></div>`;
  $('step-controls').innerHTML=controls;updatePreview(true);
  $('step-controls').querySelectorAll('[data-option]').forEach(b=>b.onclick=()=>{o[b.dataset.option]=b.dataset.value;const sh=selected('ships','ship'),dr=selected('drives','drive');o.crew=Math.max(Math.max(200,sh.min_crew),Math.min(o.crew,50000));o.speed=Math.min(o.speed,dr.max_velocity_c);o.launch_year=Math.max(o.launch_year,dr.earliest_year);renderStep();});
  const crewInput=$('option-crew');if(crewInput){const min=Math.max(200,ship.min_crew);crewInput.oninput=()=>{const n=Number(crewInput.value);if(Number.isInteger(n)&&n>=min&&n<=50000){o.crew=n;$('value-crew').textContent=num(n);updatePreview(false);}};crewInput.onchange=()=>{const n=Number(crewInput.value);o.crew=Number.isFinite(n)?Math.min(50000,Math.max(min,Math.round(n))):min;renderStep();};$('step-controls').querySelectorAll('[data-crew-preset]').forEach(b=>b.onclick=()=>{o.crew=Number(b.dataset.crewPreset);renderStep();});}
  $('step-controls').querySelectorAll('[data-range]').forEach(input=>input.oninput=()=>{const k=input.dataset.range;o[k]=Number(input.value);$('value-'+k).textContent=formatRange(k)+(k==='reserve_days'?' days':k==='medicine_years'?' years':'');if($('speed-years'))$('speed-years').innerHTML=years()+'<small>CRUISE YEARS</small>';updatePreview(false);});
  $('steps').querySelectorAll('button').forEach(b=>b.onclick=()=>{if(!state.busy){state.step=Number(b.dataset.step);renderStep();}});
  if($('long-watch'))$('long-watch').onclick=()=>{Object.assign(o,{destination:'trappist_1_e',drive:'prop_fusion_continuous',ship:'ship_aurora_ark',crew:1024,reserve_days:300,medicine_years:8,speed:.03,ethos:'mixed_voluntary',scenario:'outbreak'});o.launch_year=Math.max(o.launch_year,selected('drives','drive').earliest_year);renderStep();};
  $('step-controls').querySelectorAll('[data-scenario]').forEach(b=>b.onclick=()=>{o.scenario=b.dataset.scenario;renderStep();});
}
function updatePreview(reset){const o=state.options,i=state.step,d=selected('destinations','destination');$('mission-estimate').innerHTML=`<strong>${esc(d.name)}</strong> · ${num(o.crew)} founders · <strong class="journey-years">${num(years())} years</strong>${o.scenario==='outbreak'?'<span class="scenario-tag">SEEDED OUTBREAK · UNSCRIPTED OUTCOME</span>':''}`;
  const captions=[[d.host_star,d.name,`${d.distance_ly} light-years from everything you know.`],['PROPULSION',DRIVE[o.drive][0],`Earliest modeled departure ${selected('drives','drive').earliest_year}. Not demonstrated technology.`],['ROTATING HABITAT',SHIP[o.ship][0],'A conceptual layout of the world aboard.'],['LIFE, PACKED FOR DEPARTURE',num(o.crew)+' founders','Finite stores. Real choices within the simulation.'],['TIME IS THE DISTANCE',years()+' years',`At ${Math.round(o.speed*100)}% of the speed of light.`],['THE FIRST CHAPTER',ETHOS[o.ethos][0],'Their children will inherit what you decide.']];
  const cap=captions[i];$('visual-kicker').textContent=cap[0];$('visual-title').textContent=cap[1];$('visual-detail').textContent=cap[2];$('visual-credit').textContent=i===0?'FICTIONAL SURFACE & ATMOSPHERE · CONCEPT ART':'LOCAL HUNYUAN HULL · CONCEPT ASSEMBLY · DRAG TO INSPECT';
  $('visual-overlay').replaceChildren();$('scene').style.opacity=(i===3||i===5)?'.15':'1';
  if(reset)scene?.set(i===0?'planet':'architecture',{ship:o.ship,destination:o.destination});
  if(i===3){$('visual-overlay').innerHTML='<div class="manifest-visual">'+[[Math.log10(o.crew)/Math.log10(50000),num(o.crew),'PEOPLE'],[o.reserve_days/360,o.reserve_days,'FOOD DAYS'],[o.medicine_years/12,o.medicine_years,'MEDICAL YEARS']].map(x=>`<div class="manifest-column"><strong>${x[1]}</strong><i data-height="${x[0]*100}"></i><span>${x[2]}</span></div>`).join('')+'</div>';$('visual-overlay').querySelectorAll('i').forEach(x=>x.style.height=x.dataset.height+'%');}
  if(i===5){$('visual-overlay').innerHTML='<div class="community-visual">'+Array.from({length:96},()=>'<span class="community-person"></span>').join('')+'</div>';$('visual-overlay').querySelectorAll('.community-person').forEach((p,j)=>p.style.animationDelay=(j*.007)+'s');}
}

// === Job transport: resumable polling, never reissue an uncertain mutation ===
async function startJob(kind,extra={}){if(state.busy)return;setBusy(true,kind==='cryo'?'Entering cryo':kind==='decision'?'Jev is reading your decision':kind==='consult'?'Jev is weighing the council’s options':kind==='talk'?'Astra is considering your question':'Preparing the ship');$('notice').hidden=true;if(kind==='talk')state.pendingChat=extra.message;try{const payload=kind==='generate'?{options:state.options}:{campaign_id:state.campaign.id,revision:state.campaign.play.revision,...extra};const data=await api('/api/'+kind,payload);state.job={id:data.job_id,kind,campaign_id:state.campaign?.id,message:extra.message};localStorage.setItem('proxima-v2-job',JSON.stringify(state.job));await pollJob();}catch(e){setBusy(false);failedUI(kind,e.message);}}
function failedUI(kind,error){if(kind==='generate'){state.step=5;show('wizard');renderStep();}else if(kind==='talk'){renderChat();$('chat-input').value=state.pendingChat||'';$('chat-log').insertAdjacentHTML('beforeend',`<div class="chat-message"><small>CONNECTION INTERRUPTED</small>${esc(error)}</div>`);$('chat-log').scrollTop=$('chat-log').scrollHeight;}else if(kind==='consult'){$('council-progress').textContent=error+' No order was issued.';}else renderCurrent();notice(error);}
async function pollJob(){let errors=0,lastSig=null,lastChange=Date.now();while(state.job){let j;try{j=await api('/api/jobs/'+state.job.id);errors=0;}catch(e){if(e.status===404){const cid=state.job.campaign_id;state.job=null;localStorage.removeItem('proxima-v2-job');setBusy(false);try{if(cid)applyCampaign(await api('/api/campaign/'+cid));}catch(err){notice(err.message);return;}notice('The server restarted. The last saved state is restored; no order was automatically repeated.');renderCurrent();return;}if(++errors>12){notice(e.message+' Reload to reconnect to the pending operation.');setBusy(false);return;}$('operation-text').textContent='Reconnecting to the same operation…';await sleep(1000);continue;}
    // Give up after 6 minutes without any observable progress (issue #1c). The
    // server keeps only saved state; nothing is silently reissued.
    const sig=JSON.stringify([j.status,j.phase,j.jev?.received,j.council?.received,j.frames?.length,j.outbreak_frames?.length]);if(sig!==lastSig){lastSig=sig;lastChange=Date.now();}
    if(j.status==='running'&&Date.now()-lastChange>360000){state.job=null;localStorage.removeItem('proxima-v2-job');setBusy(false);noticeRestore('This operation made no progress for six minutes and was released on this side.');return;}
    try{$('operation-text').textContent=state.outcomePlaying?'Following the consequences, day by day':j.phase||'Working';if(j.jev)applyJev(j.jev);if(j.council)renderCouncil(j.council);
    // Hold the genuine pre-order medical state until the first saved day plays;
    // the server can finish all days before the browser has displayed any of them.
    if((j.outbreak_frames?.length||j.result?.outbreak_frames?.length)&&!state.healthFrame&&state.campaign?.outbreak)state.healthFrame={day:state.campaign.outbreak.day,summary:state.campaign.outbreak,health:Object.fromEntries(state.healthRoster.map(p=>[p.id,{status:p.health||'susceptible'}]))};
    if(j.campaign){applyCampaign(j.campaign);if(state.job.kind!=='cryo'&&state.view==='bridge')renderBridge();}if(j.frames)enqueueFrames(j.frames);if(j.outbreak_frames||j.result?.outbreak_frames)enqueueOutbreakFrames(j.outbreak_frames||j.result.outbreak_frames);
    }catch(err){console.error('Job render error (polling continues, state intact):',err);}
    if(j.status==='done'||j.status==='failed'){const kind=state.job.kind;await awaitFrames();state.job=null;localStorage.removeItem('proxima-v2-job');const result=j.result||{};try{if(result.campaign||j.campaign)applyCampaign(result.campaign||j.campaign);}finally{setBusy(false);}
      if(j.status==='failed'){failedUI(kind,j.error);return;}
      if(result.narration_error)notice(result.narration_error);
      if(kind==='generate')renderDraw();else if(kind==='cryo'){renderVoyage();}else if(kind==='decision'){renderBridge();if(result.blocked){$('field-title').textContent='PREVIEW ONLY · ORDER BLOCKED';$('astra-voice').textContent=result.reason;notice(result.reason);}else if(state.fieldMode!=='medical')$('field-title').textContent=state.campaign.play.incident.accepted_risk?'RISK ACCEPTED · NOT REPAIRED':state.campaign.play.mode==='ready'?'INCIDENT RESOLVED · CONSEQUENCES SAVED':'CONSEQUENCES SAVED · '+(state.campaign.outbreak?.active?num(state.campaign.outbreak.infected)+' STILL INFECTED · ':'')+'DECISION STILL NEEDED';}else if(kind==='consult'){renderCouncil(result.consultation);renderDeliberationBrief();}else if(kind==='talk'){renderChat();if(state.view==='bridge')renderBridge();}return;
    }await sleep(180);
  }}
function healthRoster(c){return [...c.crew,...(c.casualties||[])].sort((a,b)=>String(a.id).localeCompare(String(b.id),'en',{numeric:true}));}
function applyCampaign(c){if(state.campaign?.id!==c.id){state.healthFrame=null;state.healthRoster=[];state.outbreakFrames=[];state.outbreakDays=new Set();state.fieldMode='response';state.council=null;state.selectedMember=null;}c.responses=c.play.responses||{};state.campaign=c;state.healthRoster=healthRoster(c);localStorage.setItem('proxima-v2-campaign',c.id);if(!state.live)state.responses=c.responses;}
function applyJev(live){for(const [id,r] of Object.entries(live.responses)){if(!state.live?.responses[id]||state.live.responses[id].choice!==r.choice)state.flashes[id]=performance.now();}state.live=live;state.responses=live.responses;renderDistribution(live.mean_distribution);$('evaluated').textContent=num(live.received);$('evaluated-total').textContent='/ '+num(live.total);$('jev-timing').textContent=live.seconds.toFixed(2)+' SEC · '+(live.received===live.total?'COMPLETE':'LIVE BATCHES');if(state.fieldMode!=='medical')$('field-title').textContent=live.received===live.total?'JEV RESPONSE COMPLETE':'JEV IS READING THE SHIP';drawCrew();}
function enqueueFrames(frames){for(const frame of frames){if(state.frameYears.has(frame.stats.year)||frame.stats.year<(state.minimumFrameYear||0))continue;state.frameYears.add(frame.stats.year);chainFrames(async()=>{state.shown=frame;renderVoyageFrame(frame);await sleep(matchMedia('(prefers-reduced-motion: reduce)').matches?80:850);});}}

// === Medical outcome playback: real daily records, never a client-side forecast ===
function healthStatus(person){const h=state.healthFrame?.health?.[person.id];return typeof h==='string'?h:h?.status||person.health||'susceptible';}
function currentOutbreak(){return state.healthFrame?.summary||state.campaign?.outbreak;}
function outcomeRecord(){return state.campaign?.play.last_decision?.outbreak_result;}
function savedOutbreakFrames(){return state.outbreakFrames.length?state.outbreakFrames:outcomeRecord()?.frames||[];}
function setFieldMode(mode){state.fieldMode=mode;$('view-response').setAttribute('aria-pressed',String(mode==='response'));$('view-medical').setAttribute('aria-pressed',String(mode==='medical'));$('jev-hud').hidden=mode==='medical';$('medical-hud').hidden=mode!=='medical';$('field-legend').textContent=mode==='medical'?'AMBER INFECTED · RED CRITICAL · CYAN RECOVERED · × LOST':'COLOR = TOP RESPONSE · BARS = FULL PROBABILITIES';$('crew-canvas').setAttribute('aria-label',mode==='medical'?'Actual outbreak health by ship compartment. Crosses mark people who died.':'Individual crew response distributions by ship compartment.');$('person-inspector').hidden=true;if(mode==='medical')renderMedical();else $('field-title').textContent=state.live?.received===state.live?.total&&state.live?'JEV RESPONSE COMPLETE':'THE SHIP IS LISTENING';drawCrew();}
function enqueueOutbreakFrames(frames){const fresh=frames.filter(f=>!state.outbreakDays.has(f.day));if(!fresh.length)return;for(const frame of fresh){state.outbreakDays.add(frame.day);state.outbreakFrames.push(frame);}state.outcomePlaying=true;const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
  chainFrames(async()=>{try{await sleep(reduced?40:1050);setFieldMode('medical');for(const frame of fresh){state.healthFrame=frame;renderMedical();drawCrew();await sleep(reduced?45:180);}}finally{state.outcomePlaying=false;}renderMedical();});
}
function renderMedical(){const s=currentOutbreak(),visible=!!s&&s.status!=='armed'&&state.campaign.play.incident?.kind==='outbreak';$('medical-strip').hidden=!visible;$('field-modes').hidden=!visible;if(!visible)return;const deaths=s.deaths??s.dead??0;$('medical-day').textContent='Day '+num(s.day||0);$('medical-phase').textContent=state.outcomePlaying?'YOUR ORDER IN MOTION':s.resolved?'OUTBREAK CONTAINED':'ADMIRAL REQUIRED';$('medical-infected').textContent=num(s.infected||0);$('medical-critical').textContent=num(s.critical||0);$('medical-recovered').textContent=num(s.recovered||0);$('medical-deaths').textContent=num(deaths);$('medical-summary').textContent=state.outcomePlaying?'Actual simulated days. Every loss persists.':s.resolved?'Transmission contained. The people lost do not come back.':'Lives are at risk. Your choice changes the course.';
  const cap=s.care_capacity??s.capacity??0;$('care-capacity').textContent=num(typeof cap==='number'?cap:cap.total||0);$('care-capacity').nextElementSibling.textContent='emergency care places · '+num(s.care_delivered||0)+' treated today';const record=outcomeRecord();$('medical-hud').querySelector('.medical-heading').innerHTML=record?'Your order.<br>Its consequences.':'Lives at risk.<br>Your decision.';const med=state.healthFrame?Math.max(0,(s.medicine_spent_kg||0)-(record?.before?.medicine_spent_kg||0)):record?.medicine_spent_kg??s.medicine_spent_kg??0,food=state.healthFrame?Math.max(0,(s.food_spent_kg||0)-(record?.before?.food_spent_kg||0)):record?.food_spent_kg??s.food_spent_kg??0;$('medical-cost').innerHTML=`<strong>${num(Math.round(med))} kg medicine · ${num(Math.round(food))} kg food</strong><br>${record?'Consumed by this response.':'No response authorized yet.'}${s.cooperation!=null?` ${Math.round(s.cooperation*100)}% modeled cooperation, using Jev’s full distributions.`:''}`;$('replay-outcome').hidden=!savedOutbreakFrames().length;$('replay-outcome').disabled=state.busy||state.outcomePlaying;
  $('outbreak-chart').hidden=savedOutbreakFrames().length<2;if(state.fieldMode==='medical'){$('field-title').textContent=(state.outcomePlaying?'CONSEQUENCES UNFOLDING':s.resolved?'CONTAINED · LOSSES ARE PERMANENT':'OUTBREAK · '+num(s.infected||0)+' STILL INFECTED')+' · DAY '+(s.day||0);$('field-count').textContent=num(s.population??state.campaign.stats.crew)+' LIVING';}drawOutbreakChart();
}
function drawOutbreakChart(){if(state.view!=='bridge'||state.fieldMode!=='medical')return;const a=canvasContext('outbreak-chart');if(!a)return;const{ctx,w,h}=a,frames=savedOutbreakFrames().filter(f=>f.day<=(currentOutbreak()?.day||0));if(frames.length<2)return;const max=Math.max(1,...frames.map(f=>(f.summary?.infected||0)+(f.summary?.critical||0))),first=frames[0].day,last=frames.at(-1).day;ctx.strokeStyle='#3f5c67';ctx.beginPath();ctx.moveTo(0,h-15);ctx.lineTo(w,h-15);ctx.stroke();for(const [key,color] of [['infected',HEALTH.infected],['critical',HEALTH.critical],['deaths',HEALTH.dead]]){ctx.strokeStyle=color;ctx.lineWidth=key==='deaths'?2.5:1.5;ctx.beginPath();frames.forEach((f,i)=>{const x=(f.day-first)/(last-first||1)*(w-4)+2,y=h-16-(f.summary?.[key]||0)/max*(h-20);i?ctx.lineTo(x,y):ctx.moveTo(x,y);});ctx.stroke();}ctx.font='9px Menlo,monospace';ctx.fillStyle='#a8b5aa';ctx.fillText('DAY '+first,0,h-2);ctx.textAlign='right';ctx.fillText('DAY '+last,w,h-2);ctx.textAlign='left';}
function renderDraw(){show('commission');const c=state.campaign;$('trial-grid').innerHTML=c.candidates.map((t,i)=>`<div class="trial ${i===c.selected?'selected':''}"><span class="eyebrow">0${i+1}${i===c.selected?' · DRAWN':''}</span><h3>${esc(t.name)}</h3><strong>${num(t.crew)}</strong><small>PEOPLE AT YEAR 8</small><p>${t.integrity}% INTEGRITY<br>${t.morale} MORALE</p></div>`).join('');$('draw-result').textContent=`You will command ${c.name}. The ending is unwritten.`;$('board').textContent=`Board ${c.name} →`;$('board').hidden=false;}

// === Cryo: every rendered frame is a saved annual simulation result ===
function renderVoyage(){show('voyage');const c=state.campaign,p=c.play,s=c.stats;$('scene').style.opacity='1';const ended=c.status!=='in_flight';$('voyage-kicker').textContent=ended?'VOYAGE COMPLETE':p.mode==='awake'?'ADMIRAL · WAKE REQUIRED':s.year===0?'WELCOME ABOARD '+c.name.toUpperCase():'ADMIRAL’S CRYOGENESIS';$('voyage-title').textContent=ended?(c.status==='arrived'?'A new sky. An unwritten future.':'The ship has fallen silent.'):p.mode==='awake'?'The ship needs you.':'While you sleep, a world lives.';
  $('cryo').hidden=ended||p.mode==='awake';$('cryo').textContent=s.year===0?'Enter cryo →':'Continue in cryo →';$('wake').hidden=p.mode!=='awake';$('new-voyage').hidden=!ended;
  $('voyage-caption').textContent=ended?p.voice||'The expedition has ended.':p.mode==='awake'?p.voice||p.incident.title:s.year===0?'Only you enter cryogenesis. The crew keeps living. You wake when a decision matters.':'The watch is quiet. Continue when you’re ready.';
  $('captain-context').textContent=`${c.mission.destination_name} · ${num(Math.ceil(c.mission.distance_ly/c.mission.velocity_c))}-year passage · You are the admiral. The ship is their home.`;
  renderVoyageFrame({stats:s,crew:c.crew,distance_traveled_ly:traveled(),wake_probability:p.wake_checks.at(-1)?.wake_probability,wake:p.mode==='awake',births:null,deaths:null,mode:p.mode});
}
function renderVoyageFrame(frame){const s=frame.stats,c=state.campaign;state.shown=frame;$('voyage-year').textContent=s.year;$('population-count').textContent=num(s.crew);$('population-change').textContent=frame.births===null?'':`+${frame.births} BORN · −${frame.deaths} LOST`;$('route-distance').textContent=c.mission.distance_ly+' LIGHT-YEARS';$('route-caption').textContent=`${Math.max(0,c.mission.distance_ly-traveled(frame)).toFixed(2)} ly to ${c.mission.destination_name}`;scene?.setProgress(traveled(frame)/c.mission.distance_ly);
  const counts={};for(const p of frame.crew)counts[p.generation]=(counts[p.generation]||0)+1;$('generations').innerHTML=Object.entries(counts).map(([g,n])=>`<span><i data-gen="${g}"></i>${Number(g)===0?'FOUNDERS':'GEN '+g} ${num(n)}</span>`).join('');$('generations').querySelectorAll('i').forEach(x=>x.style.background=GEN[Number(x.dataset.gen)%GEN.length]);
  $('population-facts').innerHTML=[['BIRTHS',s.births],['DEATHS',s.deaths],['INTEGRITY',s.integrity+'%'],['YEARS LEFT',s.remaining]].map(([k,v])=>`<span><strong>${numOrText(v)}</strong>${k}</span>`).join('');
  const prob=frame.wake_probability;$('watch-value').textContent=prob==null?'—':Math.round(prob*100)+'%';$('watch-fill').style.width=(prob||0)*100+'%';$('watch-label').textContent=prob==null?'JEV WATCH STANDING BY':frame.wake?'JEV WATCH · ADMIRAL REQUESTED':'JEV WATCH · PROBABILITY OF NEEDING YOU';if(state.busy){$('voyage-kicker').textContent='ADMIRAL IN CRYOGENESIS · LIVE SHIP TIME';$('voyage-caption').textContent=frame.wake?'The watch has found a decision that needs you.':'The crew lives on. These are actual yearly simulation results.';}
  drawPopulation();drawTimeline();
}
function numOrText(v){return typeof v==='number'?num(v):esc(v);}

// === The decision instrument ===
function renderBridge(){const c=state.campaign,p=c.play,incident=p.incident;if(!incident){renderVoyage();return;}show('bridge');if(incident.kind!=='outbreak'&&state.fieldMode==='medical')setFieldMode('response');$('incident-title').textContent=incident.title;$('incident-year').textContent=' · YEAR '+incident.opened_year;$('incident-goal').textContent=incident.accepted_risk?'Risk accepted. The underlying condition remains.':incident.resolved?'Resolution reached. Your changes are saved.':incident.goal;$('field-count').textContent=num(state.live?.total??c.stats.crew)+' INDIVIDUALS';$('evaluated-total').textContent='/ '+num(state.live?.total??c.stats.crew);
  // Issue #2: a blocked return-to-cryo is shown disabled with the server's
  // explicit reason, instead of silently hidden.
  const cryoBlock=c.cryo_blocked_reason||null;$('return-cryo').hidden=p.mode!=='ready'&&!cryoBlock;$('return-cryo').disabled=state.busy||p.mode!=='ready';$('cryo-block-reason')?.remove();if(cryoBlock&&p.mode!=='ready'){$('return-cryo').insertAdjacentHTML('beforebegin','<span id="cryo-block-reason"></span>');const cb=$('cryo-block-reason');cb.textContent=cryoBlock.toUpperCase();cb.style.cssText='font:10px Menlo,monospace;letter-spacing:.08em;color:#eab44c;align-self:center;margin-right:14px;text-align:right;';}
  $('decide').disabled=state.busy||p.mode!=='awake';$('decision').readOnly=state.busy||p.mode!=='awake';$('suggest').disabled=state.busy||p.mode!=='awake';$('astra-voice').innerHTML=advisoryText(p.voice||'The incident stays open until its conditions change. What is your decision?');
  const stats=c.stats;$('ship-barriers').innerHTML=[['FOOD RESERVE',stats.food_days+' days'],['MEDICINE',stats.medicine_years+' years'],['INTEGRITY',stats.integrity+'%'],['MORALE',stats.morale],['MANDATE',stats.mandate+'%'],['WORK AVAILABLE',p.work+' / 100']].map(([k,v])=>`<div class="barrier-row"><span>${k}</span><b>${esc(v)}</b></div>`).join('');
  if(!state.live){const last=c.jev.last;renderDistribution(last?.mean_distribution);$('evaluated').textContent=last?num(last.people):'0';$('jev-timing').textContent=last?last.seconds.toFixed(2)+' SEC · LAST DECISION':'Awaiting your decision';}
  renderCrisisReading();renderMedical();drawCrew();
}
function renderDistribution(probs={}){$('distribution').innerHTML=['support','question','oppose'].map(k=>`<div class="dist-row"><div class="dist-label"><span>${k.toUpperCase()}</span><span>${probs?.[k]==null?'—':(probs[k]*100).toFixed(1)+'%'}</span></div><div class="dist-track"><i data-kind="${k}"></i></div></div>`).join('');$('distribution').querySelectorAll('i').forEach(el=>{el.style.width=((probs?.[el.dataset.kind]||0)*100)+'%';el.style.background=COLORS[el.dataset.kind];});}
function canvasContext(id){const canvas=$(id),r=canvas.getBoundingClientRect(),ratio=Math.min(devicePixelRatio,2);if(!r.width||!r.height)return null;if(canvas.width!==Math.round(r.width*ratio)||canvas.height!==Math.round(r.height*ratio)){canvas.width=Math.round(r.width*ratio);canvas.height=Math.round(r.height*ratio);}const ctx=canvas.getContext('2d');ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,r.width,r.height);return{ctx,w:r.width,h:r.height};}
function drawCrew(){if(state.view!=='bridge'||!state.campaign)return;const a=canvasContext('crew-canvas');if(!a)return;const {ctx,w,h}=a,small=w<400,pad=small?9:35,gap=small?9:20,roomW=(w-pad*2-gap*2)/3,roomH=(h-45-gap)/2,now=performance.now();state.dotHits=[];
  ctx.strokeStyle='#3f5c67';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(3,h/2);ctx.lineTo(pad,8);ctx.lineTo(w-pad/2,8);ctx.lineTo(w-3,h/2);ctx.lineTo(w-pad/2,h-8);ctx.lineTo(pad,h-8);ctx.closePath();ctx.stroke();
  const roster=state.campaign.outbreak&&state.healthRoster.length?state.healthRoster:state.campaign.crew,medical=state.fieldMode==='medical';const rooms=state.catalog.rooms;for(let ri=0;ri<rooms.length;ri++){const room=rooms[ri],x=pad+(ri%3)*(roomW+gap),y=20+Math.floor(ri/3)*(roomH+gap),people=roster.filter(p=>p.room===room.id),active=state.campaign.play.incident?.room===room.id;ctx.strokeStyle=active?'#eab44c':'#294550';ctx.strokeRect(x-5,y-4,roomW+10,roomH+5);ctx.fillStyle=active?'#eab44c':'#a8b5aa';ctx.font=`${small?7:10}px Menlo,monospace`;ctx.fillText((small?room.code:room.code+' / '+room.name.toUpperCase()),x,y+8);
    const availableH=roomH-22,cols=Math.max(1,Math.ceil(Math.sqrt(people.length*roomW/Math.max(1,availableH)))),rows=Math.ceil(people.length/cols),dx=roomW/cols,dy=availableH/rows,r=Math.max(.9,Math.min(dx,dy)*.30);
    people.forEach((p,j)=>{const px=x+(j%cols+.5)*dx,py=y+22+(Math.floor(j/cols)+.5)*dy,reaction=state.responses[p.id],health=healthStatus(p),pending=!medical&&state.busy&&state.job?.kind==='decision'&&!reaction,flash=!medical&&now-(state.flashes[p.id]||0)<580;ctx.globalAlpha=pending?.25:1;ctx.fillStyle=medical?HEALTH[health]||HEALTH.susceptible:flash?'#f2e9cf':COLORS[reaction?.choice||p.response||'unpolled'];if(medical&&health==='dead'){ctx.strokeStyle=HEALTH.dead;ctx.lineWidth=1.2;ctx.beginPath();ctx.moveTo(px-r,py-r);ctx.lineTo(px+r,py+r);ctx.moveTo(px+r,py-r);ctx.lineTo(px-r,py+r);ctx.stroke();}else{ctx.beginPath();ctx.arc(px,py,r,0,Math.PI*2);ctx.fill();}state.dotHits.push({x:px,y:py,r:Math.max(7,r),person:p});});ctx.globalAlpha=1;
  }
}
function drawPopulation(){if(state.view!=='voyage'||!state.shown)return;const a=canvasContext('population-canvas');if(!a)return;const{ctx,w,h}=a,people=state.shown.crew,cols=Math.ceil(Math.sqrt(people.length*w/Math.max(1,h))),rows=Math.ceil(people.length/cols),dx=w/cols,dy=h/rows,r=Math.min(dx,dy)*.3;people.forEach((p,i)=>{ctx.fillStyle=GEN[p.generation%GEN.length];ctx.beginPath();ctx.arc((i%cols+.5)*dx,(Math.floor(i/cols)+.5)*dy,r,0,Math.PI*2);ctx.fill();});}
function drawTimeline(){if(state.view!=='voyage'||!state.campaign)return;const a=canvasContext('population-chart');if(!a)return;const{ctx,w,h}=a,rows=state.campaign.play.timeline.filter(s=>s.year<=(state.shown?.stats.year??Infinity));if(rows.length<2)return;const max=Math.max(...rows.map(s=>s.crew))*1.05,min=Math.min(...rows.map(s=>s.crew))*.95,last=rows.at(-1).year||1;ctx.strokeStyle='#3f5c67';ctx.beginPath();ctx.moveTo(0,h-1);ctx.lineTo(w,h-1);ctx.stroke();ctx.strokeStyle='#39c6cb';ctx.lineWidth=2;ctx.beginPath();rows.forEach((s,i)=>{const x=s.year/last*w,y=h-5-(s.crew-min)/(max-min||1)*(h-10);i?ctx.lineTo(x,y):ctx.moveTo(x,y);});ctx.stroke();}
function drawAll(){drawCrew();drawPopulation();drawTimeline();drawOutbreakChart();}
function renderCurrent(){if(!state.campaign)return;if(state.view==='bridge')renderBridge();else renderVoyage();}

// === Large, focused Astra dialogue and inspectable provenance ===
function advisoryText(value){return esc(value).replace(/\*\*([^*\n]+)\*\*/g,'<strong>$1</strong>').replace(/`([^`\n]+)`/g,'<code>$1</code>');}
function renderChat(){const c=state.campaign;$('chat-context').textContent=`${c.name.toUpperCase()} · YEAR ${c.stats.year} · ${c.stats.crew} PEOPLE`;const messages=c.messages.filter(m=>m.role==='assistant'||(!m.text.startsWith('Wake me with')&&!m.text.startsWith('Report the consequence')&&!m.text.startsWith('Give the voyage')));$('chat-log').innerHTML=messages.slice(-12).map(m=>`<div class="chat-message ${m.role==='user'?'user':''}"><small>${m.role==='user'?'YOU':'ASTRA'}</small>${advisoryText(m.text)}</div>`).join('')||'<div class="chat-message"><small>ASTRA</small>What would you like to understand about the ship?</div>';if(state.busy&&state.job?.kind==='talk')$('chat-log').insertAdjacentHTML('beforeend','<div class="chat-message"><small>ASTRA · THINKING</small>…</div>');requestAnimationFrame(()=>{const log=$('chat-log'),last=log.lastElementChild;if(last&&!$('astra-planning').hidden)log.scrollTop+=last.getBoundingClientRect().top-log.getBoundingClientRect().top-12;});}

// === Admiral's council: advisory model calls cannot issue an order ===
const POLICY_COLORS={contain:'#39c6cb',care:'#eab44c',conserve:'#90aee5'};
function renderCrisisReading(){const a=state.campaign.play.crisis_assessment;$('crisis-reading').hidden=!a;if(!a)return;if(a.unavailable){$('crisis-reading').querySelector('summary').textContent='Jev reading unavailable';$('crisis-reading-content').innerHTML='<p>The model assessment is unavailable. Ship measurements remain available; no reading has been invented.</p>';return;}const stale=a.revision!==state.campaign.play.revision;$('crisis-reading').dataset.stale=String(stale);$('crisis-reading').querySelector('summary').textContent=(stale?'Earlier Jev reading: ':'Jev reading: ')+(a.urgency?.label||'inspect uncertainty');$('crisis-reading-content').innerHTML=['urgency','pressure','uncertainty'].filter(k=>a[k]).map(k=>`<section><h4>${esc(k)}</h4><strong>${esc(a[k].label)} · ${Math.round((a[k].probabilities?.[a[k].choice]||0)*100)}%</strong>${Object.entries(a[k].probabilities||{}).map(([id,p])=>`<div><span>${esc(a[k].labels?.[id]||id)}</span><b>${(p*100).toFixed(1)}%</b></div>`).join('')}</section>`).join('')+`<p>${stale?'Before your last order. ':''}Jev’s reading, not a clinical fact. Full uncertainty is shown.</p>`;}
function renderDeliberationBrief(){const c=state.campaign,s=c.outbreak,incident=c.play.incident;$('deliberation-brief').innerHTML=`<div><strong>${esc(incident?.title||'The ship is in your care')}</strong><span>${esc(c.mission.destination_name)} · year ${c.stats.year} · ${num(c.stats.remaining)} years remain</span></div>${s&&s.status!=='armed'?`<span><b class="infected-number">${num(s.infected)}</b> infected</span><span><b class="critical-number">${num(s.critical)}</b> critical</span><span><b>${num(s.deaths)}</b> lives lost</span><span><b>${num(s.care_capacity||0)}</b> surge places</span>`:`<span><b>${num(c.stats.crew)}</b> lives aboard</span><span><b>${num(c.stats.food_days)}</b> food days</span>`}`;$('chat-context').textContent=`${c.name.toUpperCase()} · ADMIRAL IN COMMAND`;}
function setDeliberationTab(tab){state.deliberationTab=tab;$('astra-planning').hidden=tab!=='astra';$('council-planning').hidden=tab!=='council';$('tab-astra').setAttribute('aria-pressed',String(tab==='astra'));$('tab-council').setAttribute('aria-pressed',String(tab==='council'));if(tab==='astra'){renderChat();$('chat-input').focus();}else{renderCouncil(state.council);$('council-question').focus();}}
function openDeliberation(tab='astra'){renderDeliberationBrief();if(!state.council)state.council=state.campaign.play.consultations?.at(-1)||null;if(!$('astra-dialog').open)$('astra-dialog').showModal();setDeliberationTab(tab);}
function councilBars(probs,labels){return Object.entries(labels||{}).map(([key,label])=>`<div class="council-bar"><div><span>${esc(label)}</span><strong>${probs?.[key]==null?'—':(probs[key]*100).toFixed(1)+'%'}</strong></div><i><b data-policy="${esc(key)}" data-width="${(probs?.[key]||0)*100}"></b></i></div>`).join('');}
function paintCouncilBars(parent){parent.querySelectorAll('[data-policy]').forEach(el=>{el.style.width=el.dataset.width+'%';el.style.background=POLICY_COLORS[el.dataset.policy]||COLORS.question;});}
function renderCouncilComposer(c){const complete=c&&(c.received===c.total||c.stage==='complete');if(complete&&state.lastPresentedCouncil!==c.id){state.lastPresentedCouncil=c.id;state.councilComposerOpen=false;}if(!c||!complete)state.councilComposerOpen=true;$('council-form').hidden=!state.councilComposerOpen;$('edit-council-question').hidden=state.councilComposerOpen;$('edit-council-question').disabled=state.busy;}
function renderCouncil(consultation){if(consultation){state.council=consultation;state.councilGroup=consultation.group;}const c=state.council;renderCouncilComposer(c);$('council-groups').querySelectorAll('[data-group]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.group===state.councilGroup)));if(!c){$('council-progress').textContent='Choose a council and ask a question. Consultation does not spend ship resources.';$('council-record-question').textContent='';$('council-distribution').replaceChildren();$('council-synthesis').textContent='';$('council-experts').replaceChildren();$('council-options').replaceChildren();$('council-roster').innerHTML='<p class="empty-council">The council has not been consulted yet. Each voice will be tied to an actual person aboard.</p>';$('council-member-count').textContent='No consultation yet';$('member-detail').hidden=true;return;}
  const total=c.total??c.people??c.members?.length??0,received=c.received??0,stale=c.revision!==state.campaign.play.revision;$('council-progress').innerHTML=`<strong>${num(received)} / ${num(total)} voices</strong><span>${stale?'BEFORE YOUR LAST ORDER':received<total?'JEV · LIVE POLICY COMPARISONS':`JEV · ${Number(c.seconds||0).toFixed(2)} SEC`} · ADVICE ONLY</span>`;$('council-record-question').textContent='They considered: “'+c.question+'”';$('council-member-count').textContent=c.sampled?`${num(total)} sampled of ${num(c.eligible_total)} eligible adults`:`${num(total)} actual ${c.group==='all'?'cross-ship':c.group} members`;$('council-distribution').innerHTML=councilBars(c.mean_distribution,c.labels);paintCouncilBars($('council-distribution'));$('council-synthesis').textContent=c.synthesis||c.synthesis_error||(received===total&&total?'Astra is synthesizing the council’s perspectives…':'Each person weighs the same concrete options from their own circumstances.');
  $('council-experts').innerHTML=(c.experts||[]).map(e=>{const member=c.members?.find(p=>p.id===e.person_id);return `<button class="expert-voice" data-person="${esc(e.person_id)}"><span>${esc(member?.specialty||member?.role||'Council voice')}</span><strong>${esc(member?.name||e.person_id)}</strong><p>${esc(e.statement)}</p><small>Astra-voiced fictional perspective · inspect evidence ↗</small></button>`;}).join('');$('council-experts').querySelectorAll('[data-person]').forEach(b=>b.onclick=()=>selectCouncilMember(b.dataset.person));
  $('council-options').innerHTML=(c.options||[]).map((o,i)=>`<div class="council-policy"><span>${esc(o.label)}</span><p>${esc(o.tradeoff||'')}</p><button data-draft="${i}" ${o.available===false?'disabled':''}>${o.available===false?'Unavailable now':'Draft this approach →'}</button>${o.available===false?`<small>${esc(o.barrier||'Outside current ship limits.')}</small>`:''}</div>`).join('');$('council-options').querySelectorAll('[data-draft]').forEach(b=>b.onclick=()=>draftCouncilOption(c.options[Number(b.dataset.draft)]));renderCouncilRoster();if(state.selectedMember)renderCouncilMember();
}
function renderCouncilRoster(){const c=state.council;if(!c)return;const q=$('member-search').value.toLowerCase().trim(),people=(c.members||[]).filter(p=>[p.name,p.id,p.role,p.specialty,p.priority].some(v=>String(v||'').toLowerCase().includes(q)));$('council-roster').innerHTML=people.slice(0,state.memberLimit).map(p=>{const r=c.positions?.[p.id];return `<button class="member-row ${state.selectedMember===p.id?'selected':''}" data-person="${esc(p.id)}"><span class="member-position" data-policy="${esc(r?.choice||'')}" aria-hidden="true"></span><span><strong>${esc(p.name||'Crew '+p.id)}</strong><small>${esc(p.specialty||p.role)} · ${p.age} years</small></span><em>${r?'↗':'…'}</em></button>`;}).join('')+(people.length>state.memberLimit?`<button id="council-more" class="quiet">Show more (${num(people.length-state.memberLimit)} remaining)</button>`:'');$('council-roster').querySelectorAll('[data-person]').forEach(b=>b.onclick=()=>selectCouncilMember(b.dataset.person));$('council-roster').querySelectorAll('.member-position').forEach(el=>el.style.background=POLICY_COLORS[el.dataset.policy]||COLORS.unpolled);if($('council-more'))$('council-more').onclick=()=>{state.memberLimit+=60;renderCouncilRoster();};}
function selectCouncilMember(id){state.selectedMember=id;renderCouncilRoster();renderCouncilMember();}
function renderCouncilMember(){const c=state.council,p=c?.members?.find(p=>p.id===state.selectedMember);if(!p){$('member-detail').hidden=true;return;}const r=c.positions?.[p.id];$('member-detail').hidden=false;$('member-detail').innerHTML=`<button id="close-member" class="quiet member-close" aria-label="Close profile">×</button><span class="eyebrow">ACTUAL FICTIONAL CREW PROFILE</span><h3>${esc(p.name||p.id)}</h3><p>${esc(p.specialty||p.role)} · age ${p.age} · generation ${p.generation}${p.experience_years!=null?`<br>${num(p.experience_years)} years of experience`:''}</p><p>${esc(p.temperament)}. Priority: ${esc(p.priority)}.</p><div class="member-probabilities">${councilBars(r?.probabilities,c.labels)}</div><button id="ask-specialist" class="planning-button">Ask this specialist ↗</button><small>Probabilities are Jev’s modeled policy preferences, not measured human opinions.</small>`;paintCouncilBars($('member-detail'));$('close-member').onclick=()=>{state.selectedMember=null;$('member-detail').hidden=true;renderCouncilRoster();};$('ask-specialist').disabled=state.busy;$('ask-specialist').onclick=()=>{if(state.busy)return;const question=$('council-question').value.trim()||`What does ${p.specialty||p.role} need me to understand before I authorize a response?`;startJob('consult',{group:state.councilGroup,question,person_id:p.id});};}
function draftCouncilOption(option){const commands={isolate:'Isolate the outbreak and restrict movement between compartments',surge_care:'Expand medical care and treat the critically ill',protect_food:'Protect food production and the ship’s reserve',hold:'Hold the current response'};const operations=option.operations||[];const text=operations.map(op=>commands[typeof op==='string'?op:op.kind||op.operation||op.id]||'').filter(Boolean).join('. ');$('decision').value=text?text+'.':option.label;state.selectedMember=null;$('astra-dialog').close();$('decision').focus();notice('Draft only. Review the order, then authorize it yourself.');}
function inspect(){const c=state.campaign,last=c.play.last_decision;$('inspect-content').innerHTML=`<h3>Three different jobs</h3><p>Astra interprets your text into supported operations. The engine validates stores, work and space. Jev evaluates how each simulated person responds—and those full distributions affect morale and legitimacy.</p><h3>Current limits</h3><p>${c.play.work} work units left in this incident. Protected food buffer: 45 days. Maximum growing area: ${num(Math.round(c.play.garden_limit))} m².</p><h3>Last committed decision</h3><pre>${esc(last?JSON.stringify({decision:last.decision,plan:last.plan,before:last.before,after:last.after,full_distribution:last.distributions},null,2):'No decision committed yet.')}</pre><h3>Provenance</h3><p>${c.jev.runs} committed Jev runs. ${c.astra.runs} Astra responses. Full requests, returned models and responses are retained in this expedition’s private local save. A blocked preview does not alter the ship.</p>`;$('inspect-dialog').showModal();}

// === User actions ===
$('next').onclick=()=>{if(state.step<5){state.step++;renderStep();}else{show('commission');$('board').hidden=true;$('draw-result').textContent='Running six independent commissioning trials…';$('trial-grid').innerHTML=Array.from({length:6},()=>'<div class="trial-placeholder"></div>').join('');startJob('generate');}};
$('back').onclick=()=>{if(state.step>0){state.step--;renderStep();}};
$('home').onclick=()=>{if(state.busy)return;show('wizard');renderStep();};
$('board').onclick=()=>{state.live=null;state.responses={};renderVoyage();};
$('cryo').onclick=()=>{state.frameYears=new Set();state.minimumFrameYear=state.campaign.stats.year;state.frameChain=Promise.resolve();$('voyage-title').textContent='The years move. Life goes on.';startJob('cryo');};
$('wake').onclick=()=>{state.live=null;state.responses={};state.flashes={};state.healthFrame=null;$('decision').value='';$('field-title').textContent='THE SHIP IS LISTENING';renderBridge();setFieldMode(state.campaign.outbreak?.active?'medical':'response');$('decision').focus();};
$('return-cryo').onclick=()=>{renderVoyage();$('cryo').click();};
$('new-voyage').onclick=()=>{state.step=0;show('wizard');renderStep();};
$('suggest').onclick=()=>{$('decision').value=state.campaign.play.incident.suggestion;$('decision').focus();};
$('decision-form').onsubmit=e=>{e.preventDefault();if(state.busy||state.campaign.play.mode!=='awake')return;state.live=null;state.responses={};state.flashes={};state.healthFrame=null;state.outbreakFrames=[];state.outbreakDays=new Set();state.frameChain=Promise.resolve();state.healthRoster=healthRoster(state.campaign);setFieldMode('response');$('person-inspector').hidden=true;applyJev({received:0,total:state.campaign.stats.crew,responses:{},seconds:0,mean_distribution:{}});startJob('decision',{decision:$('decision').value.trim()});};
$('view-response').onclick=()=>setFieldMode('response');$('view-medical').onclick=()=>setFieldMode('medical');
$('replay-outcome').onclick=async()=>{if(state.busy||state.outcomePlaying)return;const frames=savedOutbreakFrames();if(!frames.length)return;state.outbreakDays=new Set();state.outbreakFrames=[];setBusy(true,'Replaying saved medical days — no new simulation');try{enqueueOutbreakFrames(frames);await awaitFrames();}finally{setBusy(false);}renderBridge();};
$('astra-open').onclick=()=>openDeliberation('astra');$('plan-open').onclick=()=>openDeliberation('astra');
$('tab-astra').onclick=()=>setDeliberationTab('astra');$('tab-council').onclick=()=>setDeliberationTab('council');
$('return-authorize').onclick=()=>{$('astra-dialog').close();$('decision').focus();};
$('council-groups').querySelectorAll('[data-group]').forEach(b=>b.onclick=()=>{state.councilGroup=b.dataset.group;state.council=state.campaign.play.consultations?.filter(c=>c.group===state.councilGroup&&!c.person_id).at(-1)||null;state.selectedMember=null;state.memberLimit=60;renderCouncil(state.council);});
$('edit-council-question').onclick=()=>{state.councilComposerOpen=true;renderCouncilComposer(state.council);$('council-question').focus();};
$('council-form').onsubmit=e=>{e.preventDefault();if(state.busy)return;state.selectedMember=null;state.memberLimit=60;startJob('consult',{group:state.councilGroup,question:$('council-question').value.trim()});};
$('member-search').oninput=()=>{state.memberLimit=60;renderCouncilRoster();};
$('chat-form').onsubmit=e=>{e.preventDefault();if(state.busy)return;const message=$('chat-input').value.trim();if(!message)return;$('chat-input').value='';$('chat-log').insertAdjacentHTML('beforeend',`<div class="chat-message user"><small>YOU</small>${esc(message)}</div><div class="chat-message"><small>ASTRA · THINKING</small>…</div>`);$('chat-log').scrollTop=$('chat-log').scrollHeight;startJob('talk',{message});};
$('about-open').onclick=()=>$('about-dialog').showModal();$('inspect-open').onclick=inspect;$('notice-close').onclick=()=>$('notice').hidden=true;
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>$(b.dataset.close).close());
$('crew-canvas').onclick=e=>{const rect=e.currentTarget.getBoundingClientRect(),x=e.clientX-rect.left,y=e.clientY-rect.top;const hit=state.dotHits.find(d=>Math.hypot(d.x-x,d.y-y)<d.r);if(!hit){$('person-inspector').hidden=true;return;}const p=hit.person,r=state.responses[p.id];$('person-inspector').innerHTML=`<strong>CREW ${esc(p.id)} · AGE ${p.age}</strong><p>${esc(p.temperament)} · generation ${p.generation}<br>${esc(p.priority)}</p>${state.fieldMode==='medical'?`<p>HEALTH: ${esc(healthStatus(p)).toUpperCase()}<br>Day ${currentOutbreak()?.day||0} of the outbreak</p>`:`<p>${r?Object.entries(r.probabilities).map(([k,v])=>k.toUpperCase()+' '+(v*100).toFixed(1)+'%').join('<br>'):'No response received for this person yet.'}</p>`}`;$('person-inspector').hidden=false;};
new ResizeObserver(drawAll).observe($('app'));
let lastPaint=0;function animate(t){requestAnimationFrame(animate);if(state.view==='bridge'&&t-lastPaint>50){lastPaint=t;drawCrew();}}requestAnimationFrame(animate);

// === Bootstrap and reconnect ===
async function boot(){try{const b=await api('/api/bootstrap');state.catalog=b.catalog;state.token=b.token;$('connection').textContent=b.providers.astra.configured&&b.providers.jev.configured?'ASTRA + JEV · READY':'MODEL CREDENTIALS NEEDED';renderStep();const saves=b.saves.filter(s=>s.version===2);if(saves.length){const s=saves[0];$('resume-row').innerHTML=`<button id="resume">Resume ${esc(s.name)} · year ${s.year} ↗</button>`;$('resume-row').hidden=false;$('resume').onclick=async()=>{try{applyCampaign(await api('/api/campaign/'+s.id));state.live=null;state.responses=state.campaign.responses||{};state.campaign.play.mode==='awake'?renderBridge():renderVoyage();}catch(e){notice(e.message);}};}
    const pending=JSON.parse(localStorage.getItem('proxima-v2-job')||'null')||b.active_job;if(pending){state.job=pending;state.pendingChat=pending.message;if(pending.campaign_id){applyCampaign(await api('/api/campaign/'+pending.campaign_id));state.minimumFrameYear=state.campaign.stats.year;}if(pending.kind==='generate')show('commission');else if(pending.kind==='cryo')renderVoyage();else renderBridge();if(pending.kind==='consult'||pending.kind==='talk')openDeliberation(pending.kind==='consult'?'council':'astra');setBusy(true,'Reconnecting to your operation');
      // Reconnect must never strand the busy flag (issue #1): before this
      // guard, one exception here left an eternal spinner over an idle server.
      try{await pollJob();}catch(err){state.job=null;localStorage.removeItem('proxima-v2-job');setBusy(false);noticeRestore(err.message);}}
    else{const query=new URLSearchParams(location.search),id=query.get('campaign'),screen=query.get('screen');if(id&&/^[a-f0-9]{16}$/.test(id)){applyCampaign(await api('/api/campaign/'+id));state.live=null;state.responses=state.campaign.responses||{};if(state.campaign.play.incident&&['awake','ready'].includes(state.campaign.play.mode)){renderBridge();setFieldMode(state.campaign.play.incident.kind==='outbreak'?'medical':'response');if(screen==='council'||screen==='astra')openDeliberation(screen==='council'?'council':'astra');}else renderVoyage();}}
  }catch(e){notice(e.message);$('connection').textContent='OFFLINE';}}
boot();

// === C: VIGIL — resident Claude ship-intelligence agent panel (additive, self-contained) ===
// A real Claude agent (Agent SDK over the local CLI) runs server-side with bounded read-only
// tools over the saved campaign; this panel streams its tool calls, working notes, token usage
// and the layered technical brief. Transparency is the feature. Styles use a constructed
// stylesheet because the CSP (style-src 'self') blocks inline <style>. Existing code is only
// touched by wrapping the setDeliberationTab binding so 'agent' becomes a first-class tab.
{
  const AGENT_CSS=`
  .agent-panel{display:flex;flex-direction:column;gap:12px;min-height:0}
  .agent-head{display:flex;justify-content:space-between;align-items:baseline;gap:12px;border-bottom:1px solid #294550;padding-bottom:8px}
  .agent-head h3{margin:2px 0 0;font-size:19px;letter-spacing:.02em;color:#f2e9cf}
  .agent-model{font:11px Menlo,monospace;color:#39c6cb;border:1px solid #294550;padding:2px 7px;margin-left:8px;vertical-align:middle}
  .agent-usage{font:11px Menlo,monospace;color:#a8b5aa;white-space:nowrap}
  .agent-columns{display:grid;grid-template-columns:minmax(260px,2fr) 3fr;gap:12px;height:50vh;min-height:260px}
  .agent-col-top{display:flex;justify-content:space-between;gap:8px;font:10px Menlo,monospace;letter-spacing:.08em;color:#a8b5aa;border-bottom:1px solid #294550;padding-bottom:5px;margin-bottom:6px}
  .agent-feed-wrap,.agent-output-wrap{display:flex;flex-direction:column;min-height:0;background:#0b202c;border:1px solid #294550;padding:10px}
  .agent-feed{flex:1;overflow-y:auto;font:11px/1.55 Menlo,monospace;color:#a8b5aa}
  .agent-feed .agent-empty{color:#567883;font-family:inherit}
  .agent-ev{margin:0 0 3px;white-space:pre-wrap;word-break:break-word}
  .agent-ev b{font-weight:600}
  .agent-ev.call b{color:#eab44c}.agent-ev.res b{color:#39c6cb}.agent-ev.think{color:#567883}.agent-ev.done b{color:#f2e9cf}.agent-ev.err b{color:#df5860}
  .agent-ev.note{color:#d8d2bd;border-left:2px solid #3f5c67;padding-left:7px;margin:5px 0}
  .agent-brief{overflow-y:auto;min-height:0}
  .agent-answer{overflow-y:auto;color:#e6dfc8;font-size:14px;line-height:1.55;border-top:1px dashed #294550;margin-top:8px;padding-top:8px;max-height:14em}
  .agent-answer:empty,.agent-brief:empty{display:none}
  .agent-brief .brief-situation{color:#e6dfc8;font-size:13.5px;line-height:1.55;margin:0 0 10px}
  .agent-brief details{border:1px solid #294550;margin:0 0 7px;background:#081a24}
  .agent-brief summary{cursor:pointer;padding:7px 9px;font-size:13px;color:#f2e9cf;letter-spacing:.02em}
  .agent-brief summary .dom{font:9.5px Menlo,monospace;color:#39c6cb;letter-spacing:.1em;margin-right:8px;text-transform:uppercase}
  .agent-brief .sec-body{padding:2px 11px 10px;font-size:13px;line-height:1.55;color:#cfd6c9}
  .agent-brief .sec-body>p{margin:6px 0}
  .agent-brief details details{margin:6px 0;border-color:#1d3540}
  .agent-brief details details summary{font-size:12px;color:#d8d2bd}
  .agent-brief .depth{font:9px Menlo,monospace;color:#eab44c;letter-spacing:.1em;margin-left:7px;text-transform:uppercase}
  .agent-brief .layer-body{padding:2px 10px 9px;white-space:pre-wrap}
  .agent-brief h5{margin:9px 0 3px;font:10px Menlo,monospace;letter-spacing:.1em;color:#a8b5aa;text-transform:uppercase}
  .agent-brief ul{margin:0;padding-left:17px}
  .agent-brief li{margin:2px 0;font-size:12.5px}
  .agent-brief .claim-src{font:10px Menlo,monospace;color:#39c6cb;margin-left:6px}
  .agent-brief .claim-src.modeled{color:#eab44c}
  #agent-brief-provenance{color:#567883;text-align:right}
  #agent-brief-provenance .stale{color:#eab44c}
  .agent-actions{display:flex;gap:10px;justify-content:flex-end;margin-top:8px}
  body.working .agent-actions button{pointer-events:none;opacity:.4}
  #agent-form textarea{width:100%;box-sizing:border-box}
  .agent-provenance{font:10.5px Menlo,monospace;color:#567883;margin:0}
  @media (max-width:900px){.agent-columns{grid-template-columns:1fr;height:auto}.agent-feed{max-height:30vh}}`;
  try{const sheet=new CSSStyleSheet();sheet.replaceSync(AGENT_CSS);document.adoptedStyleSheets=[...document.adoptedStyleSheets,sheet];}catch{}

  const agentState={job:null,live:null};
  const paras=v=>esc(v).split(/\n{2,}/).map(p=>'<p>'+p.replace(/\n/g,'<br>')+'</p>').join('');
  const kTokens=u=>{if(!u)return null;const i=(u.input_tokens||0)+(u.cache_read_input_tokens||0)+(u.cache_creation_input_tokens||0),o=u.output_tokens||0;return `IN ${num(i)} · OUT ${num(o)} TOK`;};

  function agentEventRow(ev){
    const t=new Date(ev.t*1000).toLocaleTimeString('en-US',{hour12:false});
    if(ev.kind==='tool_call')return `<p class="agent-ev call">${t} → <b>${esc(ev.tool)}</b> ${esc(ev.args||'')}</p>`;
    if(ev.kind==='tool_result')return `<p class="agent-ev ${ev.error?'err':'res'}">${t} ← <b>${esc(ev.tool)}</b> · ${num(ev.ms||0)}ms${ev.chars?` · ${num(ev.chars)} chars`:''}${ev.error?` · <b>${esc(ev.error)}</b>`:''}</p>`;
    if(ev.kind==='thinking')return `<p class="agent-ev think">${t} · thinking (${num(ev.chars||0)} chars, not returned verbatim)</p>`;
    if(ev.kind==='note')return `<p class="agent-ev note">${esc(ev.text)}</p>`;
    if(ev.kind==='result')return `<p class="agent-ev ${ev.is_error?'err':'done'}">${t} ■ <b>${ev.is_error?'AGENT ERROR':'AGENT DONE'}</b> · ${num(ev.num_turns||0)} turns · ${((ev.duration_ms||0)/1000).toFixed(1)}s${ev.cost_usd!=null?` · $${Number(ev.cost_usd).toFixed(4)}`:''}</p>`;
    return '';
  }
  function renderAgentFeed(events){const feed=$('agent-feed');if(!events?.length){return;}feed.innerHTML=events.map(agentEventRow).join('');feed.scrollTop=feed.scrollHeight;}
  function renderAgentBrief(brief){
    const box=$('agent-brief'),provEl=$('agent-brief-provenance');
    if(!brief){box.replaceChildren();provEl.textContent='';return;}
    const prov=brief.provenance||{},stale=state.campaign&&prov.revision!==state.campaign.play.revision;
    provEl.innerHTML=`${esc(prov.attribution||'')} · ${esc(prov.model||'')}${stale?' · <span class="stale">BEFORE YOUR LAST ORDER</span>':''}`;
    box.innerHTML=`<p class="brief-situation">${esc(brief.situation||'')}</p>`+(brief.sections||[]).map(sec=>`
      <details><summary><span class="dom">${esc(sec.domain||'')}</span>${esc(sec.title||'')}</summary><div class="sec-body">
        <p>${esc(sec.summary||'')}</p>
        ${(sec.layers||[]).map(l=>`<details><summary>${esc(l.heading||'')}<span class="depth">${esc(l.depth||'')}</span></summary><div class="layer-body">${esc(l.body||'')}</div></details>`).join('')}
        ${['assumptions','uncertainties','competing_interpretations'].map(k=>(sec[k]||[]).length?`<h5>${k.replace(/_/g,' ')}</h5><ul>${sec[k].map(v=>`<li>${esc(v)}</li>`).join('')}</ul>`:'').join('')}
        ${(sec.claims||[]).length?`<h5>traced claims</h5><ul>${sec.claims.map(c=>`<li>${esc(c.claim)}<span class="claim-src ${c.source==='modeled_estimate'?'modeled':''}">${esc(c.source)}</span></li>`).join('')}</ul>`:''}
      </div></details>`).join('');
  }
  function renderAgentPanel(agent){
    const play=state.campaign?.play||{};
    const last=agentState.live||agent||play.agent_activity?.at(-1)||null;
    if(last?.model)$('agent-model').textContent=last.model;
    const bits=[kTokens(last?.usage),last?.cost_usd!=null?`$${Number(last.cost_usd).toFixed(4)}`:null,last?.seconds!=null?`${last.seconds}s`:null,last?.status==='complete'?null:last?.status?.toUpperCase()].filter(Boolean);
    $('agent-usage').textContent=bits.join(' · ')||'STANDING BY';
    renderAgentFeed(last?.events);
    renderAgentBrief((agentState.live&&agentState.live.brief)||play.technical_brief||null);
    $('agent-answer').innerHTML=last?.answer?paras(last.answer):'';
    if(last?.answer)$('agent-answer').scrollTop=0;
  }
  function showAgentTab(){
    $('astra-planning').hidden=true;$('council-planning').hidden=true;$('agent-panel').hidden=false;
    $('tab-astra').setAttribute('aria-pressed','false');$('tab-council').setAttribute('aria-pressed','false');$('tab-agent').setAttribute('aria-pressed','true');
    state.deliberationTab='agent';renderAgentPanel(null);
    if(!state.campaign?.play?.technical_brief&&!agentState.live)$('agent-input').placeholder='No brief compiled for this wake yet. Compile it, then interrogate every layer.';
    $('agent-input').focus();
  }
  // Wrap the existing tab switcher: 'agent' routes here; any other tab hides this panel.
  const __setDeliberationTab=setDeliberationTab;
  setDeliberationTab=tab=>{if(tab==='agent'){renderDeliberationBrief();showAgentTab();return;}$('agent-panel').hidden=true;$('tab-agent').setAttribute('aria-pressed','false');__setDeliberationTab(tab);};
  $('tab-agent').onclick=()=>setDeliberationTab('agent');

  async function startAgentRun(payload){
    if(state.busy||!state.campaign)return;
    agentState.live={status:'starting',events:[],model:$('agent-model').textContent};
    setBusy(true,'CLAUDE · ship intelligence reading the saved ship');
    try{
      const {job_id}=await api('/api/agent',{campaign_id:state.campaign.id,...payload});
      agentState.job=job_id;
      let errors=0;
      while(agentState.job){
        let j;
        try{j=await api('/api/jobs/'+job_id);errors=0;}
        catch(e){if(++errors>12)throw e;await sleep(900);continue;}
        if(j.agent){agentState.live=j.agent;if(state.deliberationTab==='agent')renderAgentPanel(j.agent);}
        if(j.status==='done'||j.status==='failed'){
          agentState.job=null;
          const result=j.result||{};
          if(result.campaign||j.campaign)applyCampaign(result.campaign||j.campaign);
          setBusy(false);
          if(j.status==='failed'){agentState.live={...(agentState.live||{}),status:'failed'};notice(j.error||'The ship-intelligence run could not be completed. Saved state is intact.');}
          else{agentState.live=result.agent||agentState.live;}
          if(state.deliberationTab==='agent')renderAgentPanel(agentState.live);
          if(state.view==='bridge')renderBridge();
          return;
        }
        await sleep(220);
      }
    }catch(e){agentState.job=null;setBusy(false);notice(e.message);}
  }
  $('agent-brief-run').onclick=()=>startAgentRun({mode:'brief'});
  $('agent-form').onsubmit=e=>{e.preventDefault();const question=$('agent-input').value.trim();if(!question)return;$('agent-input').value='';startAgentRun({mode:'ask',question});};
}
