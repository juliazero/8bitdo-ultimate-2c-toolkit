# Windows

Create the Python 3.12 virtual environment and install the desktop build extras
as described in [desktop builds](../docs/desktop-builds.md).

Run `powershell -ExecutionPolicy Bypass -File windows/build.ps1` to build the EXE
and portable ZIP in `dist/`. The EXE requires the WebView2 runtime; no drivers
are installed by this script.
