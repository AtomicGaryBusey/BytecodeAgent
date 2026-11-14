"""Request recorder for CLI audits (eval/audits/README.md). Local audit recorder: listens on 127.0.0.1:<port>, forwards each request unchanged to https://<host>, streams the
reply back, and saves the request method, path and JSON body (never headers, so no credentials) to <outdir>/reqNNN.json, and a GET's response body to
<outdir>/reqNNN.response.json.
usage: python3 recorder.py <outdir> <port> <host>"""
import http.client
import http.server
import json
import os
import sys
import threading

OUT, PORT, HOST = sys.argv[1], int(sys.argv[2]), sys.argv[3]
os.makedirs(OUT, exist_ok=True)
N = [0]
LOCK = threading.Lock()
HOP = {"host", "connection", "keep-alive", "transfer-encoding", "content-length", "accept-encoding"}


class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _go(self):
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n) if n else b""
        with LOCK:
            N[0] += 1
            k = N[0]
        try:
            parsed = json.loads(body) if body else None
        except ValueError:
            parsed = body[:2000].decode("utf-8", "replace")
        with open(f"{OUT}/req{k:03d}.json", "w") as f:
            json.dump({"n": k, "method": self.command, "path": self.path, "body": parsed}, f)
        fwd = {h: v for h, v in self.headers.items() if h.lower() not in HOP}
        fwd["Host"] = HOST
        fwd["Accept-Encoding"] = "identity"
        c = http.client.HTTPSConnection(HOST, timeout=600)
        c.request(self.command, self.path, body=body or None, headers=fwd)
        r = c.getresponse()
        self.send_response(r.status)
        for h, v in r.getheaders():
            if h.lower() not in HOP:
                self.send_header(h, v)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        got = bytearray()
        while True:
            chunk = r.read1(65536)
            if not chunk:
                break
            got += chunk
            self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
            self.wfile.flush()
        if got:  # catalogs, and model responses (raw/ is gitignored; response bodies carry no credential)
            with open(f"{OUT}/req{k:03d}.response.json", "wb") as fh:
                fh.write(bytes(got))
        self.wfile.write(b"0\r\n\r\n")
        c.close()

    do_GET = do_POST = _go


http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
