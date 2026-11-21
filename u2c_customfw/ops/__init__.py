# SPDX-License-Identifier: GPL-3.0-or-later
"""User-level operations: find devices, back up, patch, restore, report."""

# Recipes kept in the code but never offered or shown by the app: still recognised, but out of every list and summary.
HIDDEN_TWEAKS = frozenset({"ble_interval", "battery_log", "battery_table"})
IN_EVERY_INSTALL = ("ble_interval", "battery_table")   # hidden, added to any install with tweaks (the page does the same)


def shown(keys) -> list:
    return [k for k in keys if k not in HIDDEN_TWEAKS]


def with_hidden(keys) -> list:
    """The chosen tweaks plus the hidden ones that come with them: those in every install, and the battery log with
    Processor rest."""
    keys = list(keys)
    keys += [k for k in IN_EVERY_INSTALL if keys and k not in keys]
    keys += ["battery_log"] if "cpu_rest" in keys and "battery_log" not in keys else []
    return keys


def update_available(state) -> bool:
    """Firmware this app recognises whose tweaks an install of the same choices would change: one of them is an earlier
    version's, or a hidden tweak that comes with them is missing. Every change to a tweak must show up here."""
    return state.known and bool(shown(state.recipes)) and (state.outdated or state.older or not set(with_hidden(state.recipes)) <= set(state.recipes))
