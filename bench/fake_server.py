"""Local fake manwa server for benchmarking — mimics API shapes + image CDN.

Usage:  python bench/fake_server.py [port] [delay_ms]
Env:    IMG_DELAY (seconds, per image request), IMG_SIZE (bytes)
"""

import os
import random
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

CHAPTERS = int(os.environ.get("BENCH_CHAPTERS", "12"))
PICS_PER_CH = int(os.environ.get("BENCH_PICS", "50"))
IMG_DELAY = float(os.environ.get("IMG_DELAY", "0.15"))
API_DELAY = float(os.environ.get("API_DELAY", "0.12"))
IMG_SIZE = int(os.environ.get("IMG_SIZE", "120000"))

_BODY = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBPVP8 " + bytes(
    random.getrandbits(8) for _ in range(IMG_SIZE)
)

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
BASE = f"http://127.0.0.1:{PORT}"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body: bytes, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        q = parse_qs(u.query)

        if path == "/" or path.startswith("/comic/"):
            time.sleep(API_DELAY)
            html = "<html><head><title>测试漫画 - 漫蛙</title></head><body>《测试漫画》</body></html>"
            return self._send(200, html.encode(), "text/html; charset=utf-8")

        if path.startswith("/api/comic/") and path.endswith("/chapters"):
            time.sleep(API_DELAY)
            import json
            chs = [
                {
                    "id": 1000 + i,
                    "title": f"第{i + 1}话 测试章节标题{i + 1}",
                    "sortId": i + 1,
                    "picCount": PICS_PER_CH,
                    "isVip": False,
                }
                for i in range(CHAPTERS)
            ]
            return self._send(200, json.dumps({"code": 200, "data": {"list": chs}}).encode())

        if path.startswith("/api/comic/image/"):
            time.sleep(API_DELAY)
            import json
            ch_id = int(path.rsplit("/", 1)[-1])
            page = int(q.get("page", ["1"])[0])
            page_size = int(q.get("page_size", ["60"])[0])
            src = q.get("image_source", [BASE])[0]
            start = (page - 1) * page_size
            end = min(start + page_size, PICS_PER_CH)
            imgs = [
                {"url": f"{src}/img/{ch_id}/{i:04d}.jpg"}
                for i in range(start, end)
            ]
            total_pages = (PICS_PER_CH + page_size - 1) // page_size
            body = {
                "code": 200,
                "data": {
                    "images": imgs,
                    "pagination": {"total_pages": total_pages, "page": page},
                },
            }
            return self._send(200, json.dumps(body).encode())

        if path.startswith("/img/"):
            time.sleep(IMG_DELAY)
            if random.random() < float(os.environ.get("IMG_FAIL_RATE", "0")):
                return self._send(404, b"nope", "text/plain")
            return self._send(200, _BODY, "image/webp")

        return self._send(404, b'{"code":404}')


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    print(f"fake server on {BASE}  chapters={CHAPTERS} pics={PICS_PER_CH} "
          f"delay={IMG_DELAY}s size={IMG_SIZE}B", flush=True)
    srv.serve_forever()
