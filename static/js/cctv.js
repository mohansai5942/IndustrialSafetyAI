const $=id=>document.getElementById(id);let previousAlarm=false;let alarmTimer=null;let audioCtx=null;let audioOsc=null;let audioGain=null;
function startAlarmSound(){if(alarmTimer)return;try{audioCtx=audioCtx||new (window.AudioContext||window.webkitAudioContext)();audioGain=audioCtx.createGain();audioGain.gain.value=.10;audioGain.connect(audioCtx.destination);alarmOscillator();alarmTimer=setInterval(alarmOscillator,900)}catch(e){}}
function alarmOscillator(){if(!audioCtx||audioCtx.state==='closed')return;try{const osc=audioCtx.createOscillator();osc.type='square';osc.frequency.value=880;osc.connect(audioGain);osc.start();setTimeout(()=>{try{osc.stop()}catch(e){}},350)}catch(e){}}
function stopAlarmSound(){if(alarmTimer){clearInterval(alarmTimer);alarmTimer=null}try{if(audioOsc)audioOsc.stop()}catch(e){}audioOsc=null}
async function api(url,options={}){const r=await fetch(url,options);return r.json()}
function post(url,data){return api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})}
async function sendInput(k,v){$(k+'v').textContent=v;await post('/api/inputs',{[k]:v})}
async function demo(k){const r=await post('/api/demo',{key:k});if(!r.ok)return;refresh()}
async function ack(){await post('/api/ack');refresh()}
async function resetAll(){await post('/api/reset');refresh()}
async function preset(p){const v=p==='normal'?{gas:10,temperature:40,pressure:5,equipment:0}:p==='warning'?{gas:45,temperature:70,pressure:6.8,equipment:40}:{gas:80,temperature:95,pressure:8.5,equipment:80};await post('/api/inputs',v);for(const [k,x] of Object.entries(v)){$(k).value=x;$(k+'v').textContent=x}refresh()}
async function refresh(){
 try{const s=await api('/api/state');
 if(window.IndustrialCamera)window.IndustrialCamera.updateServerState(s);
 $('risk').textContent=s.risk+'/100';$('workers').textContent=s.workers;$('fps').textContent=s.camera_fps;$('level').textContent=s.level;$('level').className='big '+s.level.toLowerCase();
 $('events').innerHTML='<b>'+s.hazard+'</b><br>'+s.last_event+'<br><span class="muted">Gas '+s.gas+' ppm • Temp '+s.temperature+' °C • Pressure '+s.pressure+' bar • Equipment '+s.equipment+'</span>';
 $('detect').innerHTML='🚧 Restricted zone: <b>'+s.restricted+'</b><br>💨 Aerosol/mist: <b>'+(s.aerosol?'YES':'NO')+'</b><br>🔥 Fire-like visual: <b>'+(s.fire?'YES':'NO')+'</b><br>🧍 Possible fall: <b>'+(s.fall?'YES':'NO')+'</b><br>🪖 Helmet not verified: <b>'+s.helmet_not_verified+'</b><br>🎯 Vision confidence: <b>'+s.vision_confidence.toFixed(1)+'%</b>';
 const labels={ppe:'🪖 Helmet / PPE',aerosol:'💨 Aerosol',fire:'🔥 Fire-like',fall:'🧍 Fall'};$('demoStatus').innerHTML=Object.entries(s.demo).map(([k,v])=>`<span class="pill ${v?'on':'off'}">${labels[k]}: ${v?'ON':'OFF'}</span>`).join('');for(const k of Object.keys(labels))$('btn-'+k).classList.toggle('active',!!s.demo[k]);
 $('alert').style.display=s.alarm?'block':'none';$('alert').className='alert'+(s.level==='CRITICAL'?' critical-alert':'');$('alertTitle').textContent=s.level==='CRITICAL'?'🚨 CRITICAL SAFETY ALERT':'🚨 HIGH-RISK SAFETY ALERT';$('alertText').textContent=(s.hazard||'Safety event detected')+' • Risk '+s.risk+'/100';$('timer').textContent=s.ack_required?(s.escalated?'Escalation sent after 15 seconds. Acknowledgement is still required.':'Acknowledgement required within '+s.ack_seconds+' seconds.'):(s.acknowledged?'Acknowledged.':'');$('escalation').textContent=s.escalated?'Notification sent to '+(s.escalated_to||'Next Responsible Person / Area In-Charge'):'';$('ackBtn').disabled=!s.alarm;$('ackBtn').textContent='ACKNOWLEDGE / VERIFY';
 if(s.alarm&&!s.acknowledged)startAlarmSound();else stopAlarmSound();previousAlarm=s.alarm;await loadIncidents();
 }catch(e){}
}
async function loadIncidents(){const a=await api('/api/analytics');$('total').textContent=a.total;$('critical').textContent=a.critical;$('analytics').innerHTML=a.by_type.length?a.by_type.map(x=>'• '+x.type+' — '+x.count+' incident(s), avg risk <b>'+x.avg_risk+'/100</b>, max '+x.max_risk).join('<br>'):'No incidents yet.';const r=await api('/api/incidents');$('rows').innerHTML=r.map(x=>`<tr><td>${x.id}</td><td>${x.source||'CCTV / AI Vision'}</td><td>${x.timestamp}</td><td>${x.incident_type}</td><td>${x.risk}/100</td><td>${x.sif_classification||'Normal condition'}</td><td>${x.severity}</td><td>${x.status}</td><td><a href="/api/report/${x.id}">PDF</a></td></tr>`).join('')}
setInterval(refresh,1000);refresh();
