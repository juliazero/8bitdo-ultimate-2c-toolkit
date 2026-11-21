// SPDX-License-Identifier: GPL-3.0-or-later
// Controls for the protocol laboratory; never sends requests to real HID paths.
let labPanel = null, labState = null, labBusy = false, labQueue = Promise.resolve();
// Pointer holds are momentary; a second tap within 350 ms latches the button.
const labLatched = new Set(), labPointers = new Map(), labTaps = new Map();
function labButtonSend(name, down) {
  return labSend({action:'button',button:name,down});
}
function labReleaseMomentary() {
  for (const name of new Set(labPointers.values()))
    if (!labLatched.has(name)) labButtonSend(name, false).catch(()=>{});
  labPointers.clear(); labTaps.clear();
}
function labBindButton(b, name) {
  b.style.touchAction='none'; b.style.userSelect='none';
  b.onpointerdown=e=> {
    if (e.button !== 0) return;
    e.preventDefault(); b.setPointerCapture(e.pointerId);
    const now=performance.now(), last=labTaps.get(name);
    if (labLatched.has(name)) { labLatched.delete(name); labTaps.delete(name); }
    else if (last !== undefined && now-last <= 350) { labLatched.add(name); labTaps.delete(name); }
    else labTaps.set(name,now);
    labPointers.set(e.pointerId,name);
    labButtonSend(name,true).catch(()=>{});
  };
  const release=e=> {
    if (!labPointers.has(e.pointerId)) return;
    labPointers.delete(e.pointerId);
    if (e.type==='pointercancel') { labTaps.delete(name); labLatched.delete(name); }
    if (!labLatched.has(name) && ![...labPointers.values()].includes(name)) labButtonSend(name,false).catch(()=>{});
  };
  b.onpointerup=release; b.onpointercancel=release; b.onlostpointercapture=release;
  b.onkeydown=e=> {
    if (![' ','Enter'].includes(e.key) || e.repeat) return;
    e.preventDefault(); labPointers.set('key:'+name,name); labButtonSend(name,true).catch(()=>{});
  };
  b.onkeyup=e=> {
    if (![' ','Enter'].includes(e.key)) return;
    e.preventDefault(); labPointers.delete('key:'+name);
    if (!labLatched.has(name)) labButtonSend(name,false).catch(()=>{});
  };
  b.onblur=()=> { if (labPointers.delete('key:'+name) && !labLatched.has(name)) labButtonSend(name,false).catch(()=>{}); };
  b.onclick=e=> { if (e.detail===0 && !labPointers.has('key:'+name)) {
    labButtonSend(name,true).then(()=>labButtonSend(name,false)).catch(()=>{});
  }};
}
function labConnections(device, selected) {
  const select = labPanel.querySelector('[data-lab=connection]');
  if (select.dataset.device !== device) {
    select.replaceChildren();
    const choices = device === 'controller' ? [['update','USB - Update mode'],['usb','USB'],['dongle','Dongle 2.4 GHz'],['bluetooth','Bluetooth']] : [['update','USB - Update mode'],['usb','USB - Idle']];
    choices.forEach(([value, text]) => { const o=document.createElement('option'); o.value=value; o.textContent=text; select.append(o); });
    select.dataset.device = device;
  }
  select.value = selected;
}
function labPaint(state) {
  labState = state;
  if (!labPanel) return;
  labPanel.querySelector('[data-lab=status]').textContent =
    `${state.profile} · ${state.reconnecting ? 'connecting…' : state.input.connected ? 'virtual pad on' : 'virtual pad off'} · protocol model · motors: ${state.motors}`;
  labPanel.querySelector('[data-lab=device]').value = state.device;
  labConnections(state.device, state.connection);
  const usb = state.device === 'controller' && ['usb','update'].includes(state.connection);
  const power = labPanel.querySelector('[data-lab=power]');
  power.disabled = false;
  power.textContent = state.device === 'dongle' ? (state.dongle_attached ? 'Disconnect USB' : 'Connect USB') : usb ? 'Disconnect USB' : 'Power on/off';
  const dongleUSB = labPanel.querySelector('[data-lab=dongle-usb]');
  dongleUSB.hidden = state.device === 'dongle';
  dongleUSB.textContent = state.dongle_attached ? 'Disconnect dongle' : 'Connect dongle';
  power.title = usb ? 'Continue on battery using the rear switch position after 2 seconds' : '';
  labPanel.querySelector('[data-lab=rear-switch]').value = state.rear_switch;
  for (const [field,value] of [['battery-percent',state.hardware.battery_percent],['battery-mv',state.hardware.battery_mv]]) {
    const input=labPanel.querySelector('[data-lab='+field+']');
    if (document.activeElement !== input) input.value=value;
  }
  const ringNames=['normal','25 %','5 %','1 %','0.1 %','off'];
  labPanel.querySelector('[data-lab=ring]').textContent = state.ring.available ? `Home ring: ${ringNames[state.ring.live]} · saved startup: ${ringNames[state.ring.startup]} · L4 + R4: ${state.ring.chord_hold_seconds ? "hold " + state.ring.chord_hold_seconds + " s" : "immediate"}` : 'Home ring tweak is not installed';
  labPanel.querySelectorAll('[data-lab-axis]').forEach(el=>{if(document.activeElement!==el) el.value=state.raw_axes[el.dataset.labAxis];});
  labPanel.querySelectorAll('[data-lab-button]').forEach(b => {
    const on = (state.held || []).includes(b.dataset.labButton);
    b.classList.toggle('sim-held', on); b.setAttribute('aria-pressed', on);
  });
  if (typeof testVisible !== 'undefined' && testVisible) window.u2cDiagnostics({...state.input, type:'state'});
  if (state.input.connected && ((typeof testVisible !== 'undefined' && testVisible) ||
      (typeof tab !== 'undefined' && tab === 'settings'))) window.u2cDiagnostics({...state.input, type:'input'});
}
function labSend(command) {
  const next = labQueue.then(async () => {
    const state = await api('POST','/api/simulation',command);
    labPaint(state);
    labPanel.querySelector('[data-lab=error]').textContent = '';
    if (['profile','connection'].includes(command.action)) { labLatched.clear(); labPointers.clear(); labTaps.clear(); }
    if (['profile','connection','power','disconnect_usb','rear_switch','dongle_usb'].includes(command.action)) {
      await refresh();
    }
    return state;
  });
  labQueue = next.catch(e => { if (labPanel) labPanel.querySelector('[data-lab=error]').textContent = e.message; });
  return next;
}
async function labPoll() {
  if (labBusy || document.hidden) return;
  labBusy = true;
  try {
    const pending = labState?.reconnecting, usbMode = labState?.usb_dinput, profile = labState?.profile;
    const state = await api('GET','/api/simulation');
    labPaint(state);
    if ((pending && !state.reconnecting) || (profile !== undefined && profile !== state.profile) || (usbMode !== undefined && usbMode !== state.usb_dinput)) await refresh();
  } catch (_) {} finally { labBusy = false; }
}
function simulationMessage(m) {
  if (m.op === 'start') labPoll();
  if (m.op === 'vibrate') labSend({action:'vibrate',locality:m.locality})
    .then(() => window.u2cDiagnostics({type:'result',id:m.id,ok:true}))
    .catch(e => window.u2cDiagnostics({type:'result',id:m.id,ok:false,error:e.message}));
  if (m.op === 'ring') labSend({action:'ring',level:m.level,save:!!m.save})
    .then(()=>window.u2cDiagnostics({type:'result',op:'ring',id:m.id,ok:true}))
    .catch(e=>window.u2cDiagnostics({type:'result',op:'ring',id:m.id,ok:false,error:e.message}));
  if (m.op === 'direct') window.u2cDiagnostics({type:'result',op:m.op,id:m.id,ok:false,error:'This report is not emulated.'});
}
function simulationRender() {
  if (!st || !st.simulation_controls || labPanel) return;
  labPanel = document.createElement('section'); labPanel.className = 'sim-panel';
  labPanel.innerHTML = `<h3>Virtual controller — protocol model</h3>
    <p>Hold a button to press it; release to let go. Double-tap to latch, then tap to release. Hold Home for 12 seconds to power off without USB. Home + L4 + R4 for 3 seconds enters update mode; connecting USB with L4 + R4 held also enters update mode. For 2.4 GHz, install the dongle D-input tweak first, then hold B across power off/on for D-input, or X for XInput. Without a held mode button, the last mode is restored. With Bluetooth mode selection installed, B/X + Home from off also selects and remembers the Bluetooth profile; without it, identity follows the installed firmware. With the updated Bluetooth mode selection tweak, hold Home + B/X for 3 seconds while connected over Bluetooth or 2.4 GHz to restart in that mode.</p>
    <label>Device <select data-lab="device"><option value="controller">Controller</option><option value="dongle">Dongle</option></select></label>
    <label>Rear switch <select data-lab="rear-switch"><option value="dongle">2.4 GHz</option><option value="bluetooth">Bluetooth</option></select></label>
    <label>Connection <select data-lab="connection"></select></label> <button class="sec small" data-lab="power">Power on/off</button>
    <button class="sec small" data-lab="dongle-usb">Disconnect dongle</button>
    <button class="sec small" data-lab="release">Release All Buttons</button>
    <div><label>Controller battery (%) <input type="number" min="0" max="100" step="1" data-lab="battery-percent" value="100"></label> <label>Voltage (mV) <input type="number" min="0" max="5000" step="1" data-lab="battery-mv" value="4200"></label><small>Independent test values; charging and automatic battery shutdown are not simulated.</small></div>
    <p data-lab="status"></p><p data-lab="ring"></p><div class="sim-buttons"></div><div class="sim-axes"></div><p data-lab="error" role="status"></p>`;
  document.querySelector('main .wrap').prepend(labPanel);
  const device = labPanel.querySelector('[data-lab=device]');
  const connection = labPanel.querySelector('[data-lab=connection]');
  device.onchange = () => {
    labConnections(device.value, 'usb');
    labSend({action:'connection',device:device.value,connection:connection.value}).catch(()=>{});
  };
  connection.onchange = () => labSend({action:'connection',device:device.value,connection:connection.value}).catch(()=>{});
  const toggleDongle = () => labSend({action:'dongle_usb',connected:!labState?.dongle_attached}).catch(()=>{});
  labPanel.querySelector('[data-lab=dongle-usb]').onclick = toggleDongle;
  labPanel.querySelector('[data-lab=power]').onclick = () => labState?.device === 'dongle' ? toggleDongle() : labSend({action:['usb','update'].includes(labState?.connection) ? 'disconnect_usb' : 'power'}).catch(()=>{});
  labPanel.querySelector('[data-lab=rear-switch]').onchange = e => labSend({action:'rear_switch',value:e.target.value}).catch(()=>{});
  for (const field of ['battery-percent','battery-mv']) labPanel.querySelector('[data-lab='+field+']').onchange = () => labSend({action:'battery',percent:Number(labPanel.querySelector('[data-lab=battery-percent]').value),millivolts:Number(labPanel.querySelector('[data-lab=battery-mv]').value)}).catch(()=>{});
  labPanel.querySelector('[data-lab=release]').onclick=()=>{labLatched.clear();labPointers.clear();labTaps.clear();labSend({action:'release'}).catch(()=>{});};
  window.addEventListener('blur',labReleaseMomentary);
  document.addEventListener('visibilitychange',()=>{if(document.hidden)labReleaseMomentary();});
  const buttons = ['Up','Down','Left','Right','A','B','X','Y','LB','RB','L3','R3','View','Menu','L4','R4','Home'];
  buttons.forEach(name => {
    const b=document.createElement('button'); b.className='sec small'; b.textContent=name; b.dataset.labButton=name;
    b.setAttribute('aria-pressed','false');
    labBindButton(b,name);
    labPanel.querySelector('.sim-buttons').append(b);
  });
  ['lx','ly','rx','ry','lt','rt'].forEach(key => {
    const label=document.createElement('label'), slider=document.createElement('input');
    label.textContent=key.toUpperCase(); slider.type='range'; slider.min=key==='lt'||key==='rt'?'0':'-1'; slider.max='1'; slider.step='.01'; slider.value='0';
    slider.dataset.labAxis=key; slider.setAttribute('aria-label','Virtual '+key.toUpperCase());
    slider.oninput=()=>labSend({action:'axis',axis:key,value:+slider.value}).catch(()=>{});
    label.append(slider); labPanel.querySelector('.sim-axes').append(label);
  });
  labPoll(); setInterval(labPoll,70);
}
