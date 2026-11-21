# SPDX-License-Identifier: GPL-3.0-or-later
"""Build on the target OS: Windows ZIP or Linux AppImage (Python 3.12+)."""
import argparse
import ctypes.util
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from u2c_customfw import __version__


def run(*args, **kwargs):
    subprocess.run([str(a) for a in args], check=True, **kwargs)


def simulator_shortcut(folder):
    """Portable double-click launcher, independent of extraction directory."""
    shortcut = Path(folder) / 'Simulator.cmd'
    shortcut.write_bytes(b'@echo off\r\nstart "" "%~dp0Firmware Toolkit.exe" --simulator %*\r\n')
    return shortcut


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--appimagetool", type=Path, help="Linux: path to appimagetool")
    args = parser.parse_args()
    if sys.platform not in ("win32", "linux"):
        parser.error("Use macos/build.sh on macOS")
    if sys.platform == "linux" and not args.appimagetool:
        parser.error("Linux builds require --appimagetool /path/to/appimagetool")
    import webview  # Install the desktop extra before building.
    from PIL import Image
    work = ROOT / "build" / "desktop"
    work.mkdir(parents=True, exist_ok=True)
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    icon = Image.open(ROOT / "macos" / "AppIcon.icns").convert("RGBA")
    icon.thumbnail((256, 256))
    icon.save(work / "desktop-icon.png")
    icon.save(work / "desktop-icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
               "--name", "Firmware Toolkit", "--distpath", str(work / "frozen"),
               "--workpath", str(work / "pyinstaller"), "--specpath", str(work),
               "--paths", str(ROOT), "--collect-all", "u2c_customfw", "--collect-data", "webview",
               "--exclude-module", "tkinter",
               "--add-data", f"{ROOT / 'DISCLAIMER.md'}:.",
               "--add-data", f"{ROOT / 'LICENSE'}:.",
               "--add-data", f"{work / 'desktop-icon.png'}:."]
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        command += ["--add-data", f"{python_license}:licenses/python"]
    # Some managed Python distributions add a trust policy file used by ssl.py.
    # Preserve it when present rather than weakening their certificate policy.
    import ssl
    trust_policy = Path(ssl.__file__).with_name("codex-rejected-cas.pem")
    if trust_policy.is_file():
        command += ["--add-data", f"{trust_policy}:."]
    if sys.platform == "win32":
        command += ["--windowed", "--icon", str(work / "desktop-icon.ico"), "--hidden-import", "hid",
                    "--hidden-import", "webview.platforms.winforms",
                    "--exclude-module", "PySide6", "--exclude-module", "PyQt5", "--exclude-module", "PyQt6",
                    "--add-data", f"{work / 'desktop-icon.ico'}:."]
    else:
        # pyusb loads libusb through ctypes, so PyInstaller cannot discover it automatically.
        library = ctypes.util.find_library("usb-1.0")
        if not library:
            raise RuntimeError("Install libusb-1.0-0 before building")
        import PyInstaller.depend.bindepend
        resolved = PyInstaller.depend.bindepend.resolve_library_path(library)
        if not resolved:
            raise RuntimeError(f"Cannot resolve {library}")
        command += ["--add-binary", f"{resolved}:.", "--collect-all", "usb", "--collect-all", "evdev",
                    "--hidden-import", "webview.platforms.qt",
                    "--hidden-import", "PySide6.QtWebEngineWidgets", "--hidden-import", "PySide6.QtWebEngineCore",
                    "--hidden-import", "PySide6.QtWebChannel", "--exclude-module", "PyQt5", "--exclude-module", "PyQt6"]
    run(*command, ROOT / "u2c_customfw" / "desktop.py", cwd=ROOT)
    frozen = work / "frozen" / "Firmware Toolkit"
    executable = frozen / ("Firmware Toolkit.exe" if sys.platform == "win32" else "Firmware Toolkit")
    report = work / "smoke.json"
    report.unlink(missing_ok=True)
    # Windows GUI executables must be explicitly waited for.
    run(executable, "--simulator", "--smoke-test", report, "--smoke-ui", timeout=60)
    import json
    assert json.loads(report.read_text(encoding="utf-8"))["ok"]
    arch = platform.machine().lower()
    arch = {"amd64": "x86_64", "aarch64": "aarch64"}.get(arch, arch)
    stem = f"firmware-toolkit-{__version__}-{'windows' if sys.platform == 'win32' else 'linux'}-{arch}"
    if sys.platform == "win32":
        simulator_shortcut(frozen)
        artifact = Path(shutil.make_archive(str(dist / stem), "zip", frozen.parent, frozen.name))
    else:
        # Use a fresh directory: stale build files must never enter an AppImage.
        import tempfile
        with tempfile.TemporaryDirectory(prefix="appdir-", dir=work) as tmp:
            appdir = Path(tmp) / "FirmwareToolkit.AppDir"
            shutil.copytree(frozen, appdir / "usr" / "lib" / "firmware-toolkit")
            shutil.copytree(ROOT / "udev", appdir / "usr" / "share" / "firmware-toolkit" / "udev")
            shutil.copy2(work / "desktop-icon.png", appdir / "firmware-toolkit.png")
            (appdir / "AppRun").write_text('#!/bin/sh\nHERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"\nexec "$HERE/usr/lib/firmware-toolkit/Firmware Toolkit" "$@"\n', encoding="utf-8")
            (appdir / "AppRun").chmod(0o755)
            (appdir / "firmware-toolkit.desktop").write_text('[Desktop Entry]\nType=Application\nName=Firmware Toolkit\nExec=AppRun\nIcon=firmware-toolkit\nCategories=Utility;\nTerminal=false\n', encoding="utf-8")
            artifact = dist / f"{stem}.AppImage"
            run(args.appimagetool.resolve(), "--appimage-extract-and-run", appdir, artifact,
                env={**os.environ, "ARCH": arch})
        report.unlink(missing_ok=True)
        run(artifact, "--appimage-extract-and-run", "--simulator", "--smoke-test", report, "--smoke-ui", timeout=60)
        assert json.loads(report.read_text(encoding="utf-8"))["ok"]
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    artifact.with_name(artifact.name + ".sha256").write_text(f"{digest}  {artifact.name}\n", encoding="ascii")
    print(artifact)


if __name__ == "__main__":
    main()
