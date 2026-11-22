// SPDX-License-Identifier: GPL-3.0-or-later
// The controller test: on macOS shows what GameController reports, through the Mac app's native bridge
// (macos/Diagnostics.swift). It sends the bridge {op} messages and receives window.u2cDiagnostics(event).
const CONTROLS = ["A", "B", "X", "Y", "LB", "RB", "LT", "RT", "L3", "R3", "View", "Menu", "Home", "Up", "Down", "Left", "Right", "LS", "RS"];  // as Diagnostics.swift names them
const CONTROL_LABELS = { L3: "Left stick press", R3: "Right stick press", View: "− (Select)", Menu: "+ (Start)", Up: "D-pad up", Down: "D-pad down", Left: "D-pad left", Right: "D-pad right", LS: "Left stick", RS: "Right stick" };  // as printed on the controller
const STICK_SEEN = 0.5, TRIGGER_SEEN = 0.1, STICK_TRAVEL = 7, RESULT_TIMEOUT_MS = 3000, ACCENT = "#3fc8b4";
const IDLE_LINE = "Vibrates for under a second. Nothing is written to the controller.";

let testVisible = false, testConnected = null, testState = null, testSeen = new Set(), testName = null, testMounted = null, testPending = null, testTimer = null, testId = 0;
// On Linux, poll evdev input and use a separate authenticated endpoint for bounded force feedback. Retain macOS native bridge.
let linuxPoll = null, linuxLastConnected = false, linuxTickBusy = false;
const SYSTEM = navigator.platform.startsWith("Win") ? "Windows" : navigator.platform.startsWith("Linux") ? "Linux" : "macOS";
const linuxBridge = { postMessage(m) {
  if (m.op === "start") {
    if (linuxPoll) clearInterval(linuxPoll);
    linuxPoll = setInterval(linuxTick, 70);
    linuxTick();
  } else if (m.op === "stop") {
    clearInterval(linuxPoll); linuxPoll = null; linuxLastConnected = false;
  } else if (m.op === "vibrate") {
    fetch("/api/linux-gamepad/rumble", { method: "POST", headers: { "X-Token": token, "Content-Type": "application/json" }, body: JSON.stringify({ locality: m.locality }) })
      .then(async response => { const data = await response.json(); window.u2cDiagnostics({ type: "result", id: m.id, ok: !!data.ok, error: data.error }); })
      .catch(error => window.u2cDiagnostics({ type: "result", id: m.id, ok: false, error: String(error) }));
  } else if (m.op === "direct") {
    window.u2cDiagnostics({ type: "result", id: m.id, ok: false, error: "Direct Bluetooth reports are not supported by Linux diagnostics." });
  }
}};
async function linuxTick() {
  if (!testVisible || !linuxPoll || linuxTickBusy) return;
  linuxTickBusy = true;
  try {
    const response = await fetch("/api/linux-gamepad", { headers: { "X-Token": token } });
    if (!response.ok) throw new Error("HTTP " + response.status);
    const e = await response.json();
    if (e.type === "state") {
      linuxLastConnected = !!e.connected;
      window.u2cDiagnostics(e);
    } else if (e.type === "input") {
      if (!linuxLastConnected) {
        linuxLastConnected = true;
        window.u2cDiagnostics({ ...e, type: "state" });
      }
      window.u2cDiagnostics(e);
    }
  } catch (error) {
    if (linuxLastConnected) window.u2cDiagnostics({ type: "state", connected: false });
    linuxLastConnected = false;
  } finally {
    linuxTickBusy = false;
  }
}
// Windows browsers expose XInput and HID gamepads without opening the firmware interface.
let windowsPoll = null;
let windowsRumblePoll = null, windowsNativeRumble = null, windowsRumbleBusy = false;
let windowsNativeActive = null;
let windowsBatteryPoll = null, windowsBatteryBusy = false, windowsBatteryPad = null;
let windowsBattery = { level: -1, state: "unknown" };
let windowsStockState = null, windowsStockPad = null, windowsStockAt = 0;
let windowsStockStream = null, windowsStockRetryAt = 0, windowsStockStreamPad = null;
function stopWindowsStockStream() {
  windowsStockStream?.abort();
  windowsStockStream = null; windowsStockStreamPad = null;
}
function acceptWindowsStockState(p, state) {
  if (!windowsPoll || windowsPads().length !== 1 || windowsPad()?.id !== p.id) return;
  if (state.available) {
    const id = p.id.toLowerCase();
    const match = id.match(/(?:vendor:\s*2dc8\s+product:\s*|2dc8[-:])(301[bcd])/);
    if (state.product && match && state.product !== parseInt(match[1], 16)) return;
    // 301B can be either USB or Bluetooth. Never reuse a report from the other transport.
    const category = windowsCategory(p);
    if (state.transport && ((category.startsWith("Bluetooth") && state.transport !== "Bluetooth") || (category.startsWith("USB ·") && state.transport !== "USB"))) return;
    windowsStockState = state;
    windowsStockPad = p.id;
    windowsStockAt = Date.now();
  } else if (state.connected === false) {
    windowsStockState = null;
    windowsStockPad = null;
  }
}
async function streamWindowsStock(p) {
  if (!windowsPoll || windowsStockStream || windowsPads().length !== 1 || cableBatteryBusy || (typeof busy !== "undefined" && busy) || (typeof reading !== "undefined" && reading) || Date.now() < windowsStockRetryAt || document.hidden) return;
  const control = windowsStockStream = new AbortController();
  windowsStockStreamPad = p.id;
  let reader, watchdog = setTimeout(() => control.abort(), 2000);
  try {
    const response = await fetch("/api/windows-stock-stream?token=" + encodeURIComponent(token), {signal: control.signal, cache: "no-store"});
    if (!response.ok || !response.body) throw new Error("Input stream unavailable");
    reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (!control.signal.aborted) {
      const {value, done} = await reader.read();
      if (done) break;
      clearTimeout(watchdog); watchdog = setTimeout(() => control.abort(), 2000);
      buffer += decoder.decode(value, {stream: true});
      let index;
      while ((index = buffer.indexOf("\n\n")) >= 0) {
        const packet = buffer.slice(0, index); buffer = buffer.slice(index + 2);
        if (!packet.startsWith("data: ")) continue;
        if (control !== windowsStockStream || windowsPad()?.id !== p.id) return;
        acceptWindowsStockState(p, JSON.parse(packet.slice(6)));
        windowsTick();  // Paint the new report immediately, not at the next polling tick.
      }
    }
  } catch (_) { /* Reconnect without clearing the last valid sample. */ }
  finally {
    clearTimeout(watchdog);
    if (reader) { reader.cancel().catch(() => {}); reader.releaseLock(); }
    if (windowsStockStream === control) {
      windowsStockStream = null; windowsStockStreamPad = null;
      windowsStockRetryAt = Date.now() + 200;
    }
  }
}
const windowsPads = () => Array.from(navigator.getGamepads ? navigator.getGamepads() : []).filter(p => p && p.connected);
const windowsPad = () => windowsPads().find(p => p.mapping === "standard") || windowsPads()[0];
function knownStockPad(p) {
  return /vendor:\s*2dc8\s+product:\s*301[bcd]|2dc8[-:]301[bcd]/i.test(p.id);
}
function mapUltimate2cBluetooth(p) {
  // SDL's 2dc8:301b button map; axes here use the browser's HID usage slots,
  // not SDL's axis indices. Firefox exposes X/Y/Z/Rx/Ry/Rz ... Hat as 0..9.
  // Reference: libsdl-org/SDL SDL2/src/joystick/SDL_gamecontrollerdb.h.
  const id = p.id.toLowerCase();
  const known = /vendor:\s*2dc8\s+product:\s*301[bcd]/.test(id) || /2dc8[-:]301[bcd]/.test(id)
    || id.includes("8bitdo ultimate 2c wireless");
  if (p.mapping === "standard" || !known || p.axes.length !== 10 || p.buttons.length < 15) return null;
  const axis = i => Math.max(-1, Math.min(1, p.axes[i] || 0));
  const pressed = [];
  const buttons = {0: "A", 1: "B", 2: "L4", 5: "R4", 3: "X", 4: "Y", 6: "LB", 7: "RB", 10: "View", 11: "Menu", 12: "Home", 13: "L3", 14: "R3"};
  for (const [index, name] of Object.entries(buttons)) if (p.buttons[index]?.pressed || p.buttons[index]?.value > .5) pressed.push(name);
  // Digital presses can highlight the controls, but are not analog travel:
  // they switch to 1 before a trigger reaches the end of its physical range.
  const lt = 0, rt = 0;
  if (p.buttons[8]?.pressed || p.buttons[8]?.value > .5) pressed.push("LT");
  if (p.buttons[9]?.pressed || p.buttons[9]?.value > .5) pressed.push("RT");
  // Hat values 0..7 are scaled to -1..1; neutral 15 becomes 3.286.
  const hat = p.axes[9];
  if (Number.isFinite(hat) && hat >= -1.01 && hat <= 1.01) {
    const direction = Math.round((hat + 1) * 3.5);
    const directions = [["Up"], ["Up", "Right"], ["Right"], ["Down", "Right"], ["Down"], ["Down", "Left"], ["Left"], ["Up", "Left"]];
    if (directions[direction]) pressed.push(...directions[direction]);
  }
  return {name: "8BitDo Ultimate 2C Wireless Controller", category: windowsCategory(p, "D-input"), extended: true, raw: false,
    pressed, lx: axis(0), ly: -axis(1), rx: axis(2), ry: -axis(5), lt, rt, analogTriggers: false};
}
async function windowsBatteryStatus() {
  const p = windowsPad();
  if (windowsBatteryBusy || !p || document.hidden) return;
  windowsBatteryBusy = true;
  windowsBatteryPad = p.id;
  try {
    const result = await api("GET", "/api/windows-battery", undefined, AbortSignal.timeout(2000));
    if (windowsPoll && windowsPad()?.id === p.id) windowsBattery = result;
  } catch (_) { /* Keep the last reading until a successful refresh or disconnect. */ }
  finally { windowsBatteryBusy = false; }
}
const windowsActuator = () => typeof windowsPad()?.vibrationActuator?.playEffect === "function" ? windowsPad().vibrationActuator : null;
const windowsNativeTarget = () => {
  if (!windowsNativeRumble || windowsPads().length !== 1) return null;
  if (windowsNativeRumble.kind === 'vendor') {
    const product=windowsNativeRumble.product.toString(16);
    const category=windowsCategory(windowsPad());
    if (windowsNativeRumble.transport === 'Bluetooth' && !category.startsWith('Bluetooth')) return null;
    if (windowsNativeRumble.transport === 'USB' && category.startsWith('Bluetooth')) return null;
    return new RegExp('vendor:\\s*2dc8\\s+product:\\s*'+product+'|2dc8[-:]'+product,'i').test(windowsPad().id) ? windowsNativeRumble : null;
  }
  return windowsPad().mapping === 'standard' ? windowsNativeRumble : null;
};
function windowsStopRumble() {
  if (typeof st !== "undefined" && st && st.simulation) return;
  windowsPad()?.vibrationActuator?.reset()?.catch(() => {});
  const active = windowsNativeActive;
  if (active) {
    windowsNativeActive = null;
    fetch("/api/windows-rumble/stop", { method: "POST", keepalive: true, headers: { "X-Token": token, "Content-Type": "application/json" }, body: JSON.stringify({ index: active.index, kind: active.kind }) }).catch(() => {});
  }
}
window.addEventListener?.("pagehide", () => { if (SYSTEM === "Windows") windowsStopRumble(); });
document.addEventListener?.("visibilitychange", () => { if (SYSTEM === "Windows" && document.hidden) { windowsStopRumble(); stopWindowsStockStream(); } });
async function windowsRumbleStatus() {
  if (windowsRumbleBusy || windowsNativeActive || document.hidden) return;
  windowsRumbleBusy = true;
  try {
    const r = await fetch("/api/windows-rumble", { signal: AbortSignal.timeout(1500), headers: { "X-Token": token } });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const status = await r.json();
    if (windowsPoll) windowsNativeRumble = status.available ? status : null;
  } catch (_) { /* Retain capability on a transient failure; POST rechecks the actual slot. */ }
  finally { windowsRumbleBusy = false; }
}
function windowsCategory(p, inputMode = null) {
  const id = p.id.toLowerCase();
  if (/vendor:\s*2dc8\s+product:\s*301d|2dc8[-:]301d/.test(id)) return "USB · D-input";
  if (/vendor:\s*2dc8\s+product:\s*301b|2dc8[-:]301b/.test(id)) {
    const matches = typeof st === 'undefined' ? [] : (st.devices || []).filter(d => ['cable_dinput','dongle_paired','bluetooth'].includes(d.mode));
    return matches.length === 1 ? (matches[0].mode === 'dongle_paired' ? '2.4 GHz dongle · D-input' : matches[0].mode === 'cable_dinput' ? 'USB · D-input' : 'Bluetooth · D-input') : 'Gamepad · D-input';
  }
  if (/vendor:\s*2dc8\s+product:\s*301c|2dc8[-:]301c/.test(id)) return "2.4 GHz dongle · D-input";
  if (/vendor:\s*045e\s+product:\s*0b13|045e[-:]0b13/.test(id)) return "Bluetooth · XInput";
  // A generic XInput identity cannot distinguish a receiver from a cable until identification.
  if ((typeof reading !== "undefined" && reading) || (typeof refreshing !== "undefined" && refreshing)) return "Identifying connection…";
  if (typeof fw !== "undefined" && fw && fw.error) return "Connection unavailable";
  // Associate the browser input with transport metadata only when unambiguous.
  const devices = typeof st === "undefined" ? [] : (st.devices || []).filter(d =>
    ["cable", "cable_360", "cable_dinput", "dongle_paired", "bluetooth", "bluetooth_xbox", "xinput_only"].includes(d.mode));
  if (windowsPads().length === 1 && devices.length === 1) {
    const mode = devices[0].mode;
    if (mode === "cable_dinput") return "USB · D-input";
    if (mode === "bluetooth") return "Bluetooth · D-input";
    if (mode === "bluetooth_xbox") return "Bluetooth · XInput";
    if (mode === "dongle_paired") return "2.4 GHz dongle · " + (inputMode || "XInput");
    if (mode === "cable" || mode === "cable_360") return typeof fw !== "undefined" && !fw ? "USB / 2.4 GHz · " + (inputMode || "XInput") : "USB · " + (inputMode || "XInput");
    // An input-only Xbox identity cannot distinguish cable from receiver.
  }
  return inputMode ? "Gamepad · " + inputMode : "Gamepad";
}
function windowsModelName(p) {
  if (/8bitdo ultimate 2c|vendor:\s*2dc8\s+product:\s*(301b|301c|301d|310a)|2dc8[-:](301b|301c|301d|310a)/i.test(p.id))
    return "8BitDo Ultimate 2C Wireless Controller";
  // Generic XInput names identify an API, not a model. Only replace one when
  // the single input device can be associated with an actual 8BitDo HID name.
  if (windowsPads().length === 1 && /xbox|xinput|standard gamepad|045e/i.test(p.id) && typeof st !== "undefined" &&
      (st.devices || []).filter(d => /8bitdo ultimate 2c/i.test(d.name || "")).length === 1)
    return "8BitDo Ultimate 2C Wireless Controller";
  return p.id;
}
function windowsTick() {
  if ((typeof busy !== "undefined" && busy) || (typeof reading !== "undefined" && reading) || cableBatteryBusy) stopWindowsStockStream();
  const p = windowsPad();
  if (!p) {
    stopWindowsStockStream();
    windowsBatteryPad = null; windowsBattery = { level: -1, state: "unknown" };
    windowsStockState = null; windowsStockPad = null;
    window.u2cDiagnostics({ type: "state", connected: false, error: "Connect the controller and press a button while this page is focused." });
    return;
  }
  if (windowsBatteryPad !== p.id) {
    windowsBattery = { level: -1, state: "unknown" };
    windowsBatteryStatus();
  }
  const names = ["A", "B", "X", "Y", "LB", "RB", "LT", "RT", "View", "Menu", "L3", "R3", "Up", "Down", "Left", "Right", "Home"];
  const e = { connected: true, name: windowsModelName(p), category: windowsCategory(p), extended: p.mapping === "standard", raw: p.mapping !== "standard",
    rawAxes: Array.from(p.axes), rawButtons: Array.from(p.buttons, button => button.value),
    foreground: document.hasFocus(), localities: windowsNativeTarget() || windowsActuator() ? ["default", "left", "right"] : [],
    battery: windowsBatteryPad === p.id && windowsPads().length === 1 ? windowsBattery : { level: -1, state: "unknown" }, pressed: p.mapping === "standard" ? names.filter((_, i) => p.buttons[i]?.pressed) : [],
    lx: p.axes[0] || 0, ly: -(p.axes[1] || 0), rx: p.axes[2] || 0, ry: -(p.axes[3] || 0),
    lt: p.buttons[6]?.value || 0, rt: p.buttons[7]?.value || 0 };
  const mapped = mapUltimate2cBluetooth(p);
  if (mapped) {
    Object.assign(e, mapped);
    // Preserve the last analog sample while the continuous stream reconnects;
    // do not replace fresh browser buttons/sticks.
    if (windowsStockPad === p.id && windowsStockState && Date.now() - windowsStockAt <= 3000) {
      e.lt = windowsStockState.lt; e.rt = windowsStockState.rt;
      e.analogTriggers = true;
      e.pressed = e.pressed.filter(name => name !== "LT" && name !== "RT");
      if (e.lt > TRIGGER_SEEN) e.pressed.push("LT");
      if (e.rt > TRIGGER_SEEN) e.pressed.push("RT");
    }
  }
  if (windowsStockStreamPad && windowsStockStreamPad !== p.id) { stopWindowsStockStream(); windowsStockState = null; windowsStockPad = null; }
  if (windowsPads().length === 1 && (knownStockPad(p) || mapped || /D-input/.test(e.category))) {
    const native = windowsStockPad === p.id && Date.now()-windowsStockAt <= 3000 ? windowsStockState : null;
    // Keep browser sticks/buttons at the UI frame rate. HID supplements only
    // the controls that the browser cannot expose reliably.
    if (native && Array.isArray(native.pressed)) {
      e.pressed = e.pressed.filter(name => name !== "L4" && name !== "R4");
      e.pressed.push(...native.pressed.filter(name => name === "L4" || name === "R4"));
    }
    streamWindowsStock(p);
  } else {
    stopWindowsStockStream(); windowsStockState = null; windowsStockPad = null;
  }
  window.u2cDiagnostics({ ...e, type: "state" });
  window.u2cDiagnostics({ ...e, type: "input" });
}
const windowsBridge = { postMessage(m) {
  if (m.op === "ring") {
    fetch("/api/bluetooth-ring", { method: "POST", headers: { "X-Token": token, "Content-Type": "application/json" }, body: JSON.stringify({ level: m.level, save: m.save, ...(m.profile ? { profile: m.profile } : {}) }) })
      .then(async response => { const result = await response.json(); window.u2cDiagnostics({ type: "result", op: "ring", id: m.id, ok: response.ok && !!result.ok, error: result.error }); })
      .catch(error => window.u2cDiagnostics({ type: "result", op: "ring", id: m.id, ok: false, error: String(error) }));
  } else if (m.op === "start") {
    clearInterval(windowsPoll);
    windowsPoll = setInterval(windowsTick, 16);
    clearInterval(windowsRumblePoll);
    windowsRumblePoll = setInterval(windowsRumbleStatus, 1000);
    windowsRumbleStatus();
    clearInterval(windowsBatteryPoll);
    windowsBatteryPoll = setInterval(windowsBatteryStatus, 60000);
    windowsTick();
  } else if (m.op === "stop") {
    stopWindowsStockStream();
    clearInterval(windowsPoll); windowsPoll = null;
    windowsStockState = null; windowsStockPad = null;
    clearInterval(windowsBatteryPoll); windowsBatteryPoll = null;
    windowsBatteryPad = null; windowsBattery = { level: -1, state: "unknown" };
    clearInterval(windowsRumblePoll); windowsRumblePoll = null; windowsNativeRumble = null;
    windowsStopRumble();
  } else if (m.op === "vibrate") {
    const actuator = windowsActuator();
    const target = windowsNativeTarget();
    const native = target?.kind === 'vendor' || !actuator ? target : null;
    if (native) {
      if (windowsNativeActive) {
        window.u2cDiagnostics({ type: "result", id: m.id, ok: false, error: "A vibration test is already running." });
        return;
      }
      const active = windowsNativeActive = { index: native.index, kind: native.kind, id: m.id };
      fetch("/api/windows-rumble", { method: "POST", signal: AbortSignal.timeout(2500), headers: { "X-Token": token, "Content-Type": "application/json" }, body: JSON.stringify({ index: native.index, kind: native.kind, locality: m.locality, expires: Date.now() + 1500 }) })
        .then(async response => { const result = await response.json(); window.u2cDiagnostics({ type: "result", id: m.id, ok: response.ok && !!result.ok, error: result.error }); })
        .catch(error => { if (windowsNativeActive === active) windowsStopRumble(); window.u2cDiagnostics({ type: "result", id: m.id, ok: false, error: String(error) }); })
        .finally(() => { if (windowsNativeActive === active) windowsNativeActive = null; });
      return;
    }
    if (!actuator || !["default", "left", "right"].includes(m.locality)) {
      window.u2cDiagnostics({ type: "result", id: m.id, ok: false, error: "Vibration is unavailable for this controller or browser." });
      return;
    }
    actuator.playEffect("dual-rumble", { duration: 450, startDelay: 0,
      strongMagnitude: m.locality === "right" ? 0 : 0.3, weakMagnitude: m.locality === "left" ? 0 : 0.3 })
      .then(result => window.u2cDiagnostics({ type: "result", id: m.id, ok: result === "complete", error: result === "complete" ? null : "Vibration interrupted." }))
      .catch(error => window.u2cDiagnostics({ type: "result", id: m.id, ok: false, error: String(error) }));
  } else if (m.op === "direct") {
    window.u2cDiagnostics({ type: "result", id: m.id, ok: false, error: "Direct Bluetooth reports are unavailable in browser diagnostics." });
  }
}};
const firmwareLabBridge = {postMessage(m) {
  if (typeof simulationMessage === "function") simulationMessage(m);
}};
const bridge = () => window.u2cSimBridge || (typeof st !== "undefined" && st && st.simulation ? firmwareLabBridge : null) || (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.diagnostics) || (SYSTEM === "Windows" ? windowsBridge : SYSTEM === "Linux" ? linuxBridge : null);

function clearPending() {
  clearTimeout(testTimer);
  testPending = null;
}

function diagnosticsShow() {
  clearPending();
  testVisible = true; testState = null; testConnected = null; testName = null; testMounted = null; testSeen = new Set();
  syncCableBattery();
  if (cableBatteryKey && !cableBattery) cableBatteryDue = Math.max(cableBatteryDue, Date.now() + CABLE_BATTERY_SETTLE);
  clearInterval(cableBatteryTimer);
  cableBatteryTimer = setInterval(pollCableBattery, 1000); // scheduling only; hardware reads are at least 60 seconds apart
  const b = bridge();
  if (b) b.postMessage({ op: "start" });
  diagnosticsRender();
}

function diagnosticsHide() {
  clearPending();
  testVisible = false;
  clearInterval(cableBatteryTimer); cableBatteryTimer = null;
  const b = bridge();
  if (b) b.postMessage({ op: "stop" });
}

function diagnosticsSummary() {
  if (!testState || !testState.connected) return null;
  return { name: testState.name, category: testState.category, localities: testState.localities };
}

function testMode() {
  if (!bridge()) return "web";
  if (busy) return "busy";
  if (st && st.devices.some((d) => d.mode === "boot")) return "boot";
  // A completed read (including an error/note) releases the gate. Do not
  // block unrelated gamepads or transports without a firmware target.
  if (typeof firmwareTarget === "function" && firmwareTarget(st) &&
      (reading || !fw)) return "identifying";
  if (!testState || !testState.connected) return "none";
  if (!testState.extended) return "noext";
  if (!testState.foreground) return "background";
  return "live";
}

const TEST_MESSAGES = {
  web: "The controller test is only available in the Mac app. Everything else in this tab works here.",
  busy: "The controller test is unavailable while a task is running.",
  boot: "The controller test can't run in update mode. Disconnect the controller and connect it again by Bluetooth or cable.",
  none: SYSTEM === "Windows" ? "Connect the controller, focus this page and press a button to test it." : "No controller found. Connect the controller by Bluetooth or cable to test it.",
  identifying: "Reading firmware…",
  noext: `${SYSTEM} sees this controller, but not as a standard gamepad, so its controls can't be tested here.`,
  background: `The controller test is paused, because ${SYSTEM} only sends controller input to the app in front.`,
};

let cableAsked = false;

function diagnosticsRender() {
  const body = $("test-body");
  syncCableBattery();
  if (!body || !testVisible) return;
  cableAsked = byCable();
  const mode = testMode();
  const key = mode === "live" ? `live:${testState.name}:${testState.category}:${testState.localities.join()}:${directLine()}:${cableAsked}` : mode === "noext" && testState.raw ? `raw:${testState.name}:${cableAsked}` : mode;
  if (key === testMounted) return;
  testMounted = key;
  if (mode === "noext" && testState.raw && SYSTEM === "Windows") {
    body.innerHTML = `<div class="test"><div><h3 id="test-raw-name"></h3><p>This controller is detected without a standard button mapping. Raw button numbers and axis values are shown below; they are not assigned Xbox button names.</p><p id="test-raw-buttons"></p><p id="test-raw-axes"></p><p id="test-battery"></p></div></div>`;
    $("test-raw-name").textContent = testState.name;
    mountCableBatteryButton();
    paintRawInput(testState);
    return;
  }
  if (mode === "identifying") {
    body.innerHTML = `<div class="test"><div class="boot" role="status">Reading firmware…</div></div>`;
    return;
  }
  if (mode !== "live") {
    body.innerHTML = `<div class="test"><div class="test-pic dimmed">${pad(null, false, "test-g")}</div><div class="boot"><div id="test-message"></div></div></div>`;
    $("test-message").textContent = TEST_MESSAGES[mode];
    return;
  }
  mountLive(body);
}

function paintRawInput(event) {
  $("test-raw-buttons").textContent = "Buttons: " + (event.rawButtons || []).map((value, i) => `${i + 1}: ${value.toFixed(2)}`).join(" · ");
  $("test-raw-axes").textContent = "Axes: " + (event.rawAxes || []).map((value, i) => `${i + 1}: ${value.toFixed(3)}`).join(" · ");
}

const directAvailable = () => SYSTEM !== "Windows" && !!st && st.devices.some((d) => d.mode === "bluetooth_xbox");

const directLine = () => SYSTEM === "Windows" ? "Direct Bluetooth reports are unavailable in browser diagnostics." : directAvailable() ? IDLE_LINE
  : st && st.devices.some((d) => d.mode === "bluetooth") ? "The direct report needs the XInput mode over Bluetooth tweak."
  : "The direct report only works over Bluetooth.";

function batteryText(b) {  // macOS defaults to level 0 and an unknown state when it has nothing to report
  if (!b || b.level < 0 || (b.state === "unknown" && b.level === 0)) return `${SYSTEM} doesn't report the battery for this controller`;
  if (b.state === "full") return "Full";
  return `${Math.round(b.level * 100)} %${b.state === "charging" ? ", charging" : ""}`;
}

let cableBattery = null;   // Own battery via controller USB or experimental dongle RF transport

const byCable = () => !!st && st.devices.some((d) => d.mode === "cable" || d.mode === "cable_360" || d.mode === "cable_dinput");
const byDongle = () => !!st && st.devices.some((d) => d.mode === "dongle_paired");
const byBatteryTransport = () => byCable() || byDongle();

const CABLE_BATTERY_INTERVAL = 60000, CABLE_BATTERY_SETTLE = 5000;
let cableBatteryBusy = false, cableBatteryTimer = null, cableBatteryKey = "", cableBatteryGeneration = 0, cableBatteryDue = 0;
function syncCableBattery() {
  const devices = typeof st === "undefined" ? [] : (st?.devices || []);
  const cable = devices.filter(d => ["cable", "cable_360", "cable_dinput", "dongle_paired"].includes(d.mode));
  const key = cable.length === 1 && devices.length === 1 ? JSON.stringify(cable[0]) : "";
  if (key !== cableBatteryKey) {
    cableBatteryKey = key; cableBatteryGeneration++;
    cableBattery = null;
    cableBatteryDue = key ? Date.now() + CABLE_BATTERY_SETTLE : 0;
  }
}
function pollCableBattery() {
  syncCableBattery();
  if (cableBatteryKey && Date.now() >= cableBatteryDue) readCableBattery();
}
async function readCableBattery() {
  syncCableBattery();
  if (!testVisible || document.hidden || busy || testPending || cableBatteryBusy || !cableBatteryKey ||
      (typeof reading !== "undefined" && reading) || (typeof refreshing !== "undefined" && refreshing)) return;
  cableBatteryBusy = true;
  cableBatteryDue = Date.now() + CABLE_BATTERY_INTERVAL;
  stopWindowsStockStream();
  const generation = cableBatteryGeneration;
  try {
    const got = await api("GET", "/api/battery");
    syncCableBattery();
    if (!got.busy && generation === cableBatteryGeneration) cableBattery = (got.source === "cable" || got.source === "dongle") ? got : null;
  } catch (e) { return; } finally { cableBatteryBusy = false; }
  paintBattery();
}

function mountCableBatteryButton() {
  if (!byBatteryTransport() || !document.createElement) return;
  const row = $("test-battery");
  if (!row?.parentNode?.after) return;
  const button = document.createElement("button");
  button.className = "sec small"; button.textContent = "Read battery";
  button.title = "Direct USB uses controller telemetry; a 2.4 GHz dongle in either DInput or XInput uses its vendor HID channel and requires the RF battery tweaks.";
  button.onclick = readCableBattery; row.parentNode.after(button);
}

function paintBattery() {
  const row = $("test-battery");
  if (!row || !testState) return;
  const own = testState.battery, c = cableBattery;
  const none = !own || own.level < 0 || (own.state === "unknown" && own.level === 0);
  const through = c?.source === "dongle" ? "via 2.4 GHz" : "from the controller";
  row.textContent = byBatteryTransport() && !c ? "Waiting for battery reading…" : c && byBatteryTransport() && !c.unavailable
    ? (c.full ? `Charged (${through})` : `${c.percent} %${c.charging ? ", charging" : ""} (${through})`)
    : none && c && byBatteryTransport() && c.unavailable ? "Battery telemetry unavailable in this mode or firmware" : batteryText(own);
}

function vibrationText(localities) {
  if (!localities.length) return "Not available";
  return localities.includes("left") && localities.includes("right") ? "Available, also on the left and right sides separately" : "Available";
}

function testControls() { return /D-input/i.test(testState?.category || "") ? [...CONTROLS,"L4","R4"] : CONTROLS; }
function mountLive(body) {
  const s = testState, has = (l) => s.localities.includes(l), direct = directAvailable();
  const button = (op, label, ok) => `<button class="sec small" data-op="${op}" data-was="${ok ? "" : "1"}"${ok && !testPending ? "" : " disabled"}>${label}</button>`;
  body.innerHTML = `<div class="test"><div class="test-pic" id="test-pic">${pad(ACCENT, false, "test-g")}</div><div>
    <dl class="rows"><dt>${SYSTEM} sees</dt><dd id="test-name"></dd><dt>Connection</dt><dd id="test-category"></dd><dt>Vibration</dt><dd id="test-vibration"></dd><dt>Battery</dt><dd id="test-battery"></dd></dl>
    <div class="rum">${button("default", SYSTEM === "Windows" && windowsNativeTarget()?.kind === "vendor" ? "Test Both Motors" : "Vibrate Like a Game", has("default"))}${button("left", "Left Motor", has("left"))}${button("right", "Right Motor", has("right"))}${button("direct", "Direct Report", direct)}</div>
    <p class="res" id="test-res"></p></div></div>
    <p class="count" id="test-count"></p>
    <div class="seen" id="test-seen">${testControls().map((c) => `<span data-c="${c}">${CONTROL_LABELS[c] || c}</span>`).join("")}</div>
    <div class="tg"><div>LT<i><i id="test-lt"></i></i></div><div>RT<i><i id="test-rt"></i></i></div></div><p class="hint" id="test-trigger-note" hidden></p>`;
  $("test-name").textContent = s.name;
  $("test-category").textContent = s.category;
  $("test-vibration").textContent = vibrationText(s.localities);
  $("test-res").textContent = directLine();
  paintBattery();
  mountCableBatteryButton();
  body.querySelectorAll("[data-op]").forEach((b) => (b.onclick = () => vibrate(b.dataset.op)));
  body.querySelectorAll("#test-pic [data-k]").forEach((e) => { e.dataset.off = e.getAttribute(e.tagName === "path" ? "stroke" : "fill") || ""; });
  paintSeen();
}

function paintSeen() {
  document.querySelectorAll("#test-seen [data-c]").forEach((e) => e.classList.toggle("s", testSeen.has(e.dataset.c)));
  const count = $("test-count");
  if (count) count.innerHTML = `<b>${testControls().filter(c => testSeen.has(c)).length} of ${testControls().length}</b> controls checked. Press each one to check it.`;
}

function paintInput(e) {
  const pressed = new Set(e.pressed || []);
  document.querySelectorAll("#test-pic [data-k]").forEach((el) => {
    const k = el.dataset.k;
    if (k === "LS" || k === "RS") {
      const [x, y] = k === "LS" ? [e.lx, e.ly] : [e.rx, e.ry];
      el.setAttribute("transform", `translate(${+(x * STICK_TRAVEL).toFixed(2)} ${+(-y * STICK_TRAVEL).toFixed(2)})`);
      el.children[1].setAttribute("fill", pressed.has(k === "LS" ? "L3" : "R3") ? ACCENT : "#424851");
    } else {
      el.setAttribute(el.tagName === "path" ? "stroke" : "fill", pressed.has(k) ? ACCENT : el.dataset.off);
    }
  });
  document.querySelectorAll("#test-seen [data-c]").forEach((el) => el.classList.toggle("now", pressed.has(el.dataset.c)));
  paintTriggers(e);
}

function paintTriggers(e) {
  const analog = e.analogTriggers !== false;
  for (const key of ["lt", "rt"]) {
    const value = analog ? Math.max(0, Math.min(1, e[key] || 0)) : 0;
    $("test-" + key).style.width = `${Math.round(value * 100)}%`;
  }
  const note = $("test-trigger-note");
  if (note) {
    note.hidden = analog;
    note.textContent = analog ? "" : "Analog trigger readout unavailable. Digital presses are detected, but do not show how far the trigger is pressed.";
  }
}

function vibrate(op) {
  if (testPending || !bridge()) return;
  testPending = op;
  const id = ++testId;
  document.querySelectorAll("[data-op]").forEach((b) => { b.dataset.was = b.disabled ? "1" : ""; b.disabled = true; });
  testTimer = setTimeout(() => finishVibrate("No answer from the app."), RESULT_TIMEOUT_MS);
  bridge().postMessage(op === "direct" ? { op: "direct", id } : { op: "vibrate", locality: op, id });
}

function finishVibrate(line) {
  clearTimeout(testTimer);
  testPending = null;
  document.querySelectorAll("[data-op]").forEach((b) => { b.disabled = b.dataset.was === "1"; });
  const res = $("test-res");
  if (res) res.textContent = line;
}

window.u2cDiagnostics = (e) => {
  if (e.type === "result" && e.op === "ring") {  // the Home ring card is in another tab
    if (ringAsked && e.id === ringAsked.id) ringDone(e.ok ? null : e.error || "It couldn't be sent.", ringAsked.level, ringAsked.save);
    return;
  }
  if (e.type === "input" && tab === "settings") return settingsLive(e);
  if (!testVisible) return;
  if (e.type === "state") {
    if (e.connected && testName && e.name !== testName) testSeen = new Set();
    if (e.connected) testName = e.name;
    testState = e;
    testConnected = !!e.connected;
    diagnosticsRender();
    paintBattery();
  } else if (e.type === "input" && e.raw && testMode() === "noext") {
    paintRawInput(e);
  } else if (e.type === "input" && testMode() === "live" && $("test-lt")) {
    for (const p of e.pressed || []) testSeen.add(p);
    if (Math.hypot(e.lx, e.ly) > STICK_SEEN) testSeen.add("LS");
    if (Math.hypot(e.rx, e.ry) > STICK_SEEN) testSeen.add("RS");
    if (e.lt > TRIGGER_SEEN) testSeen.add("LT");
    if (e.rt > TRIGGER_SEEN) testSeen.add("RT");
    paintInput(e);
    paintSeen();
  } else if (e.type === "result" && testPending && e.id === testId) {
    const direct = testPending === "direct";
    finishVibrate(!e.ok ? e.error || "It couldn't be sent." : direct ? "Sent. You'll only feel it if Rumble over Bluetooth is installed." : "Sent. Did you feel it?");
  }
};
