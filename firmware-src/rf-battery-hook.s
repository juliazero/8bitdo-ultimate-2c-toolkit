@ SPDX-License-Identifier: GPL-3.0-or-later
@ Experimental RF battery trailer. Replace the stock call at 0xa350 only.
    .text
rf_battery_hook:
    tjl rf_battery_pack
