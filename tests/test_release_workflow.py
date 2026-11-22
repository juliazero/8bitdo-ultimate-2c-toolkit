# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline tests for a beta workflow, version/tag consistency and assets."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from tools import check_release


ROOT = Path(__file__).resolve().parents[1]


class ReleaseWorkflowTest(unittest.TestCase):
    def test_version_and_beta_tag(self):
        self.assertEqual(check_release.verify('v1.3.0b1'), '1.3.0b1')
        for incorrect in ['v1.2.0', 'v1.3.0', 'v1.3.0b2', '1.3.0b1']:
            with self.subTest(tag=incorrect), self.assertRaises(ValueError):
                check_release.verify(incorrect)

    def test_requires_all_four_artifacts_and_valid_sha256(self):
        version = check_release.version()
        filenames = [
            f'firmware-toolkit-{version}-windows-x86_64.zip',
            f'firmware-toolkit-{version}-linux-x86_64.AppImage',
            f'firmware-toolkit-{version}-macos-arm64.zip',
            f'firmware-toolkit-{version}-macos-x86_64.zip',
        ]
        with tempfile.TemporaryDirectory() as dirname:
            directory = Path(dirname)
            for name in filenames:
                data = ('test-' + name).encode()
                (directory / name).write_bytes(data)
                (directory / (name + '.sha256')).write_text(f'{hashlib.sha256(data).hexdigest()}  {name}\n')
            self.assertEqual(check_release.verify('v1.3.0b1', directory), version)
            (directory / (filenames[0] + '.sha256')).write_text('incorrect\n')
            with self.assertRaisesRegex(ValueError, 'invalid checksum'):
                check_release.verify('v1.3.0b1', directory)
            (directory / (filenames[0] + '.sha256')).unlink()
            with self.assertRaisesRegex(ValueError, 'missing/empty'):
                check_release.verify('v1.3.0b1', directory)

    def test_macos_uses_numeric_bundle_version_but_displays_beta(self):
        template = (ROOT / 'macos/Info.plist').read_text()
        script = (ROOT / 'macos/build.sh').read_text()
        swift = (ROOT / 'macos/main.swift').read_text()
        self.assertIn('@BUNDLE_VERSION@', template)
        self.assertIn('<key>U2CReleaseVersion</key><string>@VERSION@</string>', template)
        self.assertIn('bundle_version=', script)
        self.assertIn('U2CReleaseVersion', swift)

    def test_workflow_has_all_platforms_and_release_guard(self):
        workflow = (ROOT / '.github/workflows/desktop.yml').read_text()
        for text in ('windows-latest', 'ubuntu-22.04', 'macos-15', 'macos-15-intel',
                     'needs: build', 'tools/check_release.py --tag', '--prerelease',
                     '--latest=false', '--notes-file docs/beta-release-notes.md', 'contents: write', 'merge-multiple: true'):
            self.assertIn(text, workflow)
        self.assertIn('!github.event.release.prerelease',
                      (ROOT / '.github/workflows/homebrew.yml').read_text())


if __name__ == '__main__':
    unittest.main()
