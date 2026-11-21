# Switch 2 Pro (Y) rumble

The 2C has conventional ERM motors; this is an approximation of HD Rumble, not native HD Rumble playback. Switch2Connect 3.3 was observed to send 33-byte ATT 0x0012 reports with identical 16-byte actuator blocks. Separate LF and HF amplitudes in these duplicate reports map to strong and weak motors respectively. Different blocks, padded 42-byte reports, and combined rumble/command reports retain per-grip amplitude mapping. The duplicate-block heuristic cannot identify all possible hosts or infer physical grip intent from identical data.

The experimental 0x7e firmware diagnostics, direct motor tests and telemetry are removed. The full 63-byte shadow is restored, and one RAM byte is unallocated. The full 80-byte command reply capacity is unchanged. Host-side tests and USB battery diagnostics remain. Connection-time haptics sent by the host remain supported.

Regression checks: `python -m unittest tests.test_switch2_rumble tests.test_switch2_layout`; compiled GATT checks in `tools/check_switch2_rumble_wire.py`, `tools/check_switch2_input.py`, and `tools/check_switch2_diag_wire.py`.
