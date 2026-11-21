# Switch 2 patch blob inventory

The active `bt_switch2` recipe uses **13** addresses. The 49 former files named
`-preindicator`, `-prememory`, `-prediscovery`, `-prehostlist`, `-prerx`,
`-prevertical`, and `-prerumble` were snapshots from local experimental builds,
loaded only through `Edit.accepts` for recognizing those earlier images.

They were removed deliberately. Old experimental firmware may now appear as
**unrecognized firmware**. This is preferable to silently treating unknown
machine code as an upgradable official version. Restore such devices from a
verified backup before applying the current patch. This cleanup does **not**
change the bytes written when installing the current `bt_switch2` tweak.

`MANIFEST` is generated from the remaining `.bin` files. The three
`switch2-force-*` blobs and the experimental `bt_switch2_force` recipe
have also been removed; only normal selectable Y remains.
