"""The raw-machine-code editor: the committed edit.hex rebuilds into the committed binary byte for byte with no
assembler or linker, and the hex prompt states the layout and the same specification."""

import hashlib

import pytest

from bca.liveeval import hexpath
from demo.editor.prompt import SPEC
from demo.editor.prompt_hex import WORK_AREA, build_prompt
from demo.editor.run_astra_hex import OUT, build

SHA256 = "abe6de7b75b5a20cba87328e9b8644248ec60d15c0d5a66022a673f85675dcb4"


@pytest.mark.skipif(not (OUT / "edit.hex").exists(), reason="no committed hex editor")
def test_committed_hex_rebuilds_the_committed_binary(tmp_path):
    prog = build((OUT / "edit.hex").read_text(), tmp_path / "edit")
    elf = (tmp_path / "edit").read_bytes()
    assert elf == (OUT / "edit").read_bytes() and hashlib.sha256(elf).hexdigest() == SHA256
    assert elf[0x1000:0x1000 + len(prog.text)] == prog.text  # the model's bytes, unchanged
    assert len(prog.lines) == 354 and len(prog.data) == 233


def test_prompt():
    p = build_prompt()
    for s in ("no assembler, no linker", "0x400000", "0x500000", "0x600000", f"{WORK_AREA >> 20} MiB",
              "argv[1] the file name", "ioctl = 29"):
        assert s in p
    assert "`.bss`" not in p and "_start" not in p
    assert SPEC.strip().split("\n")[0] in p
    with pytest.raises(Exception):
        build("not hex\n", OUT / "never-written")
    assert not (OUT / "never-written").exists()
    assert hexpath.HEX_TEXT_BASE == 0x400000
