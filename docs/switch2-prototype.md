# Y profile: offline Switch 2 Pro experiment

Target: Switch 2. This is not an installable tweak. No device I/O, flash writes,
settings changes or Home-ring changes are performed by these tools. A console
is currently unavailable, so compatibility and pairing are not hardware-tested.

## Wire formats and pairing

`python tools/switch2_prototype.py` creates a 31-byte discovery advertisement
and a neutral 63-byte BLE input value. The encoder covers buttons, 12-bit stick
coordinates, power flags and battery level. Nintendo labels are used; physical
2C mapping is not integrated. Motion/NFC/headset fields stay zero. Discovery,
reconnection and wake packets are format experiments, not radio behavior.

`tools/switch2_pairing.py` models the custom address, key-component, challenge
and finalization commands. It checks ordering and lengths, commits bonds only
on finalization, and retains committed in-memory bonds across disconnects.
The reference AES-128 implementation is tested against NIST vectors and the
published pairing exchange, including wire byte order. This is a protocol model,
not a BLE security implementation. The compiled integration and journal tests
below add separate coverage; radio transmission and installed startup hooks remain absent.
Repeated finalization in the same committed session returns the existing ACK
without changing the bond. A malformed command raises an error; no undocumented negative reply is invented.

`python -m unittest tests.test_switch2_pairing tests.test_switch2_prototype`
checks the public vectors, controls, boundaries, pairing state and interpreter
instructions used by the transport checks.

## Executed stock firmware paths

`python tools/check_switch2_transport.py --backup FULL_CONTROLLER_BACKUP`
extracts a hash-verified original 1.09 bank and executes its TC32 routines:

- Notification gate at 0x1458c: MTU 23 accepts 16/20-byte values but rejects
  21/63-byte values (0xb5). MTU 66 permits the 63-byte value.
- Initializers 0x11e64 and 0x11ea4 establish the actual stock layout: software TX
  has eight 40-byte entries at 0x843178; hardware RX has four 64-byte entries at
  0x8432b8. 0x121a0 registers TX callback 0x122fd. These are unchanged stock
  buffers; no RAM enlargement is needed for the tested unencrypted report.
- L2CAP fragmentation at 0x1484c emits payload lengths 27, 27 and 16 for the
  63-byte report. Reassembly checks the complete L2CAP/ATT headers and input.
  Four TX slots are a synthetic negative case, not the stock configuration.
  At eight slots, two occupied entries permit the report; three yield 0x81.
- The real callback at 0x122fc copies all three fragments into the stock TX FIFO.
  Sixteen placements test wrapping through every slot. Writes outside the TX
  pool, metadata, expected registers and interpreter stack are rejected.

Hardware registers are byte-storage models. Radio DMA/interrupts are not run.
The memory-copy and signed-division helpers are modeled at arithmetic boundaries;
the actual notification, fragmentation, initializer and queue routines execute.
The original queue-only test has encryption disabled. Additional checks below
execute the encrypted path. None of these checks establish sustained throughput,
radio transmission or negotiated MTU on real hardware.
An earlier draft swapped the RX/TX labels; the initializer test replaces that
assumption and no candidate buffer relocation is applied.

## ATT channel integration (offline)

`tools/switch2_gatt.py` models ATT MTU exchange, CCCD subscription, read/blob
reads of neutral input, input notifications and pairing commands on handle
0x0014 with replies on 0x001e. It enforces Write Without Response, negotiated
MTU and subscription state. Disconnect resets MTU/subscriptions and pending
pairing, while retaining committed in-memory bonds. Unsupported operations,
combined vibration/command packets and unknown attributes are refused rather
than acknowledged as working. This is a partial endpoint, not service discovery
or a registered firmware GATT table. Tests are in `tests/test_switch2_gatt.py`.

The transport checker additionally feeds all four pairing reply notifications
and one input notification from this model through the actual stock TC32
fragmenter and TX enqueue routine, then reassembles and verifies every byte.
This bridges the two offline tests; it does not execute real ATT reception.

## Initial connection interval validation

Stock routine 0x11928 checks the received CONNECT_IND interval at packet
bytes 0x1c..0x1d. Instruction 0x11938 subtracts 6 units; the unsigned range
check against 3194 at 0x11a00 accepts 6..3200 units (1.25 ms per unit).
Thus a 4-unit / 5 ms connection is rejected before link setup at 0x12cf8.

The checker constructs a temporary candidate only in Python memory: subtract
4 instead of 6 and increase the range literal to 3196, retaining the original
upper limit of 3200. Both versions execute in the interpreter at 0, 3, 4, 5,
6, 3200, 3201 and 65535 units. Stock rejects 4/5; the candidate accepts them;
both reject below their lower bound and above 3200. No candidate image or
production recipe is written. The test stops at link setup, so it does not
prove radio timing, later connection updates, scheduler behavior or stability.
The ESP32 reference patch cannot be reused directly: it targets another CPU
and Bluetooth stack.

## Link setup and encrypted FIFO (executed stock code)

`tools/check_switch2_link.py`, invoked by the transport checker, continues from
0x11928 through real link setup at 0x12cf8. It executes channel remapping, initial
anchor-time calculations, connection-state changes and sixteen subsequent
anchor updates. Stock and candidate are compared at 4, 6 and 10 units with
normal and wrapping clocks. The candidate stores 4 units and 80,000 timer ticks
for a 5 ms connection; the original interval multiplication has no second 6-unit
clamp on this path. All 37 channels follow the expected hop sequence.

The harness supplies a scratch context pointer normally supplied by boot and
models peripheral registers as storage. Optional application/HCI callbacks are
disabled, no radio ISR runs, and advancing anchors is an explicit test call,
not a real interrupt schedule. Later connection-update validation is not covered.

`tools/check_switch2_crypto.py` executes the real enqueue encryption path,
including CCM assembly at 0x16ccc, CBC-MAC at 0x16894 and CTR at 0x16a24.
Only the AES peripheral and memcpy/memset boundaries are modeled; AES-128 uses
the separately tested reference block primitive. Twelve expected ciphertext/MIC
vectors in `tests/switch2-ccm-vectors.json` were generated independently using
Windows .NET `AesCcm` with the synthetic key 00..0f and IV a0..a7.
Both nonce directions, packet counters 0..5 and FIFO wrapping are checked.
The largest encrypted fragment uses 37 of the stock 40-byte FIFO entry, with
untouched guard bytes. No buffer enlargement is applied.

A further test uses the public example pairing exchange to obtain an LTK,
executes stock session-key derivation at 0x16c34 (including the SDK AES primitive
at 0x16ba4), and queues a complete encrypted input report using that key.
`tests/switch2-session-vector.json` independently checks the derived key and
three encrypted fragments using Windows .NET AES/CCM. The SKD and IV are synthetic.
The real LL_ENC_REQ/LL_START_ENC state machine is not executed by that test;
the direct derivation test does not execute key lookup callbacks; the full
LL callback test described below adds that coverage.
The fixture files contain no keys taken from a user's paired device.

## Compiled pairing core (not installed)

`firmware-src/switch2-pairing.c` implements the four pairing subcommands,
strict lengths/order, response byte order, explicit RAM commit and pending-key
reset. Its AES call uses the original SDK primitive at 0x16ba4.
`tools/build_switch2_pairing.py --compiler PATH_TO_TC32_GCC` builds it into
`build/switch2-pairing/pairing-prototype.bin` plus a JSON symbol map. The current
compiled pairing, GATT adapter and bond-journal code and constants occupy 4208 bytes. Address 0x1a000 is a test link
address only; no production placement/reservation or installable image is made.

`tools/check_switch2_pairing.py --backup FULL_CONTROLLER_BACKUP` executes the
compiled instructions in an expanded interpreter-only image and compares
results/state against the Python model. It covers the captured handshake,
challenge replay, disconnects, interrupted re-pairing, retention of committed
RAM keys, and 300 deterministically mutated/truncated requests. The current
run has 92 accepted and 220 rejected requests. Rejected requests leave context
and output guards unchanged. That direct-core checker does not exercise flash or GATT; the additional
checks below execute those paths against modeled peripherals. No production
hook is installed. Its 65-byte context and output buffer are supplied by
the test harness; their production RAM placement is unresolved.

## Compiled GATT adapter and persistent journal

Build the table with `python -m tools.build_switch2_gatt`, after compiling the
pairing core. It creates `build/switch2-pairing/gatt-prototype.bin` (1880 bytes)
at the test address 0x1c000, using the actual Telink 24-byte attribute ABI and
factory Pro 2 handles through 0x0030. The table is registered only in interpreter
memory. Input has a RAM backing report, command handles 0x0014 and 0x0016 invoke the
compiled callback, and command replies use notifications on 0x001e. Unknown
auxiliary values, vibration payloads, prefixed combined writes and firmware-update writes remain
unimplemented and are denied. This is not a complete console-compatible profile.

`python -m tools.check_switch2_gatt --backup FULL_CONTROLLER_BACKUP` executes
stock table registration at 0x13628 and the ATT server at 0x138c0. The harness
copies the verified stock MTU-response template from 0x18198 into its normal
RAM location and supplies the local MTU configuration. The real ATT server
negotiates MTU 66, handles CCCD writes, dispatches the four pairing requests to
compiled C, and queues replies through the stock transmitter. Pending replies
are retried after queue saturation; a second request cannot overwrite them.
Disconnect clears subscriptions and pending pairing without losing the committed
bond. The main-loop call to the retry poll and production boot/profile hooks
are not installed.

`firmware-src/switch2-bonds.c` implements a two-sector append journal, exercised
only through modeled flash callbacks. Candidate sectors 0x3d000/0x3e000 are
blank in the inspected full backup and have no direct address literals in the
stock app. They adjoin, but do not overlap, the Xbox journal ending at 0x3d000
and the update record starting at 0x3f000. This is not proof against every
computed-address access; physical installation must verify/reserve the layout.

Each 64-byte record contains a version, sequence, profile identity, up to two
hosts, LTK, CRC16 and a final commit byte. The body is written and read back
before that byte is programmed. A full journal reclaims the sector containing
no newest valid record. With no valid record, unknown full data is never erased.
Sequence overflow is refused. A bad or incomplete newest record falls back to
the preceding valid record. Profile identity is checked when loading.

`python -m tools.check_switch2_bonds --backup FULL_CONTROLLER_BACKUP` executes
the compiled scanner, save and load routines. Tests cover cold RAM reload,
65 sequential program-byte cut boundaries, five partial sector-erase boundaries,
CRC corruption, wrong identity, full-sector reclamation, sequence saturation
and unknown-data preservation. These fault cases do not model every possible
physical flash failure or measure write/erase latency during a 5 ms radio link.

The compiled ATT callback sends final pairing success only after a verified
journal commit. On a silent failed write it restores the previous committed
RAM state and retains the pending exchange for retry. A repeated finalization
ACK does not rewrite flash. The integration test verifies both behaviors and
loads the new key into fresh RAM after a simulated cold restart. No actual
controller flash is read or written by these checks beyond reading the backup
file explicitly passed on the command line.

## Scalar initialization and live input

`firmware-src/switch2-input.c` now compiles into the same offline TC32 prototype.
It implements command 0x07/subcommand 1, command 0x10/subcommand 1 (a fixed
compatibility identity for the emulated pre-headset Pro 2 profile), and command
0x0c/subcommands 1..5 (feature info/mask/clear/enable/disable). That compatibility
identity is not the actual 2C firmware version. Only buttons and sticks are
advertised as supported. IMU, mouse and vibration are not claimed implemented.
Enable/disable requires prior mask initialization; disconnect clears feature
state. The observed initialization uses characteristic handle 0x0016, so that
handle and 0x0014 both dispatch supported commands to the compiled callback.
Command ID 0x16 is distinct from handle 0x0016; its observed startup
reply is now implemented as described below. Unsupported commands do not receive fabricated success responses.

`y_input_send` builds a 63-byte report from normalized buttons, four 12-bit
coordinates, a 0..9 battery level and two power flags. It requires enabled
features, input subscription and MTU 66. A pending command reply has priority.
The counter advances only when stock BLE enqueue accepts the report; rejection
leaves both counter and readable input shadow unchanged. The ATT input value
reflects the last accepted report. Missing motion/NFC/audio fields are zero.

`y_input_from_controller` adapts the existing processed word at 0x84342c,
calibrated stick registers, battery percentage at 0x843415 and external-power/
charge-latch state. Face buttons follow Nintendo positions (2C bottom A sends
Nintendo B, right B sends A); stock remaps and digital trigger thresholds are
already represented in the processed word. L4/R4 become GL/GR. No Capture or C
button is invented. Axis polarity follows the existing D-input packer and is
not verified on a Switch 2. The 16-bit midpoint values 32767/32768 both become
2048; battery percentage is quantized upward into nine nonzero levels, with
0% mapped to level 0. Charging is inferred from external power and the stock
not-full latch, not an additional charger-status measurement.

`python -m tools.check_switch2_input --backup FULL_CONTROLLER_BACKUP` executes
these compiled paths through stock ATT registration, negotiated MTU, input CCCD
and real TX routines. It checks both command handles, feature gating, the fixed
identity, five normalized reports, counter wrapping, queue rejection, invalid
inputs, all 19 mapped physical button bits, eight battery boundaries and stick
endpoints. A 63-byte ATT read is also reassembled from the real queued L2CAP
fragments: stock ATT queues long responses directly rather than returning a
short response pointer. Calls are driven by the test harness; no main-loop hook
or radio reporting cadence is installed.

## Full LL encryption procedure with saved-host lookup

`firmware-src/switch2-security.c` adds profile-local connection and LTK callbacks.
The SDK connection callback provides the received LL header and InitA; the code
copies its host address and clears subscriptions/pending feature state. An LTK
is supplied via the original SDK function 0x130d8 only if the active connection
handle and one of the two saved host addresses match. Unknown/disconnected
peers get the SDK's negative reply (0x13114); a different connection handle is
refused without touching key state. The custom bond is selected by host address;
EDIV/Rand are not stored as SMP lookup identifiers. Callback registration uses
0x121bc and the SDK connection callback slot. No production startup invokes
this registration yet; the callback lifecycle is reserved for the Y profile.

`python -m tools.check_switch2_security --backup FULL_CONTROLLER_BACKUP`
first executes compiled pairing and journal save, then loads that journal into
fresh RAM. For each saved host it executes initial 5 ms link setup with the
candidate validator and the compiled connection callback, followed by the real
LL control handler at 0x125c0:

1. LL_ENC_REQ generates the peripheral SKD/IV and queues LL_ENC_RSP.
2. The compiled LTK callback supplies the saved key to stock 0x130d8.
3. After modeled queue consumption, stock 0x1248c queues LL_START_ENC_REQ and
   derives/installs the session key using the original SDK AES routines.
4. A separately generated encrypted LL_START_ENC_RSP is decrypted and its MIC
   checked by stock 0x16d78. The stack queues its encrypted response and reaches
   its encryption-complete state.
5. A full input report is encrypted using the correctly advanced packet counters.

`tests/switch2-ll-vectors.json` contains independent Windows .NET AES-CCM
results for the synthetic session key, both control directions, and the input
fragments after handshake counter advancement. Tests also verify the LL reject
packet for an unknown host, a mismatched handle, clearing the active peer on
disconnect, and rejection of a corrupted control-packet MIC.

The test executes LL and profile code, not a model of their state transitions.
AES peripheral operations, hardware RNG, application event delivery and actual
TX consumption are explicit harness boundaries. RNG values are fixed only in
the Python harness; the compiled profile continues to use the original SDK RNG.
Radio interrupts, link-layer retransmissions and over-the-air timing are not
run. Scratch RAM/flash locations are still not production allocations.
The offline linker now preserves the function-pointer mode bit in absolute
relocations; callback pointers are checked after real SDK registration.

## Read-only calibration/identity map and larger command replies

`firmware-src/switch2-memory.c` implements command 0x02/subcommand 0x04 against
a virtual map, never by reading a requested physical address. Requests are
limited to 1..64 bytes wholly inside 0x13000..0x13160 or 0x1fc000..0x1fc080.
Overflowing requests, other ranges, writes and erases are refused. Responses
contain the requested address/length and the virtual bytes. No bootloader,
actual settings, actual calibration, MAC sector or pairing-journal bytes are
exposed via this interface.

The virtual identity uses a synthetic `U2` serial derived from the supplied
profile address and Pro 2 compatibility VID/PID. The two nine-byte stick records
encode center (2048,2048), positive travel (2047,2047) and negative travel
(2048,2048), matching the adapter's already calibrated 12-bit report domain.
These are derived output-domain calibration parameters, not another pad's raw
stick calibration. User-calibration pages return 0xff, indicating no override.
Unknown factory fields use explicitly labeled public reference compatibility
values; their semantics and console acceptance are not proven. No gyro/motion
capability or measured calibration is invented for the 2C.

A 64-byte read needs an 80-byte Nintendo reply, requiring ATT MTU >=83.
An earlier harness used reply RAM 0x843ec0..0x843f10 and profile RAM
0x843c00. The boot-memory audit below found these unsuitable for hardware:
the current prototype uses reclaimed RAM-code tails instead.
The C linker permits up to two prototype sectors and the separately generated
GATT table moves to 0x1c000 to avoid overlap as initialization code grows.
These remain interpreter placements, not production reservations. In particular,
stack/RAM layout must be established before any hardware image is constructed.

`python -m tools.check_switch2_memory --backup FULL_CONTROLLER_BACKUP` executes
the original ATT server with negotiated MTU 83 and the compiled callback. It
reassembles an 80-byte reply from four real L2CAP TX fragments; checks buffer
canaries and neighboring profile state; tests retry of an 80-byte busy reply;
decodes both calibration records and confirms endpoints; replays six console
read windows; and verifies uninitialized user calibration and rejection of
physical/out-of-range/overflow/write requests. A request at MTU 66 is refused
before generating an oversized response. No physical flash operation occurs.

Additional calibration-format reference:
- https://github.com/mlstr0m/switch2bridge-macos (factory calibration decoding)
- https://github.com/ndeadly/switch2_controller_research/blob/master/memory_layout.md

## Remaining captured startup commands and replay coverage

The compiled command dispatcher now implements the observed command 0x16/1
24-zero-byte reply and command 0x11/1 and 0x11/3 replies. Their undocumented
payloads are explicitly compatibility constants, not measured 2C state.
Command 0x09/7 validates and retains a four-bit logical player LED pattern;
it does not write Home-ring/GPIO hardware. The logical pattern resets on
disconnect. The bootstrap characteristic at 0x0005 and descriptor 0x0010 now
have two-byte writable RAM values handled by the actual SDK ATT server. Their
observed init values (01 00 and 85 00) are stored, not interpreted as measured
radio cadence. Both reset on disconnect. The callback preflights the real
32/37-byte response sizes before changing pending state at small MTUs.

`python -m tools.check_switch2_startup --backup FULL_CONTROLLER_BACKUP` replays
21 supported startup operations through stock ATT and compiled C: bootstrap,
identity/calibration reads, the four pairing commands and durable commit,
logical player assignment, feature mask/enable, observed status commands,
descriptor write, input subscription and the first register-derived input
report. Five malformed/unsupported start commands are checked for no state
mutation. The trace is based on the published Pro Controller 2 initialization
sequence, with the published challenge response corrected to the request form
already verified by the captured pairing exchange.

Two vibration operations are deliberately tested as unsupported, not counted
as successful replay steps: command 0x0a/2 connection sample and 0x0a/8 raw
vibration data. The raw format is documented as incompletely understood;
there is no synthetic success ACK for an effect we do not render. Therefore
the checker reports `startup_replay_complete: false`. The replay is not a
real-console test, and LL encryption is exercised by the separate security
checker rather than this unencrypted ATT replay. Remaining vibration support,
startup selection and hardware validation must be completed before claiming
an operational Y mode.

## Boot RAM audit and reclaimed-tail layout

The boot literals establish initialized data at 0x842e00..0x842fac, BSS at
0x842fb0..0x843cf0, and the main stack top at 0x844000. The earlier scratch
profile at 0x843c00 overlapped BSS; input/reply scratch buffers above it occupied
the stack area. Earlier protocol tests were isolated harness tests and did not
prove those scratch addresses suitable for a hardware image.

`tools/switch2_layout.py` now reserves 237 bytes across four existing dead-code
tails, totaling 240 bytes. It checks every region for overlap and confines them
below the instruction-cache boundary at 0x842500. Prerequisite blob sizes and
SHA-256 hashes are checked, so a future change requires a new layout audit.

| Reservation | Address | Bytes |
| --- | --- | --- |
| Pairing context | 0x840070 | 65 |
| Reply buffer | 0x841740 | 80 |
| Last input shadow | 0x841790 | 63 |
| Active peer | 0x8412c4 | 6 |
| CCCDs | split across the remaining aligned tail words | 12 |
| Pending reply, descriptor, counters, flags, bootstrap | 0x8424f4..0x842500 | 11 |

The tails come from the known dead watchdog span, scan-hook padding, stall-hook
padding and the RAM housekeeping span. To free the last span, its existing
44-byte `led.bin` housekeeping routine moves to prototype flash 0x1c800, while
an eight-byte trampoline retains the original entry at 0x1738. Its PC-relative
motor-tick call is relocated; register literals and instruction behavior stay
the same. Tests compare its three register writes, one motor-tick call and stack
shape for multiple initial register values. No device LED hardware is changed
by these tests. Runtime interference and IRQ timing still need hardware checks.

The compiled-image harnesses apply this overlay before execution. Their stack
now starts at the real 0x844000 and is bounded below by BSS end 0x843cf0,
instead of the earlier synthetic 0x847f00 test stack. The radio context pointer
is recovered from boot data as 0x843ac8, rather than supplied stack scratch.
The interpreter stops on stack boundary violations and records peak use.

An 80-byte reply through ATT plus stock CCM encryption initially used 736 of
the available 784 stack bytes. Moving the 65-byte pairing rollback snapshot
into a non-inlined commit-only function reduces that tested path to 664 bytes,
leaving 120 bytes before BSS. Plain GATT and startup replay peak at 524 bytes.
These are measured harness paths, not a worst-case interrupt-stack guarantee;
radio ISR entry, interrupt nesting and real flash-driver frames remain untested.

The prototype build emits `ram-layout.json`, `housekeeping-relocated.bin` and
`housekeeping-trampoline.bin` alongside the compiled code. They are required
overlay artifacts, not a flashable package. Current C address definitions are
checked against the Python manifest by `tests/test_switch2_layout.py`.

## Remaining work

Integrate production startup/profile selection and RAM placement, the callback
lifecycle, and vibration-command handling for the remaining captured startup operations;
verify real radio timing at Nintendo's 5 ms interval and connection updates. Then test discovery,
pairing, reconnects and input on a real Switch 2. No Y option is exposed in the
application or simulator as a working firmware feature at this stage.

References (wire-format facts; implementation is independently authored):
- https://github.com/ndeadly/switch2_controller_research/blob/master/bluetooth_interface.md
- https://github.com/ndeadly/switch2_controller_research/blob/master/commands.md
- https://github.com/ndeadly/switch2_controller_research/blob/master/hid_reports.md
- https://csrc.nist.gov/CSRC/media/Projects/Cryptographic-Standards-and-Guidelines/documents/examples/AES_Core128.pdf
- https://doc.telink-semi.cn/doc/en/software/res/sdk/ble/b80_ble_single_connection_en/b80_ble_single_connection_en/

Additional implementation references:
- https://github.com/zhantss/ESP32-BLE5-NSController-Emulator/blob/master/main/src/gatt.c
- https://github.com/zhantss/ESP32-BLE5-NSController-Emulator/blob/master/patch/patch_nimble_lib.py

BR/EDR fallback: the TLSR8208 manufacturer lists BLE and proprietary 2.4 GHz,
not Classic support. No BR/EDR implementation or firmware option is claimed:
https://www.telink-semi.com/products/bluetooth-le/tlsr8208

### Staged profile initialization (offline)

`firmware-src/switch2-profile.c` now supplies an early identity stage and a late
GATT/security/advertising stage. Y derives a separate public address by XOR 4
on the high address byte (D keeps the factory address, X uses XOR 2). The early
stage loads only the journal matching that identity. The late stage forces
connectable undirected advertising, installs the custom callbacks and MTU 83,
and clears the old scan response. Unpaired startup advertises discovery; a
bonded restart advertises the first saved host.

Run `python -m tools.check_switch2_profile --backup <verified-controller-backup>`
after building the pairing and GATT prototypes. This executes the original SDK
MAC, advertising parameter, advertising data and scan response procedures with
the original 428-byte initialized data segment. It checks cold startup, a full
pairing exchange, journal save and bonded restart without modifying the saved
journal on restart. SPI access is modeled; no controller is opened or flashed.

The compiled core currently occupies 4488 bytes. These stages are **not hooked
into production startup**. Original SMP initialization must still be bypassed
for Y, call sites integrated, and advertising enable/radio behavior tested.
Successful offline checks do not establish console compatibility.

### Forced-Y startup overlay (offline)

`tools/switch2_startup_overlay.py` now patches an interpreter-memory copy of the
verified startup: the MAC call at 0xa138 invokes the Y identity stage, and the
block at 0xa184 invokes Y setup and skips original SMP journal initialization,
bond lookup and D/X advertising setup. Success rejoins stock code at 0xa21a,
which enables advertising through the real SDK procedure at 0x11598. An error
stops before advertising. Five expected original call targets are checked before
patching; mismatches are rejected.

The profile checker executes both patched call sites, including actual SDK
advertising enable with modeled register writes. It rejects any execution of the
old SMP journal routines, checks cold and bonded startup, checks the error path,
and rejects changed startup code. The test does not execute the entire firmware
boot, radio interrupts, or an over-the-air connection. This forced-Y overlay is
not production selection logic and is never written as a flashable image. Mode
selection, runtime input pumping and production integration still need work.

### Experimental flash image

`tools/build_switch2_trial.py --backup <complete-backup>` now builds a unit-specific
512 KiB restore image in `build/switch2-trial/controller-y-experimental-full.bin`.
It verifies the original 1.09 bank and blank extension area, installs the necessary
base recipes, reserves the 238th RAM byte for runtime Y selection, and links code
through 0x1d000. Holding physical Y at Bluetooth startup selects the new profile;
otherwise identity, bond initialization, input and Home dispatch to existing D/X
wrappers. Y selection is deliberately not persisted in this first trial.

The Y input hook retries pending command replies and reads physical controls. Its
application connected event sets the connection flag without requesting the old
D/X interval. The disconnect event clears Y peer/subscription/input state before
continuing original application disconnect handling. Original low-level Y LTK and
peer callbacks remain separate from those application events.

The builder requires `tools.check_switch2_trial` to pass before saving output.
This executes the final dispatch instructions, verifies D/X/Y identities, Y startup,
application callbacks and a physical-button report. It also uses the existing
restore planner/writer with simulated flash to install the full image and restore
the original backup byte-for-byte, preserving bootloader and configuration regions.
The normal explicit `restore backup --backup <full-image> --write` path supports
these additional sectors; the raw 94208-byte application image is not a backup.
No physical flashing is performed by the build or checks. Hardware/radio, full boot
interrupt behavior and Switch 2 compatibility remain untested. The trial is not a
standard recognized recipe and must not be described as confirmed console support.
See `build/switch2-trial/INSTRUKCJA.txt` for this local trial's installation and rollback.


### Toolkit tweak integration

`bt_switch2` is now a normal optional recipe, displayed as **Bluetooth Y: Switch 2
Pro Controller (experimental)** in Tweaks. Selecting it enables its existing
Bluetooth mode selection, Dimmer LEDs, Controller settings and Processor rest
dependencies. Installation preserves the person's other choices and names. The
same firmware identifier recognizes the Y recipe after install. Unchecking it
and installing again removes its code and restores ordinary D/X hooks.

Application reads now include 0x6000..0x1d000. The main bootloader erase/write
sequence still ends at 0x1a000; the three optional sectors use the existing
verified sector write/readback path. The planner writes only changed extension
sectors and erases them on uninstall, stock restore and backup restore. It does
not touch the bootloader, calibration, MAC or settings sectors for this tweak.

The ten `switch2-*.bin` recipe blobs are generic code/data, without unit-specific
backup contents. Rebuild the C/GATT prototype, then run
`python -m tools.build_switch2_recipe --backup <verified-full-backup>` and regenerate
stock hashes with `python -m tools.make_constants --backup <verified-full-backup>`
and the blob manifest when changing the payload. The separate full-backup trial
file is no longer needed for ordinary installation through the toolkit.

`tests.test_switch2_recipe` covers dependency checks, optional tweaks and names,
legacy app identification, modified Y-code rejection, normal patch installation,
uninstall and stock restore on simulated flash. Physical and console support
remain unconfirmed. This integration does not add a simulated Switch 2 host.


### Hardware report: Y remained D/X

Windows inspection showed an active D-input HID profile at an address ending
B031, while the previous D-input identity ending B030 was disconnected. That
observation does not establish that the prior bond key was erased. Y should have
a different high-byte identity and the custom Pro 2 GATT services, not the old
D-input HID service.

Y selection is now latched at 0xa4b8 before the existing bt_start/radio callback,
and consumed later at the MAC and bond initialization stages. The selector
preserves r0-r3, including the radio callback originally passed in r3. The trial
checker executes the compiled bt_start function, models RAW being cleared at the
radio boundary, and checks selection from both saved D/X modes. Earlier checks
only seeded RAW directly before the identity hook, which missed this startup
ordering. The fix remains unverified on hardware. Home+Y during operation is
still not a supported live mode switch; use Y+Home from complete power-off.


### Retired diagnostic: forced profile

The earlier `bt_switch2_force` diagnostic was removed entirely. Its
three override blobs, recipe and simulated forced-start tests are no longer
included. The supported profile is the standard Y chord and `bt_switch2` tweak.


Y disconnection no longer tails into original a070, whose renewal branch can call
b89c and persist an address-renew request. It clears Y transient state and the
application connection flag, then reenables the existing undirected advertisement
through the SDK. The trial checker asserts no original disconnect handler call,
no flash writes, cleared subscriptions/peer state and advertising enabled. The
physical root cause of Y failing to remain active is still unresolved.


### Fresh Y scan at identity selection

The ordinary Y identity hook now runs the original physical matrix scan (c7a4)
after radio initialization and checks the fresh RAW Y bit. It preserves the
factory-address pointer and updates the active flag from that scan. The forced
path remains a separate diagnostic. The existing early startup latch is no
longer authoritative for identity selection.

The checker now executes the stock radio initializer 10e34, with modeled register
storage, and executes the real c7a4 matrix decoding, with modeled GPIO column
values. It checks late Y after an empty early sample, released Y after a stale
early sample, both saved D/X modes and independent B/X keys. It still models the
GPIO electrical behavior and does not reproduce an entire physical boot. This
change is awaiting hardware confirmation; the exact cause of the previous
ordinary-selection failure remains unproven.


### Y pairing button without stock identity renewal

The original pairing-button call at d156 still invoked b8bc. That path can call
b89c, persist an address-renew request and reboot, after which an unheld Y returns
to saved D/X. The Y recipe now dispatches this call separately: D/X keeps b8bc,
while Y clears transient ATT/pairing state, publishes the discovery advertisement
under its current MAC and requests a normal BLE disconnect when connected. It
does not erase the custom bond journal or renew the original factory identity.

The instruction checker executes the original SDK advertising and disconnect
routines, verifies discovery data against the golden packet, checks the LL
termination reason, unchanged MAC/committed bonds, no flash writes or erases, and
unchanged ordinary D/X dispatch. Physical confirmation is pending. The disconnected automatic shutdown is normal stock behavior. The current
implementation persists Y as described below.


### Pairing indication and diagnostic option removal

Y startup explicitly selects the stock slow-search indicator. Pressing pairing
sets a dedicated transient flag at 0x8424f5 and stock indicator mode (4,3). A
requested disconnect retains that flag so the disconnect callback keeps the fast
pairing indication. Connection clears the flag and sets indicator mode (4,1);
ordinary disconnection uses mode (4,2). The original d36c/d2a0 LED driver is
executed in the test and fast/slow GPIO transition counts are compared. RAM
reservation is now 239 of the 240 reclaimed bytes.

Force Y has been removed from the recipe builder and UI/API. Its prior
experimental code is not recognized as an installable or migratable tweak.
The user confirmed automatic shutdown while disconnected is normal hardware
behavior; no change to that behavior is planned.

Rumble remains unsupported in Y. It needs its own Nintendo output handling and
translation to the 2C motors, separate from D/X haptics. References reviewed:
https://github.com/ndeadly/switch2_controller_research/blob/master/commands.md
https://github.com/novakpetya/linux-switch2/blob/main/PROTOCOL.md
No rumble capability or successful rumble-command response is advertised by this
update. Physical confirmation of the pairing indication is pending.


### Persistent Y and Switch2Connect v3.1

The stock settings byte at 0x843915 now reserves bit 2 for Bluetooth Y, alongside
USB D (bit 0) and Bluetooth D (bit 1). Home alone restores the last Bluetooth
mode. Holding Home with X/B/Y for three seconds saves the selected mode and
restarts automatically. Startup chords are blocked until released. USB and
Home+L4/R4 retain their existing dispatch. The original settings journal is used;
no separate configuration erase region is introduced.

Switch2Connect v3.1 uses replies on 0x1a and common input on 0x0a rather than
the native 0x1e/0x0e path. Both paths are supported. Feature mask 0x94 gates
subsequent feature changes without disabling the default buttons and sticks.
Common reports include the real millivolt reading and a 32-bit counter. Counter
scratch is used only outside an active pairing exchange, with interrupt guards.
Unsupported USB initialization, vibration and NFC return explicit status 4.
The exact v3.1 initialization sequence, common reports, dual subscriptions and
a pairing interrupt during notification are checked against compiled code.
These checks do not establish physical WinUHid, Steam or console compatibility.

The initialize() method alone does not perform the address/LTK exchange. The
discoverer subsequently calls pair() for discovery advertisements with a zero
reconnect address, before creating a virtual controller. This separate step is
now included in the compatibility test. A live BLE connection is possible
without pairing in Windows Settings; a steady ring does not prove that a virtual
controller has been created. Sensor and vibration support remain unimplemented.


### Windows service discovery correction

Direct uncached GATT discovery on the physical Y device timed out before command
initialization. Replaying Read By Group Type through the stock ATT server exposed
a malformed range: the empty final Generic Attribute service at handle 0x30
returned end handle 0. The SDK helper at 0x13638 returns zero when the next handle
is at or past the table count. The unused empty service is now omitted; all
implemented characteristic handles are unchanged. The final GAP service ends at
0x2f. The compatibility checker now walks primary services and rejects a zero or
backwards group end, in addition to exercising initialization and input. This
fix is not yet physically confirmed and does not establish Switch2Connect input
compatibility. Previous distributed payloads remain recognized for upgrading.


### Switch2Connect repeated host address correction

On physical hardware, uncached UUID-filtered discovery finds the Switch 2 service
at 0x08; full discovery on this Windows installation still merges its channels
into the auxiliary service. Filtered discovery matches Switch2Connect's Pro 2
path and permits command replies, an 80-byte identity reply, calibration reads
and 63-byte common input notifications. This does not prove the virtual driver
works.

Switch2Connect v3.1 discoverer.add_controller() invokes Controller.pair() after
initialize() for a discovery advertisement, before virtual_device_ready. pair()
sends two identical local-adapter addresses in 15:01. The previous implementation
incorrectly required distinct addresses. Physical noncommitting probes confirmed
a single address and two different addresses are accepted, while the duplicate
list is rejected. Duplicate slots are now accepted without changing ordering,
key derivation, peer matching or durable commit. The model and compiled checker
replay the exact four Switch2Connect pair commands and verify the same key and
two slots after a cold journal reload. Physical journal writes were not performed
during diagnosis. The 20-byte 0a:08 vibration init still times out on this hardware;
individual init failures are tolerated by the client and vibration is unsupported.


### Fragmented incoming pairing packets

A logged run of the unmodified Switch2Connect v3.1 executable reaches
initialize_succeeded and pair_started, then times out at Controller.pair line
4256 (15:04 LTK1). It disconnects and retries, explaining the periodic ring
changes. Original startup a178..a17c calls 1478c with buffer 8436c0, size 32.
The SDK reassembler at 14798 rejects a completed packet when total bytes are
at least the buffer capacity. SET_MAC fits (31 bytes); LTK1/LTK2 need 34,
and the unsupported 0a:08 init needs 37. Previous direct ATT tests bypassed this
receive path.

The Y-only call hook at 14a62 now guards the stock reassembler. At a fragmented
ATT start it registers the idle 80-byte reply buffer, with strict bounds, new
start reset and pending-reply exclusion. The assembled request begins nine bytes
after the response origin; existing forward-copy command handlers are safe with
this overlap. No extra RAM allocation is introduced and the native input shadow
remains intact. Disconnect clears a partial receive. D/X delegates to the original
reassembler. Reply polling and input generation do not modify a partial RX buffer.

check_switch2_rx reproduces the stock 32-byte failure, then executes 27-byte LL
fragments through the patched 14a58 L2CAP/ATT dispatch, pairing, persistent commit
and cold key reload. It also verifies an explicit unsupported reply for fragmented
vibration init, queue-pressure preservation, disconnect cleanup and buffer bounds.
The measured interpreter stack peak is 540 bytes (244 bytes before stock BSS);
real interrupt nesting and radio timing are not established by this check.
No physical firmware or pairing journal was written during diagnosis.


### Vertical stick direction in Y

Physical Switch2Connect operation is confirmed by the user after the RX fix.
Both sticks have correct horizontal directions but reversed up/down. The Y
physical adapter now reverses vertical values as well as the existing horizontal
conversion. Neutral 32767/32768 still maps exactly to 2048. The compiled checker
exercises both endpoints of all four axes independently in native and common
reports. Other transport profiles and calibration pages are unchanged. Physical
confirmation of this direction correction remains pending.
