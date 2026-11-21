const assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const source=fs.readFileSync('u2c_customfw/web/static/startup.js','utf8');
function make(ready) {
  const ids=new Map(), events={};
  for(const name of ['startup-status','startup-message','startup-error','strip-settings','strip-diagnostics']) ids.set(name,{hidden:false,disabled:true,onclick:ready?()=>{}:null,textContent:''});
  const context=vm.createContext({window:{},URL,location:{origin:'http://127.0.0.1',href:'http://127.0.0.1/'},
    document:{getElementById:id=>ids.get(id),querySelectorAll:()=>[ids.get('strip-settings'),ids.get('strip-diagnostics')]},
    addEventListener:(name,fn)=>events[name]=fn});
  if(ready) Object.assign(context,{settingsRender(){},diagnosticsRender(){},simulationRender(){}});
  vm.runInContext(source,context);return{context,ids,events};
}
let t=make(true);t.context.window.u2cStartupComplete();
assert.equal(t.context.window.u2cStartupReady,true);assert.equal(t.ids.get('startup-status').hidden,true);assert.equal(t.ids.get('strip-settings').disabled,false);
t=make(false);t.context.window.u2cStartupComplete();
assert.equal(t.context.window.u2cStartupReady,false);assert.equal(t.ids.get('startup-status').hidden,false);assert.equal(t.ids.get('strip-settings').disabled,true);assert.match(t.ids.get('startup-error').textContent,/incomplete/);
t=make(true);t.events.error({filename:'http://127.0.0.1/',message:'Injected startup error'});t.context.window.u2cStartupComplete();
assert.equal(t.context.window.u2cStartupReady,false);assert.match(t.ids.get('startup-error').textContent,/Injected startup error/);
console.log('Complete startup and visible initialization errors: passed');
