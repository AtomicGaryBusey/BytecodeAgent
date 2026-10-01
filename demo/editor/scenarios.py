"""Keystroke scenarios for the editor, run by driver.py inside the sandbox. Each scenario: the files present
before, the argument, the keys (each sent as one write), and what must hold afterwards."""

from __future__ import annotations

UP, DOWN, RIGHT, LEFT = "\x1b[A", "\x1b[B", "\x1b[C", "\x1b[D"
HOME, END, DEL = "\x1b[H", "\x1b[F", "\x1b[3~"
PGUP, PGDN = "\x1b[5~", "\x1b[6~"
ENTER, BS, SAVE, QUIT = "\r", "\x7f", "\x13", "\x11"
ROWS, COLS = 24, 80


def typed(text: str) -> list[str]:
    return list(text)


LINES60 = "".join(f"line{i:02d}\n" for i in range(1, 61))
LONG = "0123456789" * 20

SCENARIOS = [
    {"name": "new_file", "doc": "edit a missing file, type two lines, save, quit",
     "files": {}, "arg": "new.txt",
     "keys": typed("hello world") + [ENTER] + typed("second line") + [SAVE, QUIT],
     "expect": {"new.txt": "hello world\nsecond line\n"}, "screen": ["new.txt"]},
    {"name": "edit_middle", "doc": "open a 3-line file, Down, End, type X, Home, type Y, save",
     "files": {"f.txt": "alpha\nbravo\ncharlie\n"}, "arg": "f.txt",
     "keys": [DOWN, END, "X", HOME, "Y", SAVE, QUIT],
     "expect": {"f.txt": "alpha\nYbravoX\ncharlie\n"}, "screen": ["alpha", "charlie"]},
    {"name": "backspace_join", "doc": "Backspace at column 0 of line 2 joins it onto line 1",
     "files": {"f.txt": "one\ntwo\nthree\n"}, "arg": "f.txt",
     "keys": [DOWN, HOME, BS, "-", SAVE, QUIT],
     "expect": {"f.txt": "one-two\nthree\n"}},
    {"name": "delete_join", "doc": "Delete at the end of line 1 joins line 2; Delete inside a line removes a byte",
     "files": {"f.txt": "ab\ncd\nef\n"}, "arg": "f.txt",
     "keys": [END, DEL, HOME, DEL, SAVE, QUIT],
     "expect": {"f.txt": "bcd\nef\n"}},
    {"name": "enter_split", "doc": "Enter splits a line at the cursor; typing continues at the new line start",
     "files": {"f.txt": "abcdef\n"}, "arg": "f.txt",
     "keys": [RIGHT, RIGHT, RIGHT, ENTER, "Z", SAVE, QUIT],
     "expect": {"f.txt": "abc\nZdef\n"}},
    {"name": "arrows_wrap", "doc": "Right at a line end moves to the next line; Left at column 0 to the previous end; Up/Down clamp the column",
     "files": {"f.txt": "ab\ncdef\ng\n"}, "arg": "f.txt",
     "keys": [END, RIGHT, "X", DOWN, "Y", LEFT, LEFT, LEFT, "Z", UP, UP, UP, SAVE, QUIT],
     "expect": {"f.txt": "ab\nXcdefZ\ngY\n"}},
    {"name": "page_scroll", "doc": "60 lines on a 24-row screen: PgDn moves 23 lines and the view scrolls",
     "files": {"f.txt": LINES60}, "arg": "f.txt",
     "keys": [PGDN, "@", PGDN, PGDN, PGDN, HOME, "#", PGUP, HOME, "%", SAVE, QUIT],
     "expect": {"f.txt": LINES60.replace("line24", "@line24").replace("line60", "#line60")
                .replace("line37", "%line37")},
     "screen": ["line24", "line60"]},
    {"name": "quit_unsaved", "doc": "a modified buffer needs Ctrl-Q twice and is not saved",
     "files": {"keep.txt": "keep\n"}, "arg": "keep.txt",
     "keys": typed("zzz") + [QUIT, QUIT],
     "expect": {"keep.txt": "keep\n"}},
    {"name": "quit_warning_cancel", "doc": "a key between the two Ctrl-Q cancels the warning; then save and quit",
     "files": {"f.txt": "x\n"}, "arg": "f.txt",
     "keys": ["1", QUIT, "2", QUIT, SAVE, QUIT],
     "expect": {"f.txt": "12x\n"}},
    {"name": "long_line", "doc": "a 200-byte line: End, type !, the view scrolls horizontally",
     "files": {"f.txt": LONG + "\nshort\n"}, "arg": "f.txt",
     "keys": [END, "!", DOWN, "?", SAVE, QUIT],
     "expect": {"f.txt": LONG + "!\nshort?\n"}, "screen": ["789!"]},
    {"name": "empty_file", "doc": "an existing empty file is one empty line; type, save",
     "files": {"e.txt": ""}, "arg": "e.txt",
     "keys": typed("abc") + [SAVE, QUIT],
     "expect": {"e.txt": "abc\n"}},
    {"name": "no_trailing_newline", "doc": "a last line without a newline is still a line; save adds the newline",
     "files": {"f.txt": "a\nb"}, "arg": "f.txt",
     "keys": [DOWN, END, "c", SAVE, QUIT],
     "expect": {"f.txt": "a\nbc\n"}},
]


def reference(content: str | None, keys: list[str], rows: int = ROWS) -> tuple[str | None, bool]:
    """The specification's buffer rules in Python: (file content after the keys, whether it quit).
    Used to check the scenarios' expectations, never shown to the model."""
    lines = [""] if not content else content.split("\n")
    if content and content.endswith("\n"):
        lines.pop()
    y = x = 0
    modified, warn, saved = False, False, content
    t = rows - 1
    for k in keys:
        was_warn, warn = warn, False
        if k == QUIT:
            if not modified or was_warn:
                return saved, True
            warn = True
        elif k == SAVE:
            saved, modified = "".join(line + "\n" for line in lines), False
        elif k in (ENTER, "\n"):
            lines[y:y + 1] = [lines[y][:x], lines[y][x:]]
            y, x, modified = y + 1, 0, True
        elif k in (BS, "\x08"):
            if x > 0:
                lines[y] = lines[y][:x - 1] + lines[y][x:]
                x, modified = x - 1, True
            elif y > 0:
                x = len(lines[y - 1])
                lines[y - 1:y + 1] = [lines[y - 1] + lines[y]]
                y, modified = y - 1, True
        elif k == DEL:
            if x < len(lines[y]):
                lines[y] = lines[y][:x] + lines[y][x + 1:]
                modified = True
            elif y < len(lines) - 1:
                lines[y:y + 2] = [lines[y] + lines[y + 1]]
                modified = True
        elif k == LEFT:
            if x > 0:
                x -= 1
            elif y > 0:
                y, x = y - 1, len(lines[y - 1])
        elif k == RIGHT:
            if x < len(lines[y]):
                x += 1
            elif y < len(lines) - 1:
                y, x = y + 1, 0
        elif k in (UP, DOWN, PGUP, PGDN):
            step = {UP: -1, DOWN: 1, PGUP: -t, PGDN: t}[k]
            y = max(0, min(len(lines) - 1, y + step))
            x = min(x, len(lines[y]))
        elif k == HOME:
            x = 0
        elif k == END:
            x = len(lines[y])
        elif len(k) == 1 and 0x20 <= ord(k) <= 0x7E:
            lines[y] = lines[y][:x] + k + lines[y][x:]
            x, modified = x + 1, True
    return saved, False
