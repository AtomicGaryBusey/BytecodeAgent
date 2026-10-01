Program `edit`: a small full-screen terminal text editor in the style of kilo.

Invocation: `edit FILE`. argv[1] is the file name (a NUL-terminated string; the Linux stack at `_start`
holds argc, then the argv pointers, then a NULL, then envp). With no argument, write
`usage: edit FILE\n` to standard error (fd 2) and exit with status 1.

Buffer model:
- The buffer is a list of lines; a line is a sequence of bytes without the newline byte (0x0A).
- Loading: if FILE exists, its bytes are split at every 0x0A into lines. If the file ends with 0x0A, that
  final newline does not create an extra empty line. An empty or missing file gives a buffer of one empty
  line. A missing file is not an error (it is created on save). Bytes are kept as they are (no special
  handling of 0x0D or tabs; you may display a byte < 0x20 or 0x7F as `?`).
- Saving (Ctrl-S): write every line followed by one 0x0A byte, replacing the whole file: open with
  O_WRONLY|O_CREAT|O_TRUNC and mode 0644 (or truncate it with ftruncate). After a successful save the buffer
  is no longer "modified".
- Limits you may assume: the file is at most 1 MiB, at most 65536 lines, every line at most 4096 bytes,
  also after editing. Keep the buffer in `.bss`; how you lay it out is up to you.

Cursor: a position (line, column) with 0 <= line < number of lines and 0 <= column <= length of that line.
It starts at (0, 0).

Keys (input arrives as raw bytes; each key below is sent as one write, but several keys may arrive in one
read, so process the input byte by byte):
- Printable byte 0x20..0x7E: insert it at the cursor, move the cursor right by one.
- Enter (0x0D, also accept 0x0A): split the line at the cursor; the part after the cursor becomes a new
  line below; the cursor moves to column 0 of that new line.
- Backspace (0x7F, also accept 0x08): at column > 0 delete the byte before the cursor and move left; at
  column 0 of a line other than the first, join this line onto the end of the previous one, with the cursor
  at the join point; at (0, 0) do nothing.
- Delete (ESC [ 3 ~): delete the byte under the cursor; at the end of a line other than the last, join the
  next line onto this one; at the end of the last line do nothing. The cursor does not move.
- Left (ESC [ D): column - 1; at column 0 move to the end of the previous line (nothing at line 0).
- Right (ESC [ C): column + 1; at the end of a line move to column 0 of the next line (nothing on the last
  line).
- Up (ESC [ A) / Down (ESC [ B): line - 1 / + 1 (nothing at the first / last line); the column stays the
  same if the new line is long enough, otherwise it becomes the length of the new line.
- Home (ESC [ H, also ESC [ 1 ~ and ESC O H): column 0. End (ESC [ F, also ESC [ 4 ~ and ESC O F): column =
  line length.
- Page Up (ESC [ 5 ~) / Page Down (ESC [ 6 ~): move the cursor up / down by T lines, where T is the number of
  text rows on the screen (see Screen), clamped to the first / last line; the column rule is the same as
  for Up/Down.
- Ctrl-S (0x13): save. Ctrl-Q (0x11): quit; if the buffer is modified, the first Ctrl-Q only shows a warning
  in the status line (e.g. `unsaved changes: press Ctrl-Q again to quit`) and a second Ctrl-Q pressed
  immediately after it quits without saving (any other key in between cancels the warning).
- Any other byte or escape sequence is ignored (it must not crash or change the buffer).
"Modified" means the buffer changed since it was loaded or last saved.

Screen:
- Get the terminal size with ioctl(1, TIOCGWINSZ, &winsize) (struct winsize: ws_row u16, ws_col u16,
  ws_xpixel u16, ws_ypixel u16); if it fails or gives 0, use 24 rows x 80 columns.
- The top rows - 1 rows show text lines (T = rows - 1 text rows); the last row is the status bar.
- Scrolling: keep the cursor visible. Vertical: the first displayed line changes so the cursor line is on
  screen. Horizontal: each displayed line starts at the same column offset, which changes so the cursor
  column is on screen (lines longer than the screen width are cut at the right edge). Rows below the end of
  the buffer show `~`.
- The status bar shows the file name, the cursor position as `line:column` (1-based), and `[modified]` when
  the buffer is modified (or the quit warning when it is active).
- Redraw after every key: hide the cursor (ESC [ ? 25 l), move home (ESC [ H), write each text row followed
  by "erase to end of line" (ESC [ K) and "\r\n", write the status bar, place the cursor with
  ESC [ <row> ; <col> H (1-based), show the cursor (ESC [ ? 25 h). Build the frame in a buffer and write it
  with as few write calls as you like.

Terminal:
- At start read the terminal settings with ioctl(0, TCGETS, &orig) and switch to raw mode with
  ioctl(0, TCSETS, &raw), where raw is orig with these bits cleared (as kilo does):
  c_iflag: BRKINT 0x2, ICRNL 0x100, INPCK 0x10, ISTRIP 0x20, IXON 0x400;
  c_oflag: OPOST 0x1; c_cflag: set CS8 0x30; c_lflag: ECHO 0x8, ICANON 0x2, IEXTEN 0x8000, ISIG 0x1;
  c_cc[VMIN = 6] = 1, c_cc[VTIME = 5] = 0 (blocking one-byte reads are fine).
- On quit, clear the screen (ESC [ 2 J, ESC [ H), restore the original settings with ioctl(0, TCSETS,
  &orig) and exit with status 0. If TCGETS fails (input is not a terminal), print an error to fd 2 and exit
  with status 1.
- struct termios on AArch64 Linux (the kernel's, 36 bytes used here): c_iflag u32 at 0, c_oflag u32 at 4,
  c_cflag u32 at 8, c_lflag u32 at 12, c_line u8 at 16, c_cc[19] u8 at 17..35. Reserve at least 64 bytes for
  each copy.
