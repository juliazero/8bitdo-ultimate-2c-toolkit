# Linux desktop builds

The supported distribution workflow is an **AppImage** (or a system package),
not a project-specific installer. From the repository root, see
[desktop builds](../docs/desktop-builds.md) and run:

```sh
./linux/build-appimage.sh /path/to/appimagetool
```

The resulting desktop package bundles the Python runtime. Install the host's
[udev permissions](../docs/linux.md#udev-access) separately if needed. Neither
building nor launching an AppImage modifies udev rules. Run the Toolkit as the
logged-in desktop user, not as root.

For development from source, use a standard Python virtual environment as
explained in [Linux support](../docs/linux.md).
