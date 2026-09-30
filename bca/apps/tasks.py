"""The eight app tasks: specification, Python reference, deterministic tests.

Every expected output in a test comes from the task's reference, so the reference *is* the truth; the specs
are written to pin down every decision the reference makes (output format to the byte, empty input, a missing
final newline, limits, error behaviour and exit status).

Specs are ISA-neutral. The prompt builder adds the ISA, the system-call numbers and the entry state.
"""

from __future__ import annotations

import base64 as _b64
import random
import re
from dataclasses import dataclass
from typing import Callable

__all__ = ["AppTest", "AppTask", "APP_TASKS", "task_by_name"]


@dataclass(frozen=True)
class AppTest:
    label: str
    stdin: bytes
    stdout: bytes
    status: int


@dataclass(frozen=True)
class AppTask:
    name: str
    spec: str
    bss_size: int
    max_insns: int
    reference: Callable[[bytes], tuple[bytes, int]]
    tests: Callable[[], tuple[AppTest, ...]]


BSS_SIZE = 1 << 20  # 1 MiB zero-initialised area for every task

_IO = """\
Input and output (applies to this whole task):
- The input is the complete contents of standard input (file descriptor 0). Read it with `read` until `read`
  returns 0 (end of input). A `read` may return FEWER bytes than requested, at any position (in the middle of a
  line, a token or a group of bytes), so keep calling it; only a return value of 0 means end of input.
- Write the output to standard output (file descriptor 1). Only the bytes this task describes may appear on
  standard output (standard error is ignored). Every output byte must be written before the program exits.
- Finish with `exit` (or `exit_group`) and the exit status this task gives. The program must not run off the end
  of its code or crash.
"""


def _spec(body: str) -> str:
    return body.strip() + "\n\n" + _IO


def _t(ref: Callable[[bytes], tuple[bytes, int]], label: str, stdin: bytes) -> AppTest:
    out, status = ref(stdin)
    return AppTest(label, stdin, out, status)


def _split_lines(data: bytes) -> list[bytes]:
    """Lines as every task defines them: separated by 0x0A; a final line without a newline still counts;
    the empty remainder after a final newline (and empty input) is not a line."""
    if not data:
        return []
    parts = data.split(b"\n")
    if parts[-1] == b"":
        parts.pop()
    return parts


def _text(rng: random.Random, n: int, alphabet: bytes) -> bytes:
    return bytes(rng.choice(alphabet) for _ in range(n))


_WORDY = b"abcdefghijklmnopqrstuvwxyz" * 3 + b"ABCXYZ0123456789.,;'" + b"    " + b"\n" + b"\t"


# --------------------------------------------------------------------------------------------------- wc

WC_MAX = 65536

WC_SPEC = _spec(
    f"""
Task `wc`: count the lines, words and bytes of standard input and print them.

- lines = the number of newline bytes (0x0A) in the input. A final line without a newline is NOT counted.
- words = the number of maximal non-empty runs of non-whitespace bytes. Whitespace is exactly these six bytes:
  space 0x20, tab 0x09, newline 0x0A, vertical tab 0x0B, form feed 0x0C, carriage return 0x0D. Every other byte
  value (including 0x00, other control bytes and bytes >= 0x80) is a word byte.
- bytes = the total number of input bytes.

Output exactly one line: the three counts in decimal, separated by single spaces, followed by a newline:
`<lines> <words> <bytes>\\n` (no padding, no leading zeros; zero is printed as `0`). Example: input
`hello world\\nbye` prints `1 3 15\\n`; empty input prints `0 0 0\\n`.

Limits: the input is at most {WC_MAX} bytes. Exit status 0.
"""
)


def wc_reference(data: bytes) -> tuple[bytes, int]:
    # bytes.split() with no argument splits on exactly b" \t\n\r\x0b\x0c"
    return b"%d %d %d\n" % (data.count(b"\n"), len(data.split()), len(data)), 0


def wc_tests() -> tuple[AppTest, ...]:
    rng = random.Random(0x5743)
    r = wc_reference
    ts = [
        _t(r, "empty", b""),
        _t(r, "one_byte", b"a"),
        _t(r, "one_newline", b"\n"),
        _t(r, "one_space", b" "),
        _t(r, "simple", b"hello world\n"),
        _t(r, "no_final_newline", b"hello world\nbye"),
        _t(r, "leading_trailing_ws", b"   two  words   "),
        _t(r, "all_ws_kinds", b"a\tb\nc\rd\x0be\x0cf g\n"),
        _t(r, "nul_and_ff_are_word_bytes", b"\x00 \xff\xfe \x00\x00\n"),
        _t(r, "other_controls_are_word_bytes", b"x\x1cy \x01 \x7f\x1f\n"),
        _t(r, "crlf", b"one two\r\nthree\r\n\r\n"),
        _t(r, "blank_lines", b"\n" * 100),
        _t(r, "one_long_word", b"w" * 5000),
        _t(r, "alternating", b"a " * 3000),
    ]
    for n in (1000, 4095, 4096, 4097, 8193):
        ts.append(_t(r, f"text_{n}", _text(rng, n, _WORDY)))
    ts.append(_t(r, "binary_random", bytes(rng.randrange(256) for _ in range(20000))))
    ts.append(_t(r, "max_text", _text(rng, WC_MAX, _WORDY)))
    ts.append(_t(r, "max_spaces", b" " * WC_MAX))
    ts.append(_t(r, "max_one_word", b"\xff" * WC_MAX))
    ts.append(_t(r, "max_newlines", b"\n" * WC_MAX))
    return tuple(ts)


# --------------------------------------------------------------------------------------------- rev_lines

REV_MAX = 65536

REV_SPEC = _spec(
    f"""
Task `rev_lines`: print every line of standard input with its bytes in reverse order.

- A line is a (possibly empty) sequence of bytes terminated by a newline byte 0x0A. If the input does not end
  with a newline, the bytes after the last newline form one more, final line. Empty input has no lines, and the
  empty remainder after a final newline is not a line.
- For each line, in input order, print the line's bytes in reverse order followed by one newline 0x0A. The
  newline itself is not part of the line and is not reversed. The final line is printed with a newline even if
  it had none in the input.
- Reversal is byte-wise: every byte value 0x00-0xFF is an ordinary byte (no UTF-8 decoding). A carriage return
  0x0D is an ordinary byte, so `ab\\r\\n` prints `\\rba\\n`.

Examples: `abc\\nde\\n` prints `cba\\ned\\n`; `abc\\n\\nxy` prints `cba\\n\\nyx\\n`; empty input prints nothing.

Limits: the input is at most {REV_MAX} bytes; a single line may be as long as the whole input. Exit status 0.
"""
)


def rev_lines_reference(data: bytes) -> tuple[bytes, int]:
    return b"".join(line[::-1] + b"\n" for line in _split_lines(data)), 0


def rev_lines_tests() -> tuple[AppTest, ...]:
    rng = random.Random(0x5245)
    r = rev_lines_reference
    ts = [
        _t(r, "empty", b""),
        _t(r, "one_byte", b"a"),
        _t(r, "one_newline", b"\n"),
        _t(r, "one_line", b"abc\n"),
        _t(r, "no_final_newline", b"abc\nde"),
        _t(r, "blank_lines_between", b"abc\n\n\nxy\n"),
        _t(r, "only_blank_lines", b"\n\n\n\n"),
        _t(r, "crlf", b"ab\r\ncd\r\n\r\n"),
        _t(r, "binary_bytes", b"\x00\xff\x01\n\xfe\x00\n\x00"),
        _t(r, "utf8_bytewise", "héllo wörld\n".encode()),
        _t(r, "line_4096", _text(rng, 4096, b"abcdefgh") + b"\n"),
        _t(r, "line_4097_no_nl", _text(rng, 4097, b"abcdefgh")),
    ]
    for i, n in enumerate((3000, 9000, 20000)):
        lines = [_text(rng, rng.choice([0, 1, 2, 7, 80, 500, 4095]), bytes(range(1, 256)).replace(b"\n", b""))
                 for _ in range(n // 200 + 5)]
        blob = b"\n".join(lines)[:n]
        ts.append(_t(r, f"random_lines_{i}", blob + (b"\n" if i % 2 else b"")))
    ts.append(_t(r, "max_one_line_no_nl", _text(rng, REV_MAX, b"0123456789abcdef")))
    ts.append(_t(r, "max_one_line_nl", _text(rng, REV_MAX - 1, b"0123456789abcdef") + b"\n"))
    ts.append(_t(r, "max_newlines", b"\n" * REV_MAX))
    ts.append(_t(r, "max_binary", bytes(rng.randrange(256) for _ in range(REV_MAX))))
    return tuple(ts)


# ----------------------------------------------------------------------------------------------- hexdump

HEX_MAX = 65536

HEX_SPEC = _spec(
    f"""
Task `hexdump`: print a canonical hex+ASCII dump of standard input (like `hexdump -C`, but with NO squeezing of
repeated lines and with the final offset line always printed).

The input is shown in rows of 16 bytes; the last row may be shorter (1 to 15 bytes). For a row of n bytes that
starts at byte offset `off`, print exactly:
1. `off` as 8 lowercase hexadecimal digits (zero-padded), then two spaces;
2. for each position i = 0..15: if i < n, the byte as 2 lowercase hex digits followed by one space; otherwise
   three spaces. After position 7 (the eighth byte slot) print one extra space;
3. one space, `|`, then the n bytes as characters: a byte 0x20-0x7E is printed as itself, every other byte
   (0x00-0x1F and 0x7F-0xFF) is printed as `.`; then `|` and a newline. The character column is NOT padded for
   a short row.

After all rows, print the total input length as 8 lowercase hex digits and a newline. Empty input therefore
prints only `00000000\\n`. Identical rows are printed in full (never replaced by `*`).

Example: input `0123456789abcdefXYZ\\n` (20 bytes) prints
`00000000  30 31 32 33 34 35 36 37  38 39 61 62 63 64 65 66  |0123456789abcdef|\\n`
`00000010  58 59 5a 0a                                       |XYZ.|\\n`
`00000014\\n`
(each full row is exactly 78 characters plus the newline).

Limits: the input is at most {HEX_MAX} bytes. Exit status 0.
"""
)


def hexdump_reference(data: bytes) -> tuple[bytes, int]:
    out = []
    for off in range(0, len(data), 16):
        row = data[off : off + 16]
        hexpart = "".join((f"{row[i]:02x} " if i < len(row) else "   ") + (" " if i == 7 else "") for i in range(16))
        chars = "".join(chr(b) if 0x20 <= b <= 0x7E else "." for b in row)
        out.append(f"{off:08x}  {hexpart} |{chars}|\n")
    out.append(f"{len(data):08x}\n")
    return "".join(out).encode(), 0


def hexdump_tests() -> tuple[AppTest, ...]:
    rng = random.Random(0x4845)
    r = hexdump_reference
    ts = [
        _t(r, "empty", b""),
        _t(r, "one_byte", b"A"),
        _t(r, "example", b"0123456789abcdefXYZ\n"),
    ]
    for n in (7, 8, 9, 15, 16, 17, 31, 32, 33):
        ts.append(_t(r, f"len_{n}", _text(rng, n, b"abcdefghijklmnopqrstuvwxyz0123456789 \n")))
    ts.append(_t(r, "all_byte_values", bytes(range(256))))
    ts.append(_t(r, "printable_edges", b"\x1f\x20\x7e\x7f\x80\xff\x00\x09\x0a\x0d~ !"))
    ts.append(_t(r, "zeros_not_squeezed", b"\x00" * 64))
    ts.append(_t(r, "text", b"The quick brown fox jumps over the lazy dog.\nPack my box with five dozen liquor jugs.\n"))
    for n in (4095, 4096, 4097):
        ts.append(_t(r, f"random_{n}", bytes(rng.randrange(256) for _ in range(n))))
    ts.append(_t(r, "max_minus_1", bytes(rng.randrange(256) for _ in range(HEX_MAX - 1))))
    ts.append(_t(r, "max", bytes(rng.randrange(256) for _ in range(HEX_MAX))))
    return tuple(ts)


# -------------------------------------------------------------------------------------------- sort_lines

SORT_MAX = 32768
SORT_MAX_LINES = 1000

SORT_SPEC = _spec(
    f"""
Task `sort_lines`: print the lines of standard input sorted in ascending byte-wise order.

- Lines are defined as follows: a line is a (possibly empty) sequence of bytes terminated by a newline 0x0A; if
  the input does not end with a newline, the bytes after the last newline form one more, final line. Empty input
  has no lines, and the empty remainder after a final newline is not a line.
- Order: compare two lines byte by byte as UNSIGNED values 0x00-0xFF (like C `memcmp`); the first differing
  byte decides. If one line is a prefix of the other, the shorter line comes first (so the empty line sorts
  before every non-empty line). Equal lines are all printed (duplicates are kept).
- Print every line followed by one newline 0x0A (the final line gets a newline even if it had none in the
  input). Empty input prints nothing. A carriage return 0x0D and every other byte value are ordinary bytes.

Example: `pear\\napple\\nPear\\n\\napple` prints `\\nPear\\napple\\napple\\npear\\n`.

Limits: the input is at most {SORT_MAX} bytes and has at most {SORT_MAX_LINES} lines. Exit status 0.
"""
)


def sort_lines_reference(data: bytes) -> tuple[bytes, int]:
    return b"".join(line + b"\n" for line in sorted(_split_lines(data))), 0


def sort_lines_tests() -> tuple[AppTest, ...]:
    rng = random.Random(0x534F)
    r = sort_lines_reference
    ts = [
        _t(r, "empty", b""),
        _t(r, "one_byte", b"x"),
        _t(r, "one_newline", b"\n"),
        _t(r, "two", b"b\na\n"),
        _t(r, "example", b"pear\napple\nPear\n\napple"),
        _t(r, "prefixes", b"abc\nab\nabcd\na\n\nab\n"),
        _t(r, "unsigned_bytes", b"\x80x\nzz\n\xff\n\x7f\n\x01\nA\n"),
        _t(r, "nul_bytes", b"a\x00b\na\na\x00\na\x00a\n"),
        _t(r, "crlf", b"b\r\na\r\na\nb\n"),
        _t(r, "duplicates", b"same\nsame\nother\nsame"),
        _t(r, "blank_lines", b"\n\nz\n\n"),
    ]
    words = [_text(rng, rng.randint(0, 12), b"abcde") for _ in range(SORT_MAX_LINES)]
    ts.append(_t(r, "sorted_1000", b"\n".join(sorted(words)) + b"\n"))
    ts.append(_t(r, "reverse_1000", b"\n".join(sorted(words, reverse=True)) + b"\n"))
    ts.append(_t(r, "random_1000_no_nl", b"\n".join(words)))
    ts.append(_t(r, "all_equal_1000", b"k\n" * SORT_MAX_LINES))
    long_common = [b"x" * 200 + _text(rng, 3, b"ab") for _ in range(150)]
    ts.append(_t(r, "long_common_prefix", b"\n".join(long_common) + b"\n"))
    for i, nlines in enumerate((10, 100, 400)):
        lines = [bytes(rng.randrange(256) for _ in range(rng.randint(0, 30))).replace(b"\n", b"") for _ in range(nlines)]
        ts.append(_t(r, f"random_binary_{i}", b"\n".join(lines) + b"\n"))
    big = [_text(rng, 31, b"abcdefghijklmnopqrstuvwxyz") for _ in range(SORT_MAX_LINES)]
    ts.append(_t(r, "max_size", b"\n".join(big) + b"\n"))
    ts.append(_t(r, "one_long_line", _text(rng, SORT_MAX, b"xyz")))
    return tuple(ts)


# --------------------------------------------------------------------------------------------------- rpn

RPN_MAX = 65536
RPN_LINE_MAX = 1000
I64_MIN = -(1 << 63)
I64_MAX = (1 << 63) - 1

RPN_SPEC = _spec(
    f"""
Task `rpn`: evaluate one Reverse Polish Notation expression per input line and print one result line per input
line.

Lines: a line is a (possibly empty) sequence of bytes terminated by a newline 0x0A; if the input does not end
with a newline, the bytes after the last newline form one more, final line. Empty input has no lines (prints
nothing), and the empty remainder after a final newline is not a line.

Tokens: the separators are space 0x20 and tab 0x09 only; a token is a maximal run of other bytes. Any number of
separators may appear before, between and after tokens. A token is exactly one of:
- an integer literal: an optional `-` followed by 1 to 18 decimal digits `0`-`9` (leading zeros allowed, `-0`
  is 0). A literal with 19 or more digits is invalid;
- an operator: exactly one of `+` `-` `*` `/` `%` (a lone `-` is the subtraction operator);
- anything else (for example `1a`, `+5`, `--3`, `1.5`, `x`, or a token containing 0x0D or any other byte) is
  invalid.

Evaluation uses a stack of signed 64-bit integers, empty at the start of each line. Process tokens left to right:
a literal is pushed; an operator pops b (top) then a, and pushes `a op b`. `+`, `-`, `*` wrap modulo 2^64 (two's
complement). `/` is division truncated toward zero; `%` is the remainder with the sign of a (a == (a/b)*b +
a%b), as in C. A line is an ERROR if: it contains an invalid token; an operator finds fewer than two values on
the stack; `/` or `%` has b == 0; `/` or `%` has a == -9223372036854775808 and b == -1; or, after the last token,
the stack does not hold exactly one value (this includes a line with no tokens, e.g. an empty line).

Output per line: if it is not an error, the single remaining value in decimal (a `-` sign if negative, no `+`,
no leading zeros; -9223372036854775808 must print correctly) followed by a newline; if it is an error, exactly
`error\\n`. After an error the rest of that line is ignored and evaluation continues with the next line.

Example: input `3 4 +\\n-7 2 /\\n1 +\\n\\n5 1 2 + 4 * + 3 -` prints `7\\n-3\\nerror\\nerror\\n14\\n`.

Limits: the input is at most {RPN_MAX} bytes; a line is at most {RPN_LINE_MAX} bytes. Exit status 0.
"""
)

_RPN_LIT = re.compile(rb"-?[0-9]{1,18}")


def _wrap64(v: int) -> int:
    v &= (1 << 64) - 1
    return v - (1 << 64) if v >> 63 else v


def _rpn_line(line: bytes) -> int | None:
    stack: list[int] = []
    for tok in re.split(rb"[ \t]+", line):
        if not tok:
            continue
        if tok in (b"+", b"-", b"*", b"/", b"%"):
            if len(stack) < 2:
                return None
            b = stack.pop()
            a = stack.pop()
            if tok == b"+":
                v = a + b
            elif tok == b"-":
                v = a - b
            elif tok == b"*":
                v = a * b
            else:
                if b == 0 or (a == I64_MIN and b == -1):
                    return None
                q = abs(a) // abs(b)
                if (a < 0) != (b < 0):
                    q = -q
                v = q if tok == b"/" else a - q * b
            stack.append(_wrap64(v))
        elif _RPN_LIT.fullmatch(tok):
            stack.append(int(tok))
        else:
            return None
    return stack[0] if len(stack) == 1 else None


def rpn_reference(data: bytes) -> tuple[bytes, int]:
    out = []
    for line in _split_lines(data):
        v = _rpn_line(line)
        out.append(b"error\n" if v is None else b"%d\n" % v)
    return b"".join(out), 0


def _rpn_random_expr(rng: random.Random, depth: int) -> str:
    if depth <= 0 or rng.random() < 0.3:
        n = rng.choice([rng.randint(-20, 20), rng.randint(-10**6, 10**6), rng.randint(-(10**18) + 1, 10**18 - 1)])
        return str(n)
    op = rng.choice("+-*/%")
    return f"{_rpn_random_expr(rng, depth - 1)} {_rpn_random_expr(rng, depth - 1)} {op}"


def rpn_tests() -> tuple[AppTest, ...]:
    rng = random.Random(0x5250)
    r = rpn_reference
    ts = [
        _t(r, "empty", b""),
        _t(r, "one_newline", b"\n"),
        _t(r, "one_digit_no_nl", b"7"),
        _t(r, "example", b"3 4 +\n-7 2 /\n1 +\n\n5 1 2 + 4 * + 3 -"),
        _t(r, "operators", b"6 3 +\n6 3 -\n3 6 -\n6 3 *\n7 2 /\n7 2 %\n"),
        _t(r, "signed_div_mod", b"-7 2 /\n-7 2 %\n7 -2 /\n7 -2 %\n-7 -2 /\n-7 -2 %\n0 5 /\n0 -5 %\n"),
        _t(r, "div_by_zero", b"1 0 /\n1 0 %\n0 0 /\n5 3 3 - %\n"),
        _t(r, "min_div_minus_one", b"922337203685477580 -10 * 8 - -1 /\n922337203685477580 -10 * 8 - -1 %\n"),
        _t(r, "min_value_prints", b"922337203685477580 -10 * 8 -\n922337203685477580 10 * 7 + 1 +\n"),
        _t(r, "max_value_and_wrap", b"922337203685477580 10 * 7 +\n999999999999999999 999999999999999999 *\n"),
        _t(r, "literal_digits", b"999999999999999999\n-999999999999999999\n1000000000000000000\n-0\n007\n0000000000000000001\n"),
        _t(r, "invalid_tokens", b"1a\n+5\n--3\n1.5\nx\n1 2 +x\n- \n-\n1 -\n"),
        _t(r, "stack_errors", b"1 2\n+\n1 +\n1 2 3 + \n   \n\t\n"),
        _t(r, "separators", b"  1\t\t2   +  \n\t3 4\t*\t\n"),
        _t(r, "cr_is_invalid", b"1 2 +\r\n3\r\n"),
        _t(r, "nul_is_invalid", b"1 \x00 +\n1\x00\n"),
        _t(r, "deep_stack", (" ".join(["1"] * 250) + " +" * 249 + "\n").encode()),
        _t(r, "long_line_max", (" ".join(["-12"] * 150) + " *" * 149 + "   ").encode()),
    ]
    for i in range(4):
        lines = []
        for _ in range(60):
            e = _rpn_random_expr(rng, rng.randint(0, 5))
            if len(e) > RPN_LINE_MAX:
                e = "1"
            if rng.random() < 0.1:
                toks = e.split()
                del toks[rng.randrange(len(toks))]
                e = " ".join(toks)
            lines.append(e)
        ts.append(_t(r, f"random_{i}", ("\n".join(lines) + ("\n" if i % 2 else "")).encode()))
    big = []
    size = 0
    while True:
        e = _rpn_random_expr(rng, 3)
        if size + len(e) + 1 > RPN_MAX:
            break
        big.append(e)
        size += len(e) + 1
    ts.append(_t(r, "random_max", ("\n".join(big) + "\n").encode()))
    return tuple(ts)


# ------------------------------------------------------------------------------------------------ base64

B64_MAX = 49152

B64_SPEC = _spec(
    f"""
Task `base64`: Base64-encode standard input (RFC 4648 standard alphabet, like `base64` with no options).

- Alphabet: `A`-`Z` (0-25), `a`-`z` (26-51), `0`-`9` (52-61), `+` (62), `/` (63). Each group of 3 input bytes
  becomes 4 characters (most significant bits first). A final group of 1 byte becomes 2 characters plus `==`;
  a final group of 2 bytes becomes 3 characters plus `=`.
- The encoded text is split into lines of exactly 76 characters; the last line may be shorter. Every line,
  including the last, ends with one newline 0x0A. Never print an empty line: if the encoded text length is a
  multiple of 76, the output ends right after that line's newline.
- Empty input prints nothing (not even a newline). Input bytes are arbitrary (0x00-0xFF).

Examples: `Man` prints `TWFu\\n`; `Ma` prints `TWE=\\n`; `M` prints `TQ==\\n`; 57 input bytes print exactly one
76-character line and a newline; 58 input bytes print that line plus a second line of 4 characters.

Limits: the input is at most {B64_MAX} bytes. Exit status 0.
"""
)


def base64_reference(data: bytes) -> tuple[bytes, int]:
    return _b64.encodebytes(data), 0


def base64_tests() -> tuple[AppTest, ...]:
    rng = random.Random(0x4236)
    r = base64_reference
    ts = [
        _t(r, "empty", b""),
        _t(r, "one_byte", b"M"),
        _t(r, "two_bytes", b"Ma"),
        _t(r, "three_bytes", b"Man"),
        _t(r, "rfc_vectors", b"foobar"),
        _t(r, "zero_bytes", b"\x00\x00\x00\x00"),
        _t(r, "ff_bytes", b"\xff\xff\xff\xff\xff"),
        _t(r, "all_byte_values", bytes(range(256))),
        _t(r, "hi_bits", b"\xfb\xff\xbf\xfc\x00\x3f"),
    ]
    for n in (56, 57, 58, 113, 114, 115, 171):
        ts.append(_t(r, f"len_{n}", bytes(rng.randrange(256) for _ in range(n))))
    for n in (4095, 4096, 4097, 4104):
        ts.append(_t(r, f"random_{n}", bytes(rng.randrange(256) for _ in range(n))))
    ts.append(_t(r, "text", b"Many hands make light work.\n" * 20))
    ts.append(_t(r, "max", bytes(rng.randrange(256) for _ in range(B64_MAX))))
    ts.append(_t(r, "max_minus_1", bytes(rng.randrange(256) for _ in range(B64_MAX - 1))))
    return tuple(ts)


# ---------------------------------------------------------------------------------------------------- bf

BF_MAX = 65536
BF_TAPE = 30000
BF_MAX_STEPS = 5_000_000

BF_SPEC = _spec(
    f"""
Task `bf`: a Brainfuck interpreter. Standard input holds the program, then a `!` byte (0x21), then the program's
input: the program is every byte before the FIRST `!`; the program's input is every byte after it (it may itself
contain `!`). If there is no `!` at all, the whole of standard input is the program and its input is empty.

Machine: a tape of {BF_TAPE} byte cells, all 0 initially, and a data pointer at cell 0. The eight commands:
`>` pointer + 1; `<` pointer - 1; `+` cell + 1 (255 wraps to 0); `-` cell - 1 (0 wraps to 255); `.` write the
cell's byte to the output; `,` read the next byte of the program's input into the cell, or store 0 if the
program's input is exhausted (every later `,` also stores 0); `[` if the cell is 0, jump to just after the
matching `]`; `]` if the cell is not 0, jump back to just after the matching `[`. Every other byte of the program
(letters, digits, spaces, newlines, any value) is a comment and is ignored.

Brackets: before running anything, check that the program's `[` and `]` are balanced (every `]` closes an
earlier unmatched `[`, and no `[` is left open). If they are not, print NOTHING and exit with status 1.
Otherwise run the program to its end and exit with status 0 (the output is exactly the bytes written by `.`).

Limits: the whole standard input is at most {BF_MAX} bytes; bracket nesting is at most 1000 deep; the test
programs never move the pointer outside cells 0..{BF_TAPE - 1}, write at most 65536 output bytes, and execute at
most {BF_MAX_STEPS} commands.
"""
)


def _bf_run(data: bytes) -> tuple[bytes, int]:
    cut = data.find(b"!")
    prog, inp = (data, b"") if cut < 0 else (data[:cut], data[cut + 1 :])
    match, st = {}, []
    for i, c in enumerate(prog):
        if c == 0x5B:
            st.append(i)
        elif c == 0x5D:
            if not st:
                return b"", 1
            j = st.pop()
            match[i], match[j] = j, i
    if st:
        return b"", 1
    tape = bytearray(BF_TAPE)
    ptr = pc = ip = steps = 0
    out = bytearray()
    n = len(prog)
    while pc < n:
        c = prog[pc]
        if c == 0x2B:
            tape[ptr] = (tape[ptr] + 1) & 255
        elif c == 0x2D:
            tape[ptr] = (tape[ptr] - 1) & 255
        elif c == 0x3E:
            ptr += 1
        elif c == 0x3C:
            ptr -= 1
        elif c == 0x2E:
            out.append(tape[ptr])
        elif c == 0x2C:
            tape[ptr] = inp[ip] if ip < len(inp) else 0
            ip += 1
        elif c == 0x5B:
            if tape[ptr] == 0:
                pc = match[pc]
        elif c == 0x5D:
            if tape[ptr] != 0:
                pc = match[pc]
        else:
            pc += 1
            continue
        if not 0 <= ptr < BF_TAPE:
            raise AssertionError("bf test program moved the pointer off the tape")
        steps += 1
        if steps > BF_MAX_STEPS:
            raise AssertionError("bf test program exceeds the step limit")
        pc += 1
    return bytes(out), 0


def bf_reference(data: bytes) -> tuple[bytes, int]:
    return _bf_run(data)


_BF_HELLO = (b"++++++++[>++++[>++>+++>+++>+<<<<-]>+>+>->>+[<]<-]>>.>---.+++++++..+++.>>.<-.<.+++.------.--------."
             b">>+.>++.")


def _bf_print(s: bytes) -> bytes:
    return b"".join(b"[-]" + b"+" * c + b"." for c in s)


def bf_tests() -> tuple[AppTest, ...]:
    rng = random.Random(0x4246)
    r = bf_reference
    cat_input = bytes(b for b in (rng.randrange(1, 256) for _ in range(3000)))
    rev_input = _text(rng, 5000, b"abcdefghijklmnopqrstuvwxyz\n!")
    ts = [
        _t(r, "empty", b""),
        _t(r, "only_bang", b"!"),
        _t(r, "hello_world", _BF_HELLO),
        _t(r, "hello_commented", b"Hello program: prints Hello World!\n".replace(b"!", b"") + _BF_HELLO.replace(b"[", b"[ loop\n")),
        _t(r, "wrap_down", b"-."),
        _t(r, "wrap_up", b"+" * 256 + b"." + b"+" * 255 + b"."),
        _t(r, "eof_stores_zero", b"+,.+,.!"),
        _t(r, "read_then_eof", b",.,.,.!ab"),
        _t(r, "cat_binary", b",[.,]!" + cat_input),
        _t(r, "input_contains_bang", b",[.,]!!x!y"),
        _t(r, "no_bang_input_empty", b",[.,]+."),
        _t(r, "reverse_input", b">,[>,]<[.<]!" + rev_input),
        _t(r, "unbalanced_open", b"+[."),
        _t(r, "unbalanced_close", b"+]."),
        _t(r, "unbalanced_crossed", b"][."),
        _t(r, "unbalanced_nested", b"++[[-]."),
        _t(r, "unbalanced_extra_close", b"+[-]].+"),
        _t(r, "brackets_in_input_ignored", b",.!]]]["),
        _t(r, "skipped_loop", b"[[[.]]]+++[>+<-]>."),
        _t(r, "far_cell", b">" * (BF_TAPE - 1) + b"+++." + b"<" * (BF_TAPE - 1) + b"."),
        _t(r, "nested_multiply", b"+++++[>+++++[>+++++[>+++++<-]<-]<-]>>>."),
        _t(r, "heavy_loops", b"+" * 64 + b"[>" + b"+" * 64 + b"[>" + b"+" * 64 + b"[>+<-]<-]<-]>>>.[-]<<<."),
        _t(r, "print_generated", _bf_print(b"The quick brown fox\x00\xff jumps\n")),
        _t(r, "all_bytes_out", b"+[.+]"),
        _t(r, "add_digits", b",>,[<+>-]<" + b"-" * 48 + b".!34"),
        _t(r, "deep_nesting", b"+" + b"[" * 1000 + b"-" + b"]" * 1000 + b"+++."),
        _t(r, "comments_all_bytes", bytes(b for b in range(256) if b not in b"+-<>[],.!") + b"++."),
    ]
    big = _bf_print(_text(rng, 400, b"abcdefghijklmnopqrstuvwxyz ")) + b"!" + b"x" * 2000
    ts.append(_t(r, "long_program", big[:BF_MAX]))
    return tuple(ts)


# --------------------------------------------------------------------------------------------- json_lint

JSON_MAX = 65536
JSON_DEPTH = 1000

JSON_SPEC = _spec(
    f"""
Task `json_lint`: check that standard input is exactly one valid JSON document (RFC 8259 grammar) and report
the byte offset of the first error.

Grammar (byte-level):
- document = ws value ws, and nothing else. ws = any number of the bytes space 0x20, tab 0x09, newline 0x0A,
  carriage return 0x0D (only these four).
- value = object | array | string | number | `true` | `false` | `null` (lowercase only).
- object = `{{` ws `}}`, or `{{` ws member (ws `,` ws member)* ws `}}`, where member = string ws `:` ws value.
  array = `[` ws `]`, or `[` ws value (ws `,` ws value)* ws `]`. No trailing commas.
- string = `"` chars `"`. A char is any byte 0x20-0xFF except `"` (0x22) and `\\` (0x5C) (bytes >= 0x80 are
  accepted as they are; no UTF-8 validation), or an escape: `\\` followed by one of `"` `\\` `/` `b` `f` `n` `r`
  `t`, or `\\u` followed by exactly 4 hex digits (`0-9a-fA-F`; any code unit, surrogates are not checked).
  Bytes 0x00-0x1F inside a string are errors.
- number = `-`? int frac? exp?, where int = `0` | a digit `1`-`9` followed by any digits; frac = `.` and one or
  more digits; exp = `e` or `E`, an optional `+` or `-`, and one or more digits. (So `01`, `+1`, `.5`, `1.`,
  `1e`, `-` are not valid numbers.)

Output: if the input is valid, print `ok\\n` and exit with status 0. Otherwise print `error at N\\n` (N in
decimal, no padding) and exit with status 1, where N is the 0-based offset of the first byte at which the input
stops being the beginning of some valid document: the first byte that no valid document could have at that
position given all the bytes before it. If every byte of the input is such a valid beginning but the document is
incomplete (this includes empty and whitespace-only input), N is the length of the input.

Examples: `[1, 2]` ok; empty input `error at 0`; `[1,]` `error at 3`; `{{"a" 1}}` `error at 5`; `01` `error at 1`;
`tru` `error at 3`; `trux` `error at 3`; `[1] x` `error at 4`; `"a<TAB>b"` (a raw tab byte) `error at 2`;
`-` `error at 1`; `1.e5` `error at 2`.

Limits: the input is at most {JSON_MAX} bytes; arrays and objects are nested at most {JSON_DEPTH} deep.
"""
)


class _JsonError(Exception):
    def __init__(self, pos: int):
        super().__init__(pos)
        self.pos = pos


def _json_check(data: bytes) -> None:
    n = len(data)
    DIG = b"0123456789"
    HEX = b"0123456789abcdefABCDEF"

    def ws(i: int) -> int:
        while i < n and data[i] in b" \t\n\r":
            i += 1
        return i

    def need(i: int) -> int:
        if i >= n:
            raise _JsonError(n)
        return data[i]

    def string(i: int) -> int:  # i just after the opening quote
        while True:
            c = need(i)
            if c == 0x22:
                return i + 1
            if c < 0x20:
                raise _JsonError(i)
            if c == 0x5C:
                e = need(i + 1)
                if e in b'"\\/bfnrt':
                    i += 2
                elif e == 0x75:
                    i += 2
                    for _ in range(4):
                        if need(i) not in HEX:
                            raise _JsonError(i)
                        i += 1
                else:
                    raise _JsonError(i + 1)
            else:
                i += 1

    def digits1(i: int) -> int:
        if need(i) not in DIG:
            raise _JsonError(i)
        while i < n and data[i] in DIG:
            i += 1
        return i

    def number(i: int) -> int:
        if data[i] == 0x2D:
            i += 1
        c = need(i)
        if c == 0x30:
            i += 1
        elif 0x31 <= c <= 0x39:
            i = digits1(i)
        else:
            raise _JsonError(i)
        if i < n and data[i] == 0x2E:
            i = digits1(i + 1)
        if i < n and data[i] in b"eE":
            i += 1
            if i < n and data[i] in b"+-":
                i += 1
            i = digits1(i)
        return i

    # Iterative parser (explicit stack) so deep nesting never hits Python's recursion limit.
    # Containers on the stack: "a" array, "o" object.
    stack: list[str] = []
    i = ws(0)
    while True:
        # parse a value at i
        c = need(i)
        if c == 0x7B:
            i = ws(i + 1)
            if need(i) == 0x7D:
                i += 1
            else:
                stack.append("o")
                if need(i) != 0x22:
                    raise _JsonError(i)
                i = ws(string(i + 1))
                if need(i) != 0x3A:
                    raise _JsonError(i)
                i = ws(i + 1)
                continue
        elif c == 0x5B:
            i = ws(i + 1)
            if need(i) == 0x5D:
                i += 1
            else:
                stack.append("a")
                continue
        elif c == 0x22:
            i = string(i + 1)
        elif c == 0x2D or c in DIG:
            i = number(i)
        elif c in b"tfn":
            word = {0x74: b"true", 0x66: b"false", 0x6E: b"null"}[c]
            for ch in word:
                if need(i) != ch:
                    raise _JsonError(i)
                i += 1
        else:
            raise _JsonError(i)
        # a value just ended at i: close containers / continue with the next element
        while True:
            i = ws(i)
            if not stack:
                if i < n:
                    raise _JsonError(i)
                return
            c = need(i)
            top = stack[-1]
            if c == 0x2C:
                i = ws(i + 1)
                if top == "o":
                    if need(i) != 0x22:
                        raise _JsonError(i)
                    i = ws(string(i + 1))
                    if need(i) != 0x3A:
                        raise _JsonError(i)
                    i = ws(i + 1)
                break
            if (top == "a" and c == 0x5D) or (top == "o" and c == 0x7D):
                stack.pop()
                i += 1
                continue
            raise _JsonError(i)


def json_lint_reference(data: bytes) -> tuple[bytes, int]:
    try:
        _json_check(data)
    except _JsonError as e:
        return b"error at %d\n" % e.pos, 1
    return b"ok\n", 0


def _json_ws(rng: random.Random) -> bytes:
    return b"" if rng.random() < 0.6 else _text(rng, rng.randint(1, 3), b" \t\n\r")


def _json_gen(rng: random.Random, depth: int) -> bytes:
    k = rng.random()
    if depth > 0 and k < 0.2:
        items = [_json_gen(rng, depth - 1) for _ in range(rng.randint(0, 5))]
        return b"[" + _json_ws(rng) + b",".join(_json_ws(rng) + v + _json_ws(rng) for v in items) + _json_ws(rng) + b"]"
    if depth > 0 and k < 0.4:
        mem = [_json_str(rng) + _json_ws(rng) + b":" + _json_ws(rng) + _json_gen(rng, depth - 1)
               for _ in range(rng.randint(0, 5))]
        return b"{" + _json_ws(rng) + b",".join(_json_ws(rng) + m + _json_ws(rng) for m in mem) + _json_ws(rng) + b"}"
    if k < 0.6:
        return _json_str(rng)
    if k < 0.85:
        return _json_num(rng)
    return rng.choice([b"true", b"false", b"null"])


def _json_str(rng: random.Random) -> bytes:
    parts = [b'"']
    for _ in range(rng.randint(0, 8)):
        x = rng.random()
        if x < 0.5:
            parts.append(_text(rng, rng.randint(1, 4), b"abcxyz ABC019 ~!#{}[],:"))
        elif x < 0.7:
            parts.append(b"\\" + bytes([rng.choice(b'"\\/bfnrt')]))
        elif x < 0.85:
            parts.append(b"\\u" + _text(rng, 4, b"0123456789abcdefABCDEF"))
        else:
            parts.append(bytes([rng.randrange(0x80, 0x100)]))
    parts.append(b'"')
    return b"".join(parts)


def _json_num(rng: random.Random) -> bytes:
    s = b"-" if rng.random() < 0.3 else b""
    s += b"0" if rng.random() < 0.2 else bytes([rng.choice(b"123456789")]) + _text(rng, rng.randint(0, 6), b"0123456789")
    if rng.random() < 0.3:
        s += b"." + _text(rng, rng.randint(1, 4), b"0123456789")
    if rng.random() < 0.3:
        s += bytes([rng.choice(b"eE")]) + rng.choice([b"", b"+", b"-"]) + _text(rng, rng.randint(1, 3), b"0123456789")
    return s


def json_lint_tests() -> tuple[AppTest, ...]:
    rng = random.Random(0x4A53)
    r = json_lint_reference
    hand = [
        ("empty", b""),
        ("ws_only", b" \n\t\r "),
        ("one_digit", b"7"),
        ("literals", b' [true,false,null] '),
        ("trailing_comma", b"[1,]"),
        ("missing_colon", b'{"a" 1}'),
        ("leading_zero", b"01"),
        ("truncated_literal", b"tru"),
        ("bad_literal", b"trux"),
        ("trailing_garbage", b"[1] x"),
        ("raw_tab_in_string", b'"a\tb"'),
        ("lone_minus", b"-"),
        ("frac_needs_digit", b"1.e5"),
        ("exp_needs_digit", b"1e+"),
        ("numbers_ok", b"[0, -0, 1.5, -12.25e+10, 3E-2, 1e5, 0.0e0, 123456789012345678901234567890]"),
        ("bad_escape", b'"\\x"'),
        ("unicode_escape", b'"\\u00e9\\uD83D\\uDE00\\uABCD"'),
        ("short_unicode", b'"\\u12G4"'),
        ("unterminated_string", b'"abc'),
        ("high_bytes_in_string", b'"\xc3\xa9\xff\x80"'),
        ("high_byte_outside", b"[\xff]"),
        ("empty_containers", b'{ "a" : [ ], "b" : { } , "" : ""}'),
        ("object_trailing_comma", b'{"a":1,}'),
        ("object_nonstring_key", b"{1:2}"),
        ("unclosed_array", b"[1, 2"),
        ("unclosed_object", b'{"a": [1, {"b": null}]'),
        ("deep_ok", b"[" * JSON_DEPTH + b"]" * JSON_DEPTH),
        ("deep_unclosed", b"[" * 800),
        ("deep_objects", b'{"k":' * 300 + b"null" + b"}" * 300),
        ("formfeed_not_ws", b"[1,\x0c2]"),
    ]
    ts = [_t(r, label, data) for label, data in hand]
    for i in range(4):
        doc = b""
        while len(doc) < 80:
            doc = _json_ws(rng) + _json_gen(rng, 5) + _json_ws(rng)
        ts.append(_t(r, f"random_valid_{i}", doc))
        # mutate (truncate, insert, delete or replace one byte) until the document is invalid
        while True:
            m = bytearray(doc)
            kind = i % 4
            pos = rng.randrange(len(m))
            if kind == 0:
                m = m[:pos]
            elif kind == 1:
                m.insert(pos, rng.choice(b'{}[],:"\\ 0-e.x'))
            elif kind == 2:
                del m[pos]
            else:
                m[pos] = rng.choice(b'{}[],:"\\0-e.x\x01')
            if json_lint_reference(bytes(m))[1]:
                break
        ts.append(_t(r, f"random_mutated_{i}", bytes(m)))
    items = []
    size = 2
    while size < JSON_MAX - 400:
        v = _json_gen(rng, 3)
        if size + len(v) + 1 > JSON_MAX - 1:
            break
        items.append(v)
        size += len(v) + 1
    big = b"[" + b",".join(items) + b"]"
    ts.append(_t(r, "large_valid", big))
    ts.append(_t(r, "large_error_at_end", big[:-1] + b",]"))
    return tuple(ts)


# ------------------------------------------------------------------------------------------------ registry

APP_TASKS: tuple[AppTask, ...] = (
    AppTask("wc", WC_SPEC, BSS_SIZE, 50_000_000, wc_reference, wc_tests),
    AppTask("rev_lines", REV_SPEC, BSS_SIZE, 50_000_000, rev_lines_reference, rev_lines_tests),
    AppTask("hexdump", HEX_SPEC, BSS_SIZE, 50_000_000, hexdump_reference, hexdump_tests),
    AppTask("sort_lines", SORT_SPEC, BSS_SIZE, 200_000_000, sort_lines_reference, sort_lines_tests),
    AppTask("rpn", RPN_SPEC, BSS_SIZE, 50_000_000, rpn_reference, rpn_tests),
    AppTask("base64", B64_SPEC, BSS_SIZE, 50_000_000, base64_reference, base64_tests),
    AppTask("bf", BF_SPEC, BSS_SIZE, 200_000_000, bf_reference, bf_tests),
    AppTask("json_lint", JSON_SPEC, BSS_SIZE, 50_000_000, json_lint_reference, json_lint_tests),
)


def task_by_name(name: str) -> AppTask:
    for t in APP_TASKS:
        if t.name == name:
            return t
    raise KeyError(name)
