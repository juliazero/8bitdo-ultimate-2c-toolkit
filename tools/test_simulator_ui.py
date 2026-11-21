# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional GUI regression: synthetic Settings A->B save, input and Live View."""
import json
import shutil
from unittest.mock import patch
from pathlib import Path
import sys
import tempfile
import threading
import time
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import webview
from u2c_customfw import cli, state
from u2c_customfw.firmware import image, recipes
from u2c_customfw.desktop import Session
from u2c_customfw.web import server
from u2c_customfw.simulation import installed


def main():
    with tempfile.TemporaryDirectory() as tmp, installed(Path(tmp)) as world:
        stock = cli.fetch.fetch_stock()
        world.controller[image.BANK_B:image.BANK_B + image.BANK_A_SIZE] = stock
        built = recipes.build(stock, ['settings', 'ring_normal'])
        world.controller[image.BASE:image.BASE + len(built)] = built
        state.accept(Path(tmp))
        assets = Path(tmp) / "static"
        shutil.copytree(server.STATIC, assets)
        html = assets / "index.html"
        html.write_text(html.read_text(encoding="utf-8").replace("<head>", "<head><script>window.__uiErrors=[];addEventListener('error',e=>window.__uiErrors.push({message:e.message,source:e.filename||e.target.src,stack:e.error?.stack}),true);</script>"), encoding="utf-8")
        asset_patch = patch.object(server, "STATIC", assets)
        asset_patch.start()
        session = Session(Path(tmp))
        window = webview.create_window('Simulator regression', session.url, hidden=True)
        result, lock = {}, threading.Lock()
        def wait(condition):
            for _ in range(400):
                if condition(): return
                time.sleep(.05)
            raise AssertionError('UI state timeout')
        def check():
            if not lock.acquire(False): return
            stage = 'load'
            try:
                wait(lambda: 'with tweaks' in (window.evaluate_js("document.getElementById('fw-title')?.textContent") or ''))
                window.evaluate_js("Object.defineProperty(document,'hidden',{configurable:true,get:()=>false})")
                assert window.evaluate_js("document.getElementById('restore-backup').disabled")
                assert window.evaluate_js("document.getElementById('restore-stock').disabled")
                stage = 'settings'
                window.evaluate_js("showTab('settings')")
                wait(lambda: window.evaluate_js("!!document.querySelector('select[data-field=remap_13]') && !settingsBusy && !!settingsData?.available"))
                stage = 'ring save'
                window.evaluate_js("document.querySelector('[data-live=\"1\"]').click()")
                wait(lambda: world.ring_state()['live'] == 1)
                window.evaluate_js("document.getElementById('ring-save').click()")
                wait(lambda: world.ring_state()['startup'] == 1)
                wait(lambda: window.evaluate_js("!reading && !settingsBusy && !!settingsData?.available"))
                stage = 'remap'
                window.evaluate_js("const pick=document.querySelector('select[data-field=remap_13]');pick.value='12';pick.dispatchEvent(new Event('change',{bubbles:true}));")
                wait(lambda: window.evaluate_js("!settingsBusy && settingsDirty"))
                stage = 'save'
                window.evaluate_js("document.getElementById('settings-save').click()")
                wait(lambda: window.evaluate_js("!settingsBusy && !settingsDirty && settingsMessage.startsWith('Saved.')"))
                stage = 'live'
                window.evaluate_js("(b=>{labLatched.add(b.dataset.labButton);labButtonSend(b.dataset.labButton,true)})(document.querySelector('[data-lab-button=A]'));const t=document.querySelector('input[aria-label=\"Virtual LT\"]');t.value='.5';t.dispatchEvent(new Event('input',{bubbles:true}));const x=document.querySelector('input[aria-label=\"Virtual LX\"]');x.value='.8';x.dispatchEvent(new Event('input',{bubbles:true}));")
                wait(lambda: window.evaluate_js("document.getElementById('live-lt').style.width") == '50%')
                wait(lambda: window.evaluate_js("document.getElementById('live-left').style.transform") not in ('', 'translate(0px, 0px)'))
                transform = window.evaluate_js("document.getElementById('live-left').style.transform")
                assert transform and 'translate(0px' not in transform, transform
                stage = 'diagnostics'
                window.evaluate_js("showTab('diagnostics')")
                wait(lambda: window.evaluate_js("!!testState?.pressed.includes('B') && !testState.pressed.includes('A')"))
                reported = world.virtual_state()['input']['pressed']
                window.evaluate_js("document.querySelector('[data-lab=release]').click()")
                wait(lambda: not world.pressed and world.axes['lt'] == 0)
                stage = 'battery values'
                window.evaluate_js("const p=document.querySelector('[data-lab=battery-percent]');p.value='15';p.dispatchEvent(new Event('change'));const v=document.querySelector('[data-lab=battery-mv]');v.value='3300';v.dispatchEvent(new Event('change'));")
                wait(lambda: world.battery_percent == 15 and world.battery_mv == 3300)
                wait(lambda: window.evaluate_js('testState?.battery?.level') == .15)
                assert window.evaluate_js('testState?.battery?.voltage_mv') == 3300
                stage = 'five-second ring chord'
                old_level = world.ring_state()['live']
                window.evaluate_js("(b=>{labLatched.add(b.dataset.labButton);labButtonSend(b.dataset.labButton,true)})(document.querySelector('[data-lab-button=L4]'));(b=>{labLatched.add(b.dataset.labButton);labButtonSend(b.dataset.labButton,true)})(document.querySelector('[data-lab-button=R4]'))")
                wait(lambda: world.ring_chord_since is not None)
                started = time.monotonic()
                time.sleep(.2)
                assert world.ring_state()['live'] == old_level
                wait(lambda: world.ring_state()['live'] == (old_level + 1) % 6)
                assert time.monotonic()-started >= 4.8
                window.evaluate_js("document.querySelector('[data-lab=release]').click()")
                wait(lambda: not world.pressed)
                stage = 'RF connection controls'
                def select(device, connection):
                    # Real change events fire only when the value changes. Drain
                    # each queued mutation before changing the dependent select.
                    window.evaluate_js("window.__labSelectDone=false;(async()=>{await labQueue;const d=document.querySelector('[data-lab=device]');if(d.value!=="+json.dumps(device)+"){d.value="+json.dumps(device)+";d.dispatchEvent(new Event('change'));await labQueue;}const c=document.querySelector('[data-lab=connection]');c.value="+json.dumps(connection)+";c.dispatchEvent(new Event('change'));await labQueue;window.__labSelectDone=true;})()")
                    wait(lambda:window.evaluate_js('window.__labSelectDone'))
                    wait(lambda: world.selected_device == device and world.selected_connection == connection)
                    wait(lambda: window.evaluate_js('!reading'))
                def cycle(button, expected):
                    window.evaluate_js("document.querySelector('[data-lab=release]').click()")
                    wait(lambda: not world.pressed)
                    window.evaluate_js("(b=>{labLatched.add(b.dataset.labButton);labButtonSend(b.dataset.labButton,true)})(document.querySelector('[data-lab-button=" + button + "]'))")
                    wait(lambda: button in world.pressed)
                    window.evaluate_js("document.querySelector('[data-lab=power]').click()")
                    wait(lambda: world.profile == 'dongle-idle')
                    wait(lambda: window.evaluate_js('!reading'))
                    assert button in world.pressed
                    window.evaluate_js("(b=>{labLatched.add(b.dataset.labButton);labButtonSend(b.dataset.labButton,true)})(document.querySelector('[data-lab-button=Home]'))")
                    wait(lambda: world.profile == expected)
                    wait(lambda: window.evaluate_js("testState?.category") == world.virtual_state()['input']['category'])
                select('controller', 'dongle')
                cycle('B', 'dongle-xinput')
                select('dongle', 'usb')
                # Real CLI installation is covered in test_simulation; seed its
                # exact patch bytes here to focus the native UI regression.
                from u2c_customfw.ops import dongle
                for address, original, replacement in dongle.TWEAKS['dongle_dinput'][1]:
                    world.receiver[address:address + len(replacement)] = replacement
                assert world._dongle_dinput()
                select('controller', 'dongle')
                cycle('B', 'dongle-dinput')
                cycle('X', 'dongle-xinput')
                stage = 'dongle USB unplug/replug'
                window.evaluate_js("document.querySelector('[data-lab=dongle-usb]').click()")
                wait(lambda: not world.dongle_attached)
                assert not world.virtual_state()['input']['connected']
                window.evaluate_js("document.querySelector('[data-lab=dongle-usb]').click()")
                wait(lambda: world.dongle_attached and world.virtual_state()['input']['connected'])
                stage = 'install USB switching via WebUI API'
                select('controller', 'usb')
                window.evaluate_js("runJob('Backing up…','/api/backup',{},'Backup saved')")
                wait(lambda: bool(list(Path(tmp).glob('controller-20*.bin'))) and window.evaluate_js('!busy && !reading && !refreshing'))
                window.evaluate_js("runJob('Installing…','/api/patch',{write:true,base:'backup',tweaks:['ring_normal','settings','usb_dinput','bt_xbox','bt_modes','bt_rumble','bt_rumble_long']},'Installed')")
                wait(lambda: 'usb_dinput' in world._controller_keys() and window.evaluate_js('!busy && !reading && !refreshing'))
                assert not window.evaluate_js('window.__installError || null')
                window.evaluate_js("refresh(); showTab('diagnostics')")
                wait(lambda: window.evaluate_js('!reading'))
                for button, expected in (('B','D-input'), ('X','XInput')):
                    stage = 'USB ' + expected + ' quiet'
                    wait(lambda: world.usb_last_vendor is None or time.monotonic()-world.usb_last_vendor >= 2.1)
                    window.evaluate_js("document.querySelector('[data-lab=release]').click()")
                    wait(lambda: not world.pressed)
                    window.evaluate_js("(b=>{labLatched.add(b.dataset.labButton);labButtonSend(b.dataset.labButton,true)})(document.querySelector('[data-lab-button=" + button + "]'));(b=>{labLatched.add(b.dataset.labButton);labButtonSend(b.dataset.labButton,true)})(document.querySelector('[data-lab-button=Home]'))")
                    stage = 'USB ' + expected + ' hold'
                    wait(lambda: world.usb_dinput == (expected == 'D-input'))
                    stage = 'USB ' + expected + ' diagnostics'
                    wait(lambda: window.evaluate_js('testState?.category') == 'USB · ' + expected)
                window.evaluate_js("document.querySelector('[data-lab=release]').click()")
                wait(lambda: not world.pressed)
                stage = 'USB disconnect and rear switch'
                for switch in ('dongle', 'bluetooth'):
                    select('controller', 'usb')
                    wait(lambda: window.evaluate_js("document.querySelector('[data-lab=power]').textContent") == 'Disconnect USB')
                    assert not window.evaluate_js("document.querySelector('[data-lab=power]').disabled")
                    window.evaluate_js("const r=document.querySelector('[data-lab=rear-switch]');r.value=" + json.dumps(switch) + ";r.dispatchEvent(new Event('change'));")
                    wait(lambda: world.rear_switch == switch)
                    assert world.profile == 'controller-usb'
                    started = time.monotonic()
                    window.evaluate_js("document.querySelector('[data-lab=power]').click()")
                    wait(lambda: world.reconnect_at is not None)
                    assert not world.virtual_state()['input']['connected']
                    wait(lambda: world.reconnect_at is None and world.virtual_state()['input']['connected'])
                    assert time.monotonic() - started >= 1.9
                    assert world.connection == switch
                    wait(lambda: window.evaluate_js("document.querySelector('[data-lab=connection]').value") == switch)
                stage = 'Bluetooth startup modes'
                select('controller','bluetooth')
                for modifier,expected in (('B','D-input'),(None,'D-input'),('X','XInput'),(None,'XInput')):
                    window.evaluate_js("document.querySelector('[data-lab=release]').click()")
                    wait(lambda: not world.pressed)
                    window.evaluate_js("document.querySelector('[data-lab=power]').click()")
                    wait(lambda: not world.connected and window.evaluate_js('!reading'))
                    if modifier:
                        window.evaluate_js("labLatched.add('"+modifier+"');labButtonSend('"+modifier+"',true)")
                        wait(lambda: modifier in world.pressed)
                    window.evaluate_js("labLatched.add('Home');labButtonSend('Home',true)")
                    wait(lambda: world.virtual_state()['input']['category']=='Bluetooth · '+expected)
                    wait(lambda: window.evaluate_js('testState?.category')=='Bluetooth · '+expected)
                    wait(lambda: window.evaluate_js('!reading'))
                for connection,prefix in (('bluetooth','Bluetooth'),('dongle','2.4 GHz dongle')):
                    select('controller',connection)
                    for modifier,expected in (('B','D-input'),('X','XInput')):
                        stage='live '+connection+' '+expected
                        window.evaluate_js("document.querySelector('[data-lab=release]').click()")
                        wait(lambda:not world.pressed)
                        window.evaluate_js("labLatched.add('"+modifier+"');labButtonSend('"+modifier+"',true);labLatched.add('Home');labButtonSend('Home',true)")
                        stage='live '+connection+' '+expected+' restart'
                        wait(lambda:world.reconnect_at is not None)
                        stage='live '+connection+' '+expected+' reconnect'
                        wait(lambda:world.reconnect_at is None and world.connected)
                        stage='live '+connection+' '+expected+' diagnostics'
                        window.evaluate_js("showTab('diagnostics')")
                        wait(lambda:window.evaluate_js('testState?.category')==prefix+' · '+expected)
                        wait(lambda:window.evaluate_js('document.querySelector("#test-seen") !== null && (document.querySelector("#test-seen [data-c=L4]") !== null) === '+str(expected=='D-input').lower()))
                        if expected=='D-input':
                            window.evaluate_js("document.querySelector('[data-lab=release]').click()")
                            wait(lambda:not world.pressed)
                            for paddle in ('L4','R4'):
                                window.evaluate_js("labLatched.add('"+paddle+"');labButtonSend('"+paddle+"',true)")
                                wait(lambda:window.evaluate_js("testSeen.has('"+paddle+"')"))
                                window.evaluate_js("document.querySelector('[data-lab=release]').click()")
                                wait(lambda:not world.pressed)

                select('controller', 'update')
                assert window.evaluate_js("document.querySelector('[data-lab=connection] option:checked').textContent") == 'USB - Update mode'
                assert window.evaluate_js("document.querySelector('[data-lab=power]').textContent") == 'Disconnect USB'
                assert window.evaluate_js("document.querySelector('[data-lab=startup]') === null")
                window.evaluate_js("document.querySelector('[data-lab=release]').click()")
                wait(lambda: not world.pressed)
                result.update(ok=True, live_wireless_switch=['Bluetooth D/X','2.4 GHz D/X'], reported_before_release=reported, released=not world.pressed,
                    ring_startup=world.ring_state()['startup'], live_lt='50%', stick=transform, rf_modes=['XInput without tweak','D-input with B','XInput with X'], usb_disconnect_modes=['dongle','bluetooth'], empty_initial_backups=True, experimental_removed=True, usb_patch_modes=['D-input','XInput'], dongle_unplug=True, battery=[15,3300], ring_hold_seconds=5, bluetooth_modes=['D-input','remember D-input','XInput','remember XInput'])
            except Exception as exc:
                result.update(ok=False, stage=stage, error=str(exc), job_results=list(session.server.runner.results.values()), virtual=world.virtual_state(), gesture=world.usb_gesture, fired=world.usb_fired, last_vendor=world.usb_last_vendor)
                try:
                    result['debug'] = window.evaluate_js("JSON.stringify({tab,visible:testVisible,category:testState?.category,errors:window.__uiErrors, ready:document.readyState, title:document.getElementById('fw-title')?.textContent,resources:performance.getEntriesByType('resource').slice(-5).map(e=>({host:new URL(e.name).host,path:new URL(e.name).pathname,size:e.decodedBodySize}))})")
                except Exception: pass
            finally:
                window.destroy()
        window.events.loaded += check
        timer = threading.Timer(150, window.destroy)
        timer.daemon = True
        timer.start()
        try:
            webview.start(gui='edgechromium' if sys.platform == 'win32' else 'qt', private_mode=True, storage_path=str(Path(tmp) / 'webview'))
        finally:
            timer.cancel()
            session.close()
            asset_patch.stop()
        print(json.dumps(result))
        if not result.get('ok'): raise SystemExit(1)


if __name__ == '__main__':
    main()
