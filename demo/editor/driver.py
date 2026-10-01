"""Runs INSIDE the sandbox container (python:3.12-slim, standard library only): for each scenario in
/work/scenarios.json, start /work/edit on a fresh pseudo-terminal of a fixed size, send the keys, wait for
it to exit, and report the files, exit status, terminal state and screen bytes as JSON on stdout."""

import base64
import fcntl
import json
import os
import select
import struct
import subprocess
import sys
import termios
import time

ROWS, COLS = 24, 80
START_WAIT, KEY_GAP, EXIT_WAIT = 0.4, 0.05, 5.0


def drain(fd: int, out: bytearray, seconds: float) -> None:
    end = time.monotonic() + seconds
    while True:
        left = end - time.monotonic()
        if left <= 0:
            return
        r, _, _ = select.select([fd], [], [], left)
        if not r:
            return
        try:
            data = os.read(fd, 65536)
        except OSError:
            return
        if not data:
            return
        out += data


def run(s: dict) -> dict:
    d = f"/work/run/{s['name']}"
    os.makedirs(d, exist_ok=True)
    for name, content in s["files"].items():
        with open(os.path.join(d, name), "wb") as f:
            f.write(content.encode())
    master, slave = os.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
    before = termios.tcgetattr(slave)
    screen = bytearray()
    p = subprocess.Popen(["/work/edit", s["arg"]], cwd=d, stdin=slave, stdout=slave, stderr=slave,
                         start_new_session=True, close_fds=True)
    drain(master, screen, START_WAIT)
    for k in s["keys"]:
        if p.poll() is not None:
            break
        try:
            os.write(master, k.encode())
        except OSError:
            break
        drain(master, screen, KEY_GAP)
    status, timed_out = None, False
    end = time.monotonic() + EXIT_WAIT
    while time.monotonic() < end:
        status = p.poll()
        if status is not None:
            break
        drain(master, screen, 0.05)
    if status is None:
        timed_out = True
        p.kill()
        status = p.wait()
    drain(master, screen, 0.1)
    after = termios.tcgetattr(slave)
    os.close(master)
    os.close(slave)
    files = {}
    for name in s["expect"]:
        path = os.path.join(d, name)
        files[name] = base64.b64encode(open(path, "rb").read()).decode() if os.path.exists(path) else None
    return {"name": s["name"], "status": status, "timed_out": timed_out, "restored": before == after,
            "files": files, "screen": base64.b64encode(bytes(screen)).decode()}


def main() -> None:
    scenarios = json.load(open("/work/scenarios.json"))
    only = sys.argv[1:]
    print(json.dumps([run(s) for s in scenarios if not only or s["name"] in only]))


if __name__ == "__main__":
    main()
