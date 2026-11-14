"""bca.liveeval.pins: the pinned binary is used unless something more explicit names one, and a pin that is not the
binary that was pinned fails closed instead of falling back to whatever the vendor installed since."""

import json
import os

import pytest

from bca.liveeval import pins
from bca.liveeval.vendor_base import IsolationError
from bca.liveeval.vendor_muse import MuseSubject


@pytest.fixture
def pinfile(tmp_path, monkeypatch):
    exe = tmp_path / "store" / "muse"
    exe.parent.mkdir()
    exe.write_text("#!/bin/sh\necho 'Muse Code 1.4.20 (1.4.20-R1)'\n")
    exe.chmod(0o755)
    f = tmp_path / "pins.json"
    f.write_text(json.dumps({"muse": {"version": "1.4.20", "path": str(exe), "sha256": pins.sha256(exe),
                                      "env": {"MUSE_RELEASE_INFO": "{}"}}}))
    monkeypatch.setattr(pins, "PINS_FILE", f)
    monkeypatch.delenv("BCA_NO_PINS", raising=False)
    monkeypatch.delenv("BCA_MUSE_BIN", raising=False)
    return exe


def test_the_pin_is_used_and_carries_its_environment(pinfile):
    s = MuseSubject("muse-spark-1.3")
    assert s.binary == str(pinfile) and s.pin_env == {"MUSE_RELEASE_INFO": "{}"} and s.cli_version == "1.4.20"


def test_explicit_binary_then_env_then_pin_then_path(pinfile, monkeypatch, tmp_path):
    assert pins.resolve("muse", "/x/explicit") == ("/x/explicit", {})
    monkeypatch.setenv("BCA_MUSE_BIN", "/x/env")
    assert pins.resolve("muse") == ("/x/env", {})
    monkeypatch.delenv("BCA_MUSE_BIN")
    assert pins.resolve("muse")[0] == str(pinfile)
    assert pins.resolve("grok") == (None, {})  # no pin recorded: PATH
    monkeypatch.setenv("BCA_NO_PINS", "1")
    assert pins.resolve("muse") == (None, {})


def test_a_changed_or_missing_pin_fails_closed(pinfile):
    pinfile.write_text("#!/bin/sh\necho 'Muse Code 9.9.9'\n")  # e.g. an in-place self-update
    with pytest.raises(IsolationError, match="not the binary that was pinned"):
        MuseSubject("muse-spark-1.3")
    os.remove(pinfile)
    with pytest.raises(IsolationError, match="is missing"):
        MuseSubject("muse-spark-1.3")
