import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import dongle_rf_probe as p


def item(mode):
    return SimpleNamespace(mode=mode, info=object())


def test_select_rejects_wrong_transport_and_ambiguity():
    for found in ([], [item('dongle')], [item('cable')], [item('dongle_paired'), item('dongle_paired')],
                  [item('dongle'), item('dongle_paired')]):
        with pytest.raises(RuntimeError):
            p.select_dongle(found)
    assert p.select_dongle([item('dongle_paired')]).mode == 'dongle_paired'


def test_flash_identity_required(monkeypatch, tmp_path):
    calls = []
    def fake(ch, addr, n, tries=1):
        calls.append(addr)
        return b'\0' * n
    monkeypatch.setattr(p.app, 'read', fake)
    with pytest.raises(RuntimeError, match='identity'):
        p.capture(None, seconds=1, interval=.2, output=tmp_path/'test.jsonl')
    assert calls == [p.FLASH_SIGNATURE]


def test_unsupported_ram_fails_closed(monkeypatch, tmp_path):
    calls = []
    def fake(ch, addr, n, tries=1):
        calls.append(addr)
        return b'\xff'*n if addr == p.FLASH_SIGNATURE else None
    monkeypatch.setattr(p.app, 'read', fake)
    with pytest.raises(RuntimeError, match='RAM read'):
        p.capture(None, seconds=1, interval=.2, output=tmp_path/'test.jsonl')
    assert calls == [p.FLASH_SIGNATURE, p.RF_PACKET]


def test_records_different_snapshots_only(monkeypatch, tmp_path):
    packets = iter([b'A'*32, b'A'*32, b'B'*32])
    def fake(ch, addr, n, tries=1):
        return b'\xff'*16 if addr == p.FLASH_SIGNATURE else next(packets)
    monkeypatch.setattr(p.app, 'read', fake)
    times = iter([0.0, 0.0, .2, .4, .6])
    output = tmp_path/'samples.jsonl'
    n = p.capture(None, seconds=.6, interval=.2, output=output, clock=lambda: next(times), pause=lambda s: None)
    lines = [json.loads(x) for x in output.read_text().splitlines()]
    assert n == 2
    assert [r['hex'] for r in lines] == [('41 '*31+'41'), ('42 '*31+'42')]


def test_ff_is_not_reported_as_success(monkeypatch, tmp_path):
    def fake(ch, addr, n, tries=1):
        return b'\xff' * n
    monkeypatch.setattr(p.app, 'read', fake)
    times = iter([0., 0., .2, .4])
    with pytest.raises(RuntimeError, match='Only FF'):
        p.capture(None, seconds=.4, interval=.2, output=tmp_path/'ff.jsonl', clock=lambda: next(times), pause=lambda s: None)
