# Releasing a beta (GitHub Actions)

Current beta: **1.3.0b1**; tag: **`v1.3.0b1`**.

1. Make changes and run tests. The version must match in `pyproject.toml`,
   `u2c_customfw/__init__.py` and the version test (`tests/test_smoke.py`).
2. Validate: `python3 tools/check_release.py --tag v1.3.0b1`.
3. Push the commit first. In the target repository, create an annotated tag
   for the commit containing the workflow and the version bump:

   ```bash
   git tag -a v1.3.0b1 -m "Firmware Toolkit 1.3.0 beta 1"
   git push origin HEAD
   git push origin v1.3.0b1
   ```

4. GitHub Actions → **desktop packages** builds Windows x86_64, Linux x86_64
   AppImage, macOS arm64 and macOS x86_64. The prerelease job runs only once
   **all four builds succeed**, verifies checksums and creates a single GitHub
   Pre-release with all four packages. If a build fails, fix it and create a
   **new beta tag** (e.g. `v1.3.0b2`); avoid force-moving published tags.
5. Confirm the release is marked **Pre-release**, assets and checksums are
   present, and document any limitations in the release notes. Manual workflow
   runs and pull requests only upload Actions artifacts; they do not publish.

GitHub's `GITHUB_TOKEN` needs `contents: write` for the publishing job (already
requested by the workflow); repository Actions settings must permit it. No
personal access token or additional release secret is needed. These beta builds
are **not signed/notarized**. A tag alone is not a firmware hardware test.

## Stable releases / Homebrew

Beta tags intentionally do **not** update the Homebrew formula/cask. Preserve
the existing stable-release workflow and update Homebrew only when publishing a
stable release. `releases/latest` intentionally excludes prereleases.

The manual macOS packagers remain available:

```bash
bash macos/build.sh arm64
bash macos/build.sh x86_64
```

Windows can be built with `./windows/build.ps1` in PowerShell; Linux AppImage
with `./linux/build-appimage.sh /path/to/appimagetool`. See
[desktop build details](desktop-builds.md).
