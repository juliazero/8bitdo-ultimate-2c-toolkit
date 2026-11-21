// SPDX-License-Identifier: GPL-3.0-or-later
// Independent of the application: never disguise failed initialization as no pad.
window.u2cStartupErrors = [];
window.u2cStartupReady = false;
window.u2cStartupFailure = function(message) {
  window.u2cStartupErrors.push(String(message));
  const box=document.getElementById('startup-status');
  if (!box) return;
  box.hidden=false;
  document.getElementById('startup-message').textContent='The interface could not start completely. Reload the interface or include this message in a problem report.';
  document.getElementById('startup-error').textContent=window.u2cStartupErrors.join('\n');
};
addEventListener('error', event=>{
  if (event.filename && new URL(event.filename,location.href).origin!==location.origin) return;
  const message=event.message || (event.target?.tagName==='SCRIPT' ? 'A required interface script could not load.' : null);
  if (message) window.u2cStartupFailure(message);
},true);
window.u2cStartupComplete=function() {
  const required=['strip-settings','strip-diagnostics'];
  if (typeof settingsRender!=='function' || typeof diagnosticsRender!=='function' ||
      typeof simulationRender!=='function' || required.some(id=>typeof document.getElementById(id)?.onclick!=='function')) {
    window.u2cStartupFailure('Interface initialization is incomplete.'); return;
  }
  if (window.u2cStartupErrors.length) return;
  window.u2cStartupReady=true;
  document.querySelectorAll('[role=tab]').forEach(button=>button.disabled=false);
  document.getElementById('startup-status').hidden=true;
};
addEventListener('DOMContentLoaded',()=>{
  if (!window.u2cStartupReady && !window.u2cStartupErrors.length) window.u2cStartupFailure('Interface initialization did not finish.');
},{once:true});
