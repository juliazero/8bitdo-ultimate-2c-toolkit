# SPDX-License-Identifier: GPL-3.0-or-later
"""HTTP server bound to 127.0.0.1 on a random port. API calls need the session token in X-Token; the Host header must
be this address (against DNS rebinding)."""
import datetime
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import __version__, disclaimer, settings, state, update
from .. import cli
from ..firmware import recipes
from ..ops import backup as bkp, battery as battery_ops, battlog, dongle, probe, report as report_mod, shown, update_available
from ..ops.devices import XINPUT_NOTE, Found
from ..ops import settings as settings_ops
from ..ops.plan import OpError
from ..protocol import ProtocolError, app
from ..transport import PermissionDenied
from . import jobs
from .device_events import DeviceEvents
from .linux_gamepad import GAMEPAD
from .windows_rumble import RUMBLE as WINDOWS_RUMBLE
from .hid_rumble import RUMBLE as HID_RUMBLE
from .windows_battery import snapshot as windows_battery
from .windows_stock_gamepad import GAMEPAD as WINDOWS_STOCK_GAMEPAD

def read_controller_details(f, store):
    """UI-only reads: recover a transient Windows D-input handle once, never replay flash jobs."""
    for attempt in range(2):
        try:
            ch=cli.open_channel(f.info,0 if f.mode=='boot' else 0x81)
            try:
                return probe.read_firmware(ch,f.mode), cli.unit_backups(ch,store,f.mode,whole=False)
            finally:ch.close()
        except probe.PairedDongle:raise
        except (OpError,OSError,ProtocolError):
            if attempt or sys.platform!='win32' or f.mode!='cable_dinput':raise
            time.sleep(.1)


STATIC = Path(__file__).parent / "static"
SETTINGS_MISSING = "Controller settings isn't installed. Install it in the Firmware tab to change the sticks, triggers, buttons and vibration."
SIMULATION_WORLD = None
KEEPALIVE_S = 15
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css"}


def open_store(store):
    folder = Path(store).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(str(folder))
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def page_html():
    """One self-contained document, preserving parser/DOMContentLoaded ordering.

    A dropped stylesheet/script request must not leave only some app modules
    initialized. Individual source assets remain available for debugging.
    """
    page = (STATIC / "index.html").read_bytes()
    css = (STATIC / "style.css").read_bytes()
    page = page.replace(b'<link rel="stylesheet" href="/static/style.css">', b'<style>' + css + b'</style>')
    for name in ('startup.js','diagnostics.js','settings.js','simulation.js','app.js'):
        script = (STATIC / name).read_bytes()
        script = re.sub(br'</script', lambda m: b'<\\/script', script, flags=re.I)
        tag = ('<script src="/static/' + name + '"></script>').encode()
        page = page.replace(tag, b'<script>\n' + script + b'\n</script>')
    return page


class LocalHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = sys.platform != "win32"

    def server_close(self):
        if hasattr(self, "device_events"):
            self.device_events.close()
        super().server_close()

    def server_bind(self):
        if sys.platform == "win32":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def shutdown_request(self, request):
        if sys.platform != "win32":
            return super().shutdown_request(request)
        # Winsock can reset the peer if the socket is closed immediately after
        # sending. Half-close, then briefly let the peer finish receiving/close.
        try:
            request.shutdown(socket.SHUT_WR)
            deadline = time.monotonic() + 2.0
            while (remaining := deadline - time.monotonic()) > 0:
                request.settimeout(remaining)
                if not request.recv(4096):
                    break
        except OSError:
            pass
        finally:
            self.close_request(request)


def make_server(store: Path, port: int = 0):
    token = secrets.token_urlsafe(24)
    runner = jobs.Runner(store)
    checked = {}   # GitHub is asked once per launch

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        disable_nagle_algorithm = True   # Small input reports must not wait for TCP batching.

        def handle(self):
            try:
                super().handle()
            except (ConnectionResetError, BrokenPipeError):
                self.close_connection = True  # client closed a persistent connection

        def log_message(self, *a):
            pass

        def send(self, code, body, ctype="application/json"):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            # Keep HTTP/1.1 browser connections alive. Explicit close is needed
            # for rejected requests whose body may not have been consumed.
            if self.close_connection or code >= 400:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            self.wfile.write(data)

        def host_ok(self):
            return self.headers.get("Host", "") in (f"127.0.0.1:{self.server.server_address[1]}", f"localhost:{self.server.server_address[1]}")

        def auth(self):
            given = self.headers.get("X-Token") or parse_qs(urlparse(self.path).query).get("token", [""])[0]
            if not self.host_ok() or not secrets.compare_digest(given, token):
                self.send(403, json.dumps({"error": "forbidden"}))
                return False
            return True

        def body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        def do_GET(self):
            if not self.host_ok():
                return self.send(403, "forbidden", "text/plain")
            path = self.path.split("?")[0]
            route = {"/api/simulation": self.api_simulation, "/api/windows-stock-stream": self.api_windows_stock_stream, "/api/windows-stock-gamepad": self.api_windows_stock_gamepad, "/api/windows-battery": self.api_windows_battery, "/api/battery": self.api_battery, "/api/state": self.api_state, "/api/device-events": self.api_device_events, "/api/events": self.api_events, "/api/firmware": self.api_firmware,
                     "/api/settings": self.api_settings_read, "/api/linux-gamepad": self.api_linux_gamepad, "/api/busy": self.api_busy, "/api/update": self.api_update, "/api/job": self.api_job, "/api/windows-rumble": self.api_windows_rumble}.get(path)
            if route:
                return route()
            if path in ("/", "/index.html", "/static/index.html"):
                return self.send(200, page_html(), TYPES[".html"])
            name = "index.html" if path == "/" else path.lstrip("/").removeprefix("static/")
            f = (STATIC / name).resolve()
            if f.parent == STATIC.resolve() and f.is_file():
                return self.send(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"))
            self.send(404, "not found", "text/plain")

        def api_windows_stock_stream(self):
            if not self.auth():
                return
            if sys.platform != "win32":
                return self.send(404, json.dumps({"error": "Windows only"}))
            self.protocol_version = "HTTP/1.1"
            self.connection.settimeout(2)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            deadline = time.monotonic() + 30
            try:
                while time.monotonic() < deadline and not runner.running and not runner.closing:
                    state = WINDOWS_STOCK_GAMEPAD.snapshot()
                    payload = ("data: " + json.dumps(state) + "\n\n").encode()
                    self.wfile.write(f"{len(payload):x}\r\n".encode() + payload + b"\r\n")
                    self.wfile.flush()
                    if state.get("connected") is False:
                        break
                    time.sleep(0.016)
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except OSError:
                pass   # Browser left diagnostics.
            finally:
                WINDOWS_STOCK_GAMEPAD.close()

        def api_windows_stock_gamepad(self):
            if not self.auth():
                return
            if sys.platform != "win32":
                return self.send(404, json.dumps({"error": "Windows only"}))
            try:
                self.send(200, json.dumps(WINDOWS_STOCK_GAMEPAD.snapshot()))
            finally:
                WINDOWS_STOCK_GAMEPAD.close()

        def api_windows_battery(self):
            if not self.auth():
                return
            if sys.platform != "win32":
                return self.send(404, json.dumps({"error": "Windows only"}))
            self.send(200, json.dumps(windows_battery()))

        def api_windows_rumble(self):
            if not self.auth():
                return
            if sys.platform != "win32":
                return self.send(404, json.dumps({"error": "Windows only"}))
            status = HID_RUMBLE.status()
            self.send(200, json.dumps(status if status['available'] else WINDOWS_RUMBLE.status()))

        def api_linux_gamepad(self):
            if not self.auth():
                return
            if sys.platform != "linux":
                return self.send(404, json.dumps({"error": "Linux only"}))
            self.send(200, json.dumps(GAMEPAD.snapshot()))

        def api_simulation(self, req=None):
            if not self.auth(): return
            if SIMULATION_WORLD is None:
                return self.send(404, json.dumps({"error": "Simulation is not running"}))
            if req is not None and runner.running and req.get("action") in ("profile", "connection", "power", "disconnect_usb", "rear_switch", "dongle_usb"):
                return self.send(409, json.dumps({"error": "Wait for the current operation to finish"}))
            try:
                data = SIMULATION_WORLD.control(req) if req is not None else SIMULATION_WORLD.virtual_state()
                return self.send(200, json.dumps(data))
            except ValueError as exc:
                return self.send(400, json.dumps({"error": str(exc)}))

        def api_device_events(self):
            if not self.auth():
                return
            try:
                generation = int(parse_qs(urlparse(self.path).query).get("generation", ["-1"])[0])
            except ValueError:
                return self.send(400, json.dumps({"error": "Invalid generation"}))
            self.send(200, json.dumps(self.server.device_events.wait(generation)))

        def api_state(self):
            if not self.auth():
                return
            found = []
            try:
                found = [{"mode": f.mode, "vendor": f.info.vendor, "product": f.info.product, "name": f.info.name}
                         for f in cli.find()]
            except Exception as e:
                found = [{"mode": "error", "name": str(e)}]
            backups = sorted(p.name for p in Path(store).glob("controller-*.bin"))
            self.send(200, json.dumps({"version": __version__, "simulation": bool(os.environ.get("U2C_SIM")), "simulation_controls": SIMULATION_WORLD is not None, "accepted": state.accepted(store), "devices": found,
                                       "backups": backups, "storage_dir": str(Path(store).resolve()),
                                       "disclaimer": disclaimer.text()}))

        def api_battery(self):
            """Read the controller's battery from its own USB firmware command.

            The vendor command is optional: older controller firmware does not answer it.
            If a paired dongle mimics the cable VID/PID in XInput, first identify
            it through the flash signature and read its RF buffer instead.
            Never send the controller battery command to the dongle.
            """
            if not self.auth():
                return
            found = cli.find()
            cable = [f for f in found if f.mode in ("cable", "cable_360", "cable_dinput")]
            if len(cable) == 1:
                if not runner.claim():
                    return self.send(200, json.dumps({"busy": True}))
                try:
                    ch = cli.open_channel(cable[0].info, 0x81)
                    try:
                        # The direct USB controller and the stock-XInput dongle both
                        # advertise 2DC8:310A, often with the *same* bcdDevice.
                        # Do not assume that VID/PID identifies the controller.
                        # The dongle has blank flash at 0x10000; use the existing
                        # read-only identity check before choosing a battery command.
                        try:
                            probe.check_not_dongle(ch)
                        except probe.PairedDongle:
                            # On Windows the dongle's MI_02 vendor HID is accessible
                            # beside XUSB. Reuse this exact channel and the existing
                            # RF telemetry reader. No new command or driver needed.
                            from ..ops.rf_battery import read as rf_battery_read
                            reading = rf_battery_read(ch)
                            return self.send(200, json.dumps({"source": "dongle", **(reading or {"unavailable": True})}))
                        reading = app.battery(ch)
                    finally:
                        ch.close()
                    return self.send(200, json.dumps({"source": "cable", **(reading if reading is not None else {"unavailable": True})}))
                except (OpError, PermissionDenied, OSError, ProtocolError):
                    return self.send(200, json.dumps({"source": "cable", "unavailable": True}))
                finally:
                    runner.release()
            if cable:
                return self.send(200, "{}")
            bt = [f for f in found if f.mode in ("bluetooth", "bluetooth_xbox")]
            if len(bt) == 1:
                pad = bt[0]
                if sys.platform == "win32":
                    data = windows_battery()
                    percent = round(data["level"] * 100) if data.get("state") == "reported" else None
                else:
                    percent = battery_ops.bluetooth_percent(pad.info.name)
                result = {"source": "bluetooth", "name": pad.info.name}
                if percent is not None:
                    result["percent"] = percent
                return self.send(200, json.dumps(result))
            receivers = [f for f in found if f.mode == "dongle_paired"]
            if len(receivers) == 1 and len(found) == 1:
                if not runner.claim():
                    return self.send(200, json.dumps({"busy": True}))
                try:
                    from ..ops.rf_battery import read as rf_battery_read
                    ch = cli.open_channel(receivers[0].info, 0x81)
                    try:
                        reading = rf_battery_read(ch)
                    finally:
                        ch.close()
                    return self.send(200, json.dumps({"source": "dongle", **(reading or {"unavailable": True})}))
                except (OpError, PermissionDenied, OSError, ProtocolError):
                    return self.send(200, json.dumps({"source": "dongle", "unavailable": True}))
                finally:
                    runner.release()
            return self.send(200, "{}")

        def api_battery_log(self):
            """Export the controller's stored battery history over USB, including bootloader mode."""
            if not runner.claim():
                return self.send(409, json.dumps({"error": "Another task is running."}))
            try:
                ours = [f for f in cli.find() if f.mode in ("cable", "cable_360", "cable_dinput", "boot")]
                if len(ours) != 1:
                    return self.send(200, json.dumps({"error": "Connect the controller directly by USB (normal or update mode). Its battery log cannot be exported through Bluetooth or the dongle."}))
                in_boot = ours[0].mode == "boot"
                ch = cli.open_channel(ours[0].info, 0 if in_boot else 0x81)
                try:
                    probe._check_device(ch, ours[0].mode, None)
                    rows = battlog.read(ch, ours[0].mode)
                finally:
                    ch.close()
            except PermissionDenied as e:
                return self.send(200, json.dumps({"error": str(e), "permission": True}))
            except (OpError, OSError, ProtocolError) as e:
                return self.send(200, json.dumps({"error": str(e)}))
            finally:
                runner.release()
            if not rows:
                return self.send(200, json.dumps({"records": 0}))
            path = Path(store) / f"battery-log-{datetime.datetime.now():%Y%m%d-%H%M%S}.csv"
            path.write_text(battlog.csv(rows))
            if sys.platform == "darwin" and not os.environ.get("U2C_SIM"):
                subprocess.run(["/usr/bin/open", "-R", str(path)], check=False)
            return self.send(200, json.dumps({"records": len(rows), "summary": battlog.summary(rows), "name": path.name, "storage_dir": str(Path(store).resolve())}))

        def api_job(self):
            if not self.auth():
                return
            job_id = parse_qs(urlparse(self.path).query).get("job", [""])[0]
            status = runner.status(job_id)
            self.send(200 if status is not None else 404, json.dumps(status or {"error": "Task status is no longer available."}))

        def api_busy(self):
            """For the Mac app's quit and close guard: the server knows, even when the page has lost track."""
            if self.auth():
                self.send(200, json.dumps({"writing": runner.writing}))

        def api_update(self):
            """A newer version, if there is one: {version, url}, or {}. The simulation never asks GitHub."""
            if not self.auth():
                return
            if "answer" not in checked:
                checked["answer"] = {} if os.environ.get("U2C_SIM") else update.newer() or {}
            self.send(200, json.dumps(checked["answer"]))

        def api_firmware(self):
            """Read the connected controller's bank A (read-only) and name its firmware and tweaks."""
            if not self.auth():
                return
            if not runner.claim():   # one user of the device at a time: no job may start during this read
                return self.send(409, json.dumps({"error": "Another task is running."}))
            try:
                self.firmware_report()
            finally:
                runner.release()

        def api_ring(self, req):
            """The Home ring's live level or save, by cable; over Bluetooth the Mac app sends them itself."""
            level, save = req.get("level"), req.get("save") is True
            if (level is None and not save) or (level is not None and (type(level) is not int or not 0 <= level < len(recipes.RING_ORDER))):
                return self.send(400, json.dumps({"error": "bad request"}))

            def work(ch):
                if app.read(ch, recipes.RING_CODE[0], 4) in (None, b"\xff" * 4):
                    raise OpError("Install a Home ring brightness in Tweaks first.")
                if level is not None:
                    app.set_ring(ch, level)
                if save:
                    app.save_ring(ch)
                return {"ok": True}

            self.by_cable(work)

        def by_cable(self, work):
            """Runs work(channel) on the one controller connected by cable, holding the device, and sends its answer."""
            if not runner.claim():
                return self.send(409, json.dumps({"error": "Another task is running."}))
            try:
                ours = [f for f in cli.find() if f.mode in ("cable", "cable_360", "cable_dinput")]
                if len(ours) != 1:
                    return self.send(409, json.dumps({"error": "Connect the controller by cable, without other controllers."}))
                ch = cli.open_channel(ours[0].info, 0x81)
                try:
                    if app.open_session(ch) is None:
                        raise OpError(settings_ops.NO_ANSWER)
                    probe.check_not_dongle(ch)
                    answer = work(ch)
                finally:
                    ch.close()
                self.send(200, json.dumps(answer))
            except (OpError, PermissionDenied, OSError) as e:
                self.send(409, json.dumps({"error": str(e)}))
            finally:
                runner.release()

        def api_settings_read(self):
            """Always answers with the fields, which the page builds its controls from, and says if they can be changed now."""
            if not self.auth():
                return
            fields = [{"name": f.name, "low": f.low, "high": f.high, "default": f.default, "choices": list(f.choices) if f.choices else None}
                      for f in settings.FIELDS]
            for f in fields:
                if f["choices"] is None:
                    del f["choices"]

            def answer(**rest):
                self.send(200, json.dumps({"fields": fields, "available": False, "note": "", **rest}))

            if not runner.claim():
                return answer(note="Another task is running.")
            try:
                ours = [f for f in cli.find() if f.mode in ("cable", "cable_360", "cable_dinput")]
                if len(ours) != 1:
                    return answer(note="Connect the controller by cable to change these settings.")
                ch = cli.open_channel(ours[0].info, 0x81)
                try:
                    if app.open_session(ch) is None:
                        return answer(note=settings_ops.NO_ANSWER)
                    probe.check_not_dongle(ch)
                    if not settings_ops.installed(ch):
                        return answer(note=SETTINGS_MISSING)
                    answer(available=True, **settings_ops.load(ch))
                finally:
                    ch.close()
            except (OpError, PermissionDenied, OSError) as e:
                answer(note=str(e))
            finally:
                runner.release()

        def api_settings(self, req):
            """One request: change fields, a mapping or the dead zone switch, save, or reset."""
            changes, mapping, dead_zone = req.get("set"), req.get("mapping"), req.get("dead_zone_off")
            values, save, reset = req.get("values"), req.get("save") is True, req.get("reset") is True
            good = sum(x is not None for x in (changes, mapping, dead_zone)) + save + reset == 1
            if changes is not None:
                good = good and isinstance(changes, dict) and bool(changes) and all(settings.valid(n, v) for n, v in changes.items())
            if mapping is not None:
                good = good and isinstance(mapping, dict) and mapping.get("slot") in ("r4", "l4") and type(mapping.get("mask")) is int \
                    and mapping["mask"] >= 0 and not mapping["mask"] & ~settings.MAP_MASK
            if dead_zone is not None:
                good = good and type(dead_zone) is bool
            if save:
                good = good and isinstance(values, dict) and set(values) == set(settings.BY_NAME) and all(settings.valid(n, v) for n, v in values.items())
                kept_map, kept_dz = req.get("kept_mapping"), req.get("kept_dead_zone_off")
                good = good and (kept_dz is None or type(kept_dz) is bool) and (kept_map is None or (
                    isinstance(kept_map, dict) and set(kept_map) == {"r4", "l4"}
                    and all(type(m) is int and 0 <= m <= settings.MAP_MASK for m in kept_map.values())))
            if not good:
                return self.send(400, json.dumps({"error": "bad request"}))

            def work(ch):
                if not settings_ops.installed(ch):
                    raise OpError(SETTINGS_MISSING)
                if changes is not None:
                    settings_ops.change(ch, changes)
                elif mapping is not None:
                    app.set_mapping(ch, 1 if mapping["slot"] == "l4" else 0, mapping["mask"])
                elif dead_zone is not None:
                    app.set_dead_zone(ch, dead_zone)
                elif save and not settings_ops.save(ch, values, kept_map, kept_dz):
                    raise OpError("The controller didn't confirm the save. Check the cable and try again.")
                elif reset:
                    return {"ok": True, "values": settings_ops.reset(ch)}
                settings_ops.still_there(ch)
                return {"ok": True}

            self.by_cable(work)

        def firmware_report(self):
            found = cli.find()
            ours = [f for f in found if f.mode in cli.CONTROLLER_MODES]
            if len(ours) > 1:
                return self.send(200, json.dumps({"error": "More than one device is connected. Unplug all but one controller."}))
            if not ours:
                note = "Connect it by cable, or put it in update mode."
                if any(f.mode == "xinput_only" for f in found):
                    return self.send(200, json.dumps({"note": XINPUT_NOTE}))
                if any(f.mode.startswith("bluetooth") for f in found):
                    note = "Connect the controller by cable to see or change its firmware."
                if any(f.mode == "bluetooth_xbox" for f in found):
                    note += " Over Bluetooth, you can test it in Diagnostics and change the Home ring in Settings."
                return self.send(200, json.dumps({"note": note}))
            f = ours[0]
            try:
                if f.mode.startswith("dongle") or cli._dongle_in_boot(f):
                    return self.dongle_report(f, in_boot=f.mode == "boot")
            except PermissionDenied as e:
                return self.send(200, json.dumps({"error": str(e), "permission": True}))
            except (OpError, OSError, ProtocolError) as e:
                return self.send(200, json.dumps({"error": str(e)}))
            try:
                fw, here = read_controller_details(f, store)
                pick = bkp.restorable(here)
                st, version = fw.state, fw.version
            except probe.PairedDongle:
                # Stock XInput receivers use the controller's 2dc8:310a ID.
                # The read-only memory check has identified the actual device.
                return self.dongle_report(Found(f.info, "dongle_paired"))
            except PermissionDenied as e:   # the page offers a button to Input Monitoring
                return self.send(200, json.dumps({"error": str(e), "permission": True}))
            except (OpError, OSError, ProtocolError) as e:
                return self.send(200, json.dumps({"error": str(e)}))
            keys = shown(st.recipes)
            ring = [recipes.RECIPES[k].title for k in keys if recipes.RECIPES[k].group == "ring"]
            # Details show the saved startup brightness, which may override the
            # default compiled into the ring tweak. This is not a live RAM read.
            ring_title = recipes.RECIPES[recipes.RING_ORDER[fw.ring_level]].title if fw.ring_level is not None else (ring[0] if ring else "Home ring: normal")
            self.send(200, json.dumps({"known": st.known, "recipes": keys, "version": version, "version_from": fw.version_from,
                                       "pending": fw.pending, "boot_identity": fw.boot_identity, "outdated": st.outdated, "update": update_available(st), "ring_level": fw.ring_level,
                                       "restore_backup": pick and {"name": pick.path.name, "taken": bkp.taken(pick.path), "firmware": bkp.describe(pick.state)},
                                       "unit_backups": sorted(b.path.name for b in here),
                                       "titles": [recipes.RECIPES[k].title for k in keys],
                                       "names": {k: {"label": r.name.label, "default": r.name.default, "max": r.name.max_len, "held": st.names.get(k)}
                                                 for k, r in recipes.RECIPES.items() if r.name},
                                       "tweaks": [{"key": k, "title": recipes.RECIPES[k].title, "on": k in keys}
                                                  for k in shown(k for k, r in recipes.RECIPES.items() if not r.group)]
                                                 + [{"key": "ring", "title": ring_title, "on": bool(ring)}]}))

        def dongle_report(self, f, in_boot=False):
            """The dongle, read-only: its firmware, its tweaks, and its backups here."""
            try:
                ch = cli.open_channel(f.info, 0 if in_boot else 0x81)
                try:
                    fw = dongle.read_firmware(ch, in_boot=in_boot)
                    found = dongle.unit_backups(store, dongle.read_unit(ch, in_boot))
                finally:
                    ch.close()
            except PermissionDenied as e:
                return self.send(200, json.dumps({"error": str(e), "permission": True}))
            except (OpError, OSError, ProtocolError) as e:
                return self.send(200, json.dumps({"error": str(e)}))
            usable = [(p, dongle.state(d[dongle.BASE:dongle.END])) for p, d in found]
            pick = next(((p, tweaks) for p, (known, tweaks) in usable if known), None)
            self.send(200, json.dumps({"device": "dongle", "paired": f.mode == "dongle_paired", "boot": in_boot, "backed": bool(found),
                                       "restore_backup": pick and {"name": pick[0].name, "taken": bkp.taken(pick[0]),
                                                                   "firmware": dongle.describe(pick[1])},
                                       "stock_ok": fw.known or pick is not None,
                                       "known": fw.known, "version": fw.version,
                                       "tweaks": [{"key": k, "title": title, "on": k in fw.tweaks} for k, (title, _) in dongle.TWEAKS.items()],
                                       "names": {k: {"label": n.label, "default": n.default, "max": n.max_len, "held": fw.names.get(k)}
                                                 for k, n in dongle.NAMES.items()}}))

        def api_events(self):
            if not self.auth():
                return
            with runner.cond:
                job_id = parse_qs(urlparse(self.path).query).get("job", [None])[0]
                if job_id is not None and job_id != runner.job_id:
                    return self.send(409, json.dumps({"error": "Another task has started."}))
                events = runner.events
            self.protocol_version = "HTTP/1.1"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            def chunk(data):
                self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                self.wfile.flush()
            i = 0
            while True:
                with runner.cond:
                    if i >= len(events):
                        runner.cond.wait(timeout=KEEPALIVE_S)
                    batch = events[i:]
                if not batch:   # written outside the lock, so a client that stops reading can't hold up the job
                    chunk(b": keepalive\n\n")
                    continue
                i += len(batch)
                for e in batch:
                    chunk(f"data: {json.dumps(e)}\n\n".encode())
                if batch[-1]["type"] in ("done", "error"):
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                    return

        def do_POST(self):
            if not self.auth():
                return
            try:
                req = self.body()
                if not isinstance(req, dict):
                    raise ValueError("not an object")
            except ValueError:
                return self.send(400, json.dumps({"error": "bad request"}))
            route = self.path.split("?")[0]
            if route == "/api/simulation":
                return self.api_simulation(req)
            if route == "/api/open-store":
                try:
                    open_store(store)
                    return self.send(200, json.dumps({"ok": True}))
                except OSError as exc:
                    return self.send(422, json.dumps({"error": f"Could not open the folder: {exc}"}))
            if route == "/api/bluetooth-ring/read":
                if sys.platform != "win32":
                    return self.send(404, json.dumps({"error": "Windows only"}))
                profile = req.get("profile")
                if profile not in ("xinput", "dinput"):
                    return self.send(400, json.dumps({"error": "Invalid Bluetooth profile"}))
                if not runner.claim():
                    return self.send(409, json.dumps({"error": "Another task is running."}))
                try:
                    from ..transport.windows_ring import read_bluetooth_ring
                    return self.send(200, json.dumps({"level": read_bluetooth_ring(profile)}))
                except (OSError, RuntimeError, ValueError) as exc:
                    return self.send(422, json.dumps({"error": str(exc)}))
                finally:
                    runner.release()
            if route == "/api/bluetooth-ring":
                if sys.platform != "win32":
                    return self.send(404, json.dumps({"error": "Windows only"}))
                level, save = req.get("level"), req.get("save", False)
                profile = req.get("profile", "xinput")
                if profile not in ("xinput", "dinput"):
                    return self.send(400, json.dumps({"error": "Invalid Bluetooth profile"}))
                if type(level) is not int or level not in (*range(6), 255) or type(save) is not bool:
                    return self.send(400, json.dumps({"error": "Invalid Home ring request"}))
                if not runner.claim():
                    return self.send(409, json.dumps({"error": "Another task is running."}))
                try:
                    from ..transport.windows import set_bluetooth_ring
                    if profile == "xinput":
                        set_bluetooth_ring(level, save)
                    else:
                        set_bluetooth_ring(level, save, profile=profile)
                    return self.send(200, json.dumps({"ok": True}))
                except (OSError, RuntimeError, ValueError) as exc:
                    return self.send(422, json.dumps({"error": str(exc)}))
                finally:
                    runner.release()
            if route == "/api/windows-rumble/stop":
                if sys.platform != "win32":
                    return self.send(404, json.dumps({"error": "Windows only"}))
                if req.get('kind') == 'vendor':
                    return self.send(200, json.dumps(HID_RUMBLE.stop()))
                if type(req.get("index")) is not int or not 0 <= req["index"] < 4:
                    return self.send(400, json.dumps({"error": "Invalid controller index"}))
                result = WINDOWS_RUMBLE.stop(req["index"])
                return self.send(200 if result["ok"] else 422, json.dumps(result))
            if route == "/api/windows-rumble":
                if sys.platform != "win32":
                    return self.send(404, json.dumps({"error": "Windows only"}))
                vendor = req.get('kind') == 'vendor'
                if (not vendor and (type(req.get("index")) is not int or not 0 <= req["index"] < 4)) or req.get("locality") not in ("default", "left", "right"):
                    return self.send(400, json.dumps({"error": "Invalid vibration request"}))
                if "expires" in req and (type(req["expires"]) is not int or req["expires"] < time.time() * 1000):
                    return self.send(408, json.dumps({"error": "Vibration request expired; try again."}))
                if not runner.claim():
                    return self.send(409, json.dumps({"error": "Another task is running."}))
                try:
                    result = HID_RUMBLE.rumble(req["locality"]) if vendor else WINDOWS_RUMBLE.rumble(req["index"], req["locality"])
                    return self.send(200 if result["ok"] else 422, json.dumps(result))
                finally:
                    runner.release()
            if route == "/api/linux-gamepad/rumble":
                if sys.platform != "linux":
                    return self.send(404, json.dumps({"error": "Linux only"}))
                if req.get("locality") not in ("default", "left", "right"):
                    return self.send(400, json.dumps({"error": "Invalid locality"}))
                result = GAMEPAD.rumble(req["locality"])
                return self.send(200 if result["ok"] else 422, json.dumps(result))
            if route == "/api/accept":
                state.accept(store)
                return self.send(200, json.dumps({"accepted": True}))
            if route == "/api/ring":
                return self.api_ring(req)
            if route == "/api/settings":
                return self.api_settings(req)
            if route == "/api/battery-log":
                return self.api_battery_log()
            if route == "/api/report":
                return self.send(200, json.dumps({"text": report_mod.report(cli.find(), None, report_mod.log_tail(store), report_mod.clean_extras(req))}))
            if route in ("/api/patch", "/api/restore") and not state.accepted(store):
                return self.send(403, json.dumps({"error": "Accept the disclaimer first."}))
            shown = req.get("device") if req.get("device") in ("controller", "dongle") else None   # the device the page asked about
            params = {"/api/backup": ("backup", {}),
                      "/api/patch": ("patch", dict(tweaks=",".join(req.get("tweaks", [])), download=req.get("base") == "download", device=shown,
                                                    write=bool(req.get("write")),
                                                    **{arg: n[key] for n in [req.get("names") if isinstance(req.get("names"), dict) else {}]
                                                       for key, arg in (("bt_xbox", "bt_name"), ("cable_360", "cable_name"), ("dongle_360", "xinput_name"), ("dongle_dinput", "dinput_name"))
                                                       if isinstance(n.get(key), str)})),
                      "/api/restore": ("restore", dict(source=req.get("source", "backup"), write=bool(req.get("write")), device=shown,
                                                        expect=req.get("backup") if isinstance(req.get("backup"), str) else None,
                                                        force_unknown=bool(req.get("force_unknown"))))}.get(route)
            if params is None:
                return self.send(404, json.dumps({"error": "unknown call"}))
            job_id = runner.start(params[0], **params[1])
            if not job_id:
                return self.send(409, json.dumps({"error": "Another task is running."}))
            self.send(202, json.dumps({"started": params[0], "job": job_id}))

    httpd = LocalHTTPServer(("127.0.0.1", port), Handler)
    httpd.runner = runner
    httpd.device_events = DeviceEvents()
    return httpd, token


def serve(store: Path, port: int = 0) -> None:
    httpd, token = make_server(store, port)
    page = os.environ.get("U2C_PAGE_QUERY", "") if os.environ.get("U2C_SIM") else ""   # macos/preview.sh: the page simulator, never with real devices
    print(f"U2C_URL http://127.0.0.1:{httpd.server_address[1]}/?token={token}{page}", flush=True)
    if os.environ.get("U2C_EXIT_ON_EOF"):   # started by the Mac app, which holds our stdin open while it lives
        threading.Thread(target=exit_when_orphaned, args=(httpd.runner,), daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping WebUI...", flush=True)
    finally:
        # Firmware jobs run in daemon threads. Never exit while a write is active.
        with httpd.runner.cond:
            if httpd.runner.running:
                print("Waiting for the current operation to finish...", flush=True)
            httpd.runner.cond.wait_for(lambda: not httpd.runner.running)
        httpd.server_close()


def exit_when_orphaned(runner, stdin=None, wait: float = 1.0, exit=os._exit) -> None:
    """When the app's end of stdin closes, however the app ended, exit; but a task, a write above all, runs to its end."""
    for _ in stdin or sys.stdin:
        pass
    while runner.running:
        time.sleep(wait)
    exit(0)
