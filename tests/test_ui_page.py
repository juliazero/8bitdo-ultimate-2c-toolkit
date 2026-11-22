# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from u2c_customfw.web import server


class UIPageTest(unittest.TestCase):
    def test_required_assets_are_inline_and_ordered(self):
        page = server.page_html()
        for name in ('startup.js','diagnostics.js','settings.js','simulation.js','app.js'):
            self.assertNotIn(('<script src="/static/' + name + '"></script>').encode(), page)
        self.assertNotIn(b'<link rel="stylesheet"', page)
        self.assertLess(page.index(b'window.u2cStartupReady = false'), page.index(b'function windowsTick'))
        self.assertLess(page.index(b'function settingsLive'), page.index(b'async function refresh('))
        self.assertIn(b'window.u2cStartupComplete();', page)
        self.assertIn(b'id="startup-status"', page)
        self.assertIn(b'id="fw-title">Starting interface', page)

    def test_script_strings_cannot_terminate_the_html_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            names = ('startup.js','diagnostics.js','settings.js','simulation.js','app.js')
            (root/'index.html').write_text(''.join('<script src="/static/'+name+'"></script>' for name in names),encoding='utf-8')
            (root/'style.css').write_text('',encoding='utf-8')
            for name in names:
                (root/name).write_text('const sample="</script>";' if name=='app.js' else '',encoding='utf-8')
            with patch.object(server,'STATIC',root):
                page=server.page_html()
            self.assertIn(b'const sample="<\\/script>";',page)
            self.assertEqual(page.count(b'</script>'),len(names))
