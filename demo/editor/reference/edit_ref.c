/* A C implementation of SPEC.md, used ONLY to validate the sandbox tests (a correct editor must pass every
   scenario). It is not the demo: the demo binary is written by the model in assembly. */
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <termios.h>
#include <unistd.h>

#define MAXL 65536
#define MAXC 4096
static char *L[MAXL];
static int len[MAXL], n, cy, cx, rowoff, coloff, rows = 24, cols = 80, modified, warn;
static const char *fname;
static struct termios orig;
static char fb[1 << 20];
static int fl;

static void ap(const char *s, int k) { memcpy(fb + fl, s, k); fl += k; }
static void aps(const char *s) { ap(s, strlen(s)); }
static void wr(const char *s, int k) { while (k > 0) { int w = write(1, s, k); if (w <= 0) return; s += w; k -= w; } }
static void newline_at(int at) { memmove(L + at + 1, L + at, (n - at) * sizeof *L); memmove(len + at + 1, len + at, (n - at) * sizeof *len); L[at] = calloc(MAXC + 1, 1); len[at] = 0; n++; }
static void del_line(int at) { free(L[at]); memmove(L + at, L + at + 1, (n - at - 1) * sizeof *L); memmove(len + at, len + at + 1, (n - at - 1) * sizeof *len); n--; }

static void load(void) {
  int fd = open(fname, O_RDONLY);
  n = 0; newline_at(0);
  if (fd < 0) return;
  char c; int y = 0, any = 0, last = '\n';
  while (read(fd, &c, 1) == 1) {
    any = 1; last = c;
    if (c == '\n') { newline_at(++y); continue; }
    L[y][len[y]++] = c;
  }
  close(fd);
  if (any && last == '\n') del_line(n - 1);
}

static void save(void) {
  int fd = open(fname, O_WRONLY | O_CREAT | O_TRUNC, 0644);
  if (fd < 0) return;
  for (int i = 0; i < n; i++) { write(fd, L[i], len[i]); write(fd, "\n", 1); }
  close(fd); modified = 0;
}

static void draw(void) {
  int t = rows - 1;
  if (cy < rowoff) rowoff = cy;
  if (cy >= rowoff + t) rowoff = cy - t + 1;
  if (cx < coloff) coloff = cx;
  if (cx >= coloff + cols) coloff = cx - cols + 1;
  fl = 0; aps("\x1b[?25l\x1b[H");
  for (int r = 0; r < t; r++) {
    int y = r + rowoff;
    if (y >= n) aps("~");
    else { int k = len[y] - coloff; if (k < 0) k = 0; if (k > cols) k = cols; ap(L[y] + coloff, k); }
    aps("\x1b[K\r\n");
  }
  char s[256];
  if (warn) snprintf(s, sizeof s, "unsaved changes: press Ctrl-Q again to quit");
  else snprintf(s, sizeof s, "%s  %d:%d%s", fname, cy + 1, cx + 1, modified ? "  [modified]" : "");
  aps(s); aps("\x1b[K");
  snprintf(s, sizeof s, "\x1b[%d;%dH\x1b[?25h", cy - rowoff + 1, cx - coloff + 1); aps(s);
  wr(fb, fl);
}

static int rd(void) { unsigned char c; return read(0, &c, 1) == 1 ? c : -1; }
static void clampx(void) { if (cx > len[cy]) cx = len[cy]; }

int main(int argc, char **argv) {
  if (argc < 2) { write(2, "usage: edit FILE\n", 17); return 1; }
  fname = argv[1];
  if (ioctl(0, TCGETS, &orig) < 0) { write(2, "not a terminal\n", 15); return 1; }
  struct termios raw = orig;
  raw.c_iflag &= ~(BRKINT | ICRNL | INPCK | ISTRIP | IXON); raw.c_oflag &= ~OPOST; raw.c_cflag |= CS8;
  raw.c_lflag &= ~(ECHO | ICANON | IEXTEN | ISIG); raw.c_cc[VMIN] = 1; raw.c_cc[VTIME] = 0;
  ioctl(0, TCSETS, &raw);
  struct winsize ws;
  if (ioctl(1, TIOCGWINSZ, &ws) == 0 && ws.ws_row && ws.ws_col) { rows = ws.ws_row; cols = ws.ws_col; }
  load();
  for (;;) {
    draw();
    int c = rd(), was = warn; warn = 0;
    if (c < 0) break;
    if (c == 0x11) { if (!modified || was) break; warn = 1; continue; }
    if (c == 0x13) { save(); continue; }
    if (c == 0x1b) {
      int a = rd(), b = rd(), key = 0;
      if (a == '[' && b >= '0' && b <= '9') { int t = rd(); if (t == '~') key = b; }
      else if (a == '[' || a == 'O') key = b;
      switch (key) {
        case 'A': if (cy > 0) cy--; clampx(); break;
        case 'B': if (cy < n - 1) cy++; clampx(); break;
        case 'C': if (cx < len[cy]) cx++; else if (cy < n - 1) { cy++; cx = 0; } break;
        case 'D': if (cx > 0) cx--; else if (cy > 0) { cy--; cx = len[cy]; } break;
        case 'H': case '1': cx = 0; break;
        case 'F': case '4': cx = len[cy]; break;
        case '5': cy -= rows - 1; if (cy < 0) cy = 0; clampx(); break;
        case '6': cy += rows - 1; if (cy > n - 1) cy = n - 1; clampx(); break;
        case '3':
          if (cx < len[cy]) { memmove(L[cy] + cx, L[cy] + cx + 1, len[cy] - cx - 1); len[cy]--; modified = 1; }
          else if (cy < n - 1) { memcpy(L[cy] + len[cy], L[cy + 1], len[cy + 1]); len[cy] += len[cy + 1]; del_line(cy + 1); modified = 1; }
          break;
      }
      continue;
    }
    if (c == '\r' || c == '\n') {
      newline_at(cy + 1); memcpy(L[cy + 1], L[cy] + cx, len[cy] - cx); len[cy + 1] = len[cy] - cx; len[cy] = cx;
      cy++; cx = 0; modified = 1; continue;
    }
    if (c == 0x7f || c == 8) {
      if (cx > 0) { memmove(L[cy] + cx - 1, L[cy] + cx, len[cy] - cx); len[cy]--; cx--; modified = 1; }
      else if (cy > 0) { cx = len[cy - 1]; memcpy(L[cy - 1] + cx, L[cy], len[cy]); len[cy - 1] += len[cy]; del_line(cy); cy--; modified = 1; }
      continue;
    }
    if (c >= 0x20 && c <= 0x7e) { memmove(L[cy] + cx + 1, L[cy] + cx, len[cy] - cx); L[cy][cx++] = c; len[cy]++; modified = 1; }
  }
  wr("\x1b[2J\x1b[H", 7);
  ioctl(0, TCSETS, &orig);
  return 0;
}
