# SPDX-License-Identifier: GPL-3.0-or-later
"""Hidden native UI check for the normal Y tweak dependencies; synthetic device only."""
import json,tempfile,time
from pathlib import Path
import webview
from u2c_customfw.desktop import Session
from u2c_customfw.simulation import installed

def main():
    result={}
    with tempfile.TemporaryDirectory() as tmp,installed(Path(tmp)):
        session=Session(Path(tmp))
        window=webview.create_window('Y tweak check',session.url,hidden=True)
        def check():
            try:
                for _ in range(300):
                    if window.evaluate_js("!!fw && !reading && !!document.querySelector('[value=bt_switch2]') && !document.querySelector('[value=bt_switch2]').disabled"):
                        break
                    time.sleep(.1)
                else:raise AssertionError('Y checkbox not ready')
                value=window.evaluate_js("""(()=>{
                  const boxes=tweakBoxes(), get=k=>boxes.find(x=>x.value===k);
                  get('bt_xbox').checked=false;render();
                  if (get('bt_switch2_force')) throw new Error('Removed diagnostic option is visible');
                  get('bt_switch2').click();
                  const selected=tweaks();
                  const dependencies=['bt_switch2','bt_xbox','bt_modes','dim_leds','settings','cpu_rest','ring_normal'];
                  const enabled=dependencies.every(k=>selected.includes(k));
                  get('settings').click();
                  return JSON.stringify({enabled,cleared:!get('bt_switch2').checked,selected});
                })()""")
                result.update(json.loads(value))
                assert result['enabled'] and result['cleared'],result
                result['ok']=True
            except Exception as exc:result.update(ok=False,error=str(exc))
            finally:window.destroy()
        window.events.loaded+=check
        try:webview.start(gui='edgechromium',private_mode=True,storage_path=str(Path(tmp)/'webview'))
        finally:session.close()
    print(json.dumps(result,indent=2))
    if not result.get('ok'):raise SystemExit(1)
if __name__=='__main__':main()
