"""Loopback-only UI test gateway; validates upstream TLS and keeps production cookie settings."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

UPSTREAM = "https://fiap-switch.13.140.191.31.sslip.io"
LOCAL = "http://localhost:8011"
client = httpx.Client(base_url=UPSTREAM, proxy="socks5://127.0.0.1:1080", timeout=30)


class Handler(BaseHTTPRequestHandler):
    def forward(self):
        headers = {
            key.lower(): value
            for key, value in self.headers.items()
            if key.lower()
            not in ("host", "connection", "content-length", "accept-encoding")
        }
        headers['cookie'] = self.headers.get('Cookie', '')
        if headers.get("origin") == LOCAL:
            headers["origin"] = UPSTREAM
        try:
            result = client.request(
                self.command,
                self.path,
                headers=headers,
                content=self.rfile.read(int(self.headers.get("Content-Length", "0"))),
            )
        except httpx.HTTPError:
            self.send_error(502, "Upstream connection failed.")
            return
        self.send_response(result.status_code)
        for key, value in result.headers.multi_items():
            if key.lower() not in (
                "content-length",
                "content-encoding",
                "transfer-encoding",
                "connection",
            ):
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(result.content)))
        self.end_headers()
        self.wfile.write(result.content)

    do_GET = do_POST = do_PUT = forward

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    print("UI validation gateway: http://localhost:8011", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8011), Handler).serve_forever()
