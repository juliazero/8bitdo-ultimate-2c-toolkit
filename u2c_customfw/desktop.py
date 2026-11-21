# SPDX-License-Identifier: GPL-3.0-or-later
"""Desktop application with an embedded WebUI (WebView2 on Windows, Qt on Linux)."""
import argparse
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from urllib.request import urlopen

from u2c_customfw import __version__, disclaimer, state
from u2c_customfw.web.server import STATIC, make_server


class Session:
    def __init__(self, store):
        self.server, token = make_server(store)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/?token={token}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        runner = self.server.runner
        runner.request_shutdown()
        with runner.cond:
            runner.cond.wait_for(lambda: not runner.running)
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()


def smoke_test(destination):
    """Exercise the frozen resources and local HTTP server without touching hardware."""
    if getattr(sys, "frozen", False):
        assert (Path(sys._MEIPASS) / "DISCLAIMER.md").is_file(), "Missing disclaimer"
    from u2c_customfw.firmware import recipes
    import u2c_customfw.transport
    assert (STATIC / "index.html").is_file()
    blobs = Path(recipes.__file__).parent / "blobs"
    assert list(blobs.glob("*.bin")), "Missing firmware resources"
    assert "warranty" in disclaimer.text().lower()
    if sys.platform == "win32":
        import hid
    elif sys.platform == "linux":
        import evdev
        import usb.backend.libusb1
        assert usb.backend.libusb1.get_backend() is not None, "Missing libusb runtime"
    with tempfile.TemporaryDirectory() as tmp:
        session = Session(Path(tmp))
        try:
            with urlopen(session.url, timeout=5) as response:
                assert response.status == 200
                # Only a prefix is needed here; the native UI smoke test checks the full page.
                assert b"<html" in response.read(1024).lower()
        finally:
            session.close()
    Path(destination).write_text(json.dumps({"ok": True, "version": __version__, "platform": sys.platform,
                                            "embedded": True}), encoding="utf-8")


class WindowLifetime:
    """Keep the window and process alive until operations finish, without blocking its UI thread."""
    def __init__(self, session, window):
        self.session, self.window = session, window
        self.requested = False
        self.finished = threading.Event()
        self.lock = threading.Lock()

    def closing(self):
        with self.lock:
            if self.finished.is_set():
                return True
            if self.requested:
                return False
            self.requested = True
            self.session.server.runner.request_shutdown()
        self.window.set_title("Firmware Toolkit — finishing current operation…")
        threading.Thread(target=self._finish, daemon=True).start()
        return False

    def _finish(self):
        self.session.close()
        self.finished.set()
        self.window.destroy()


def run_window(store, smoke_report=None, expected_profile=None):
    import webview
    webview.settings["ALLOW_DOWNLOADS"] = True
    if sys.platform.startswith("linux"):
        os.environ["QT_API"] = "pyside6"
    session = Session(store)
    result = {}
    window = webview.create_window("Firmware Toolkit — Simulation" if os.environ.get("U2C_SIM") and not smoke_report else "Firmware Toolkit", session.url, width=1144, height=780,
                                   min_size=(760, 720), text_select=True, hidden=bool(smoke_report))
    lifetime = WindowLifetime(session, window)
    window.events.closing += lifetime.closing
    watchdog = None
    if smoke_report:
        check_lock = threading.Lock()
        def check():
            if not check_lock.acquire(blocking=False):
                return
            try:
                import time
                for _ in range(200):
                    version = window.evaluate_js("document.getElementById('version')?.textContent || ''")
                    if version == "v" + __version__:
                        break
                    time.sleep(0.1)
                assert version == "v" + __version__, "WebUI JavaScript did not load its state"
                value = window.evaluate_js("JSON.stringify({title:document.title, tabs:document.querySelectorAll('[role=tab]').length, gamepads:typeof navigator.getGamepads, secure:window.isSecureContext, agent:navigator.userAgent, startup:window.u2cStartupReady===true, startupErrors:window.u2cStartupErrors||[], tabsReady:['strip-settings','strip-diagnostics'].every(id=>typeof document.getElementById(id)?.onclick==='function'&&!document.getElementById(id).disabled)})")
                value = json.loads(value)
                assert value["title"] == "U2C Firmware Toolkit", value
                assert value["tabs"] == 3, value
                assert value["gamepads"] == "function", value
                assert value["secure"], value
                assert value["startup"] and value["tabsReady"] and not value["startupErrors"], value
                if expected_profile:
                    expected = {"dongle-idle": "dongle", "dongle-xinput": "dongle_paired",
                        "dongle-dinput": "dongle_paired", "dongle-boot": "boot",
                        "controller-boot": "boot", "controller-usb": "cable",
                        "controller-bluetooth": "bluetooth", "controller-bluetooth-xinput": "bluetooth_xbox"}[expected_profile]
                    for _ in range(150):
                        identity = json.loads(window.evaluate_js("JSON.stringify({modes:st.devices.map(d=>d.mode), firmware:fw, title:document.getElementById('fw-title').textContent, badge:document.getElementById('badge-text').textContent})"))
                        if identity["firmware"] and not identity["firmware"].get("error"):
                            break
                        time.sleep(.1)
                    assert expected in identity["modes"], identity
                    assert identity["firmware"] and not identity["firmware"].get("error"), identity
                    if expected_profile.startswith("dongle"):
                        assert identity["firmware"].get("device") == "dongle", identity
                    assert "No controller found" not in identity["title"], identity
                    value["simulation"] = identity
                result.update(ok=True, version=__version__, webview=value)
            except Exception as exc:
                result.update(ok=False, error=str(exc))
            finally:
                window.destroy()
        def timeout():
            result.update(ok=False, error="Embedded WebUI load timed out")
            window.destroy()
        window.events.loaded += check
        watchdog = threading.Timer(35, timeout)
        watchdog.daemon = True
        watchdog.start()
    icon = Path(getattr(sys, "_MEIPASS", Path(__file__).parent.parent)) / ("desktop-icon.ico" if sys.platform == "win32" else "desktop-icon.png")
    try:
        webview.start(gui="edgechromium" if sys.platform == "win32" else "qt",
                      private_mode=True, storage_path=str(store / "webview"),
                      icon=str(icon) if icon.is_file() else None)
    finally:
        if watchdog:
            watchdog.cancel()
        session.close()
    if smoke_report:
        assert result.get("ok"), result
        Path(smoke_report).write_text(json.dumps(result), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", type=Path)
    parser.add_argument("--smoke-ui", action="store_true")
    from u2c_customfw.simulation import PROFILES
    parser.add_argument("--simulator", action="store_true", help="Open the virtual controller laboratory")
    parser.add_argument("--simulate", choices=PROFILES, help="Run an isolated synthetic firmware laboratory")
    parser.add_argument("--disconnect-after", type=int, help="Simulation: disconnect after N protocol requests")
    args = parser.parse_args()
    if args.simulator and not args.simulate:
        args.simulate = "controller-usb"
    if args.smoke_test:
        smoke_test(args.smoke_test)
        if args.smoke_ui:
            from u2c_customfw import sim
            with tempfile.TemporaryDirectory() as tmp:
                store = Path(tmp)
                if args.simulate:
                    from u2c_customfw.simulation import installed
                    with installed(store, args.simulate, args.disconnect_after):
                        state.accept(store)
                        run_window(store, args.smoke_test, args.simulate)
                else:
                    os.environ["U2C_SIM"] = "1"
                    sim.install(store)
                    state.accept(store)
                    run_window(store, args.smoke_test)
        return
    if args.simulate:
        from u2c_customfw.simulation import installed
        with tempfile.TemporaryDirectory(prefix="u2c-simulation-") as tmp:
            store = Path(tmp)
            with installed(store, args.simulate, args.disconnect_after,
                           preferences_path=state.default_store() / "simulator-settings.json"):
                state.accept(store)
                run_window(store)
        return
    run_window(state.default_store())


if __name__ == "__main__":
    if "--smoke-test" in sys.argv:
        try:
            main()
        except Exception as exc:
            import traceback
            destination = Path(sys.argv[sys.argv.index("--smoke-test") + 1])
            destination.write_text(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}",
                                               "traceback": traceback.format_exc()}), encoding="utf-8")
            raise SystemExit(1)
    else:
        main()
