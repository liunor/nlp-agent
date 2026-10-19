"""Disposable loopback HTTP/WebSocket upstream for nginx connection accounting.

No app, accounts, database, emails or model provider participates in this test.
Run only inside the network namespace of the networkless nginx fixture.
"""
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import socket
import sys
import threading
import time


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/ws/v1":
            key = self.headers["Sec-WebSocket-Key"]
            accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()
            self.connection.recv(1)  # Keep the upgraded request active until our client closes.
            self.close_connection = True
            return
        time.sleep(.35)  # Real proxy I/O keeps the 41 simultaneous requests overlapping.
        body = b"fixture\n"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def open_websocket(address=None):
    connection = socket.create_connection(("127.0.0.1", 80), timeout=8)
    key = base64.b64encode(b"classroom-test-1").decode()
    headers = ["GET /ws/v1 HTTP/1.1", "Host: 127.0.0.1", "Upgrade: websocket",
               "Connection: Upgrade", "Sec-WebSocket-Version: 13", "Sec-WebSocket-Key: " + key]
    if address:
        headers.append("X-Forwarded-For: " + address)
    connection.sendall(("\r\n".join(headers) + "\r\n\r\n").encode())
    stream = connection.makefile("rb")
    status = int(stream.readline().split()[1])
    response_headers = {}
    while True:
        line = stream.readline().strip()
        if not line:
            break
        name, value = line.decode().split(":", 1)
        response_headers[name.lower()] = value.strip()
    if status != 101:
        stream.close()
        connection.close()
        raise RuntimeError("FIXTURE_WEBSOCKET_NOT_UPGRADED_" + str(status))
    expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
    assert response_headers.get("sec-websocket-accept") == expected
    return connection, stream


def read(address=None):
    connection = http.client.HTTPConnection("127.0.0.1", 80, timeout=8)
    try:
        connection.request("GET", "/api/v1/sessions", headers={"X-Forwarded-For": address} if address else {})
        response = connection.getresponse()
        response.read()
        return response.status, response.getheader("Retry-After")
    finally:
        connection.close()


def main(mode):
    if mode not in {"two-tabs", "per-ip-bound", "global-bound"}:
        raise ValueError("UNKNOWN_FIXTURE_SCENARIO")
    upstream = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    upstream.daemon_threads = True
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    held = []
    try:
        count = {"two-tabs": 82, "per-ip-bound": 256, "global-bound": 320}[mode]
        for index in range(count):
            address = "203.0.113." + str(index % 200 + 1) if mode == "global-bound" else None
            held.append(open_websocket(address))
        if mode == "two-tabs":
            barrier = threading.Barrier(41)

            def simultaneous_read(_):
                barrier.wait(timeout=20)
                return read()

            with ThreadPoolExecutor(max_workers=41) as pool:
                results = list(pool.map(simultaneous_read, range(41)))
        else:
            results = [read("203.0.113.250" if mode == "global-bound" else None)]
        print(json.dumps({"held_websockets": len(held), "requests": len(results),
                          "codes": dict(Counter(str(code) for code, _ in results)),
                          "all_rejections_have_retry_after": all(header == "1" for code, header in results if code == 429),
                          "successes_have_no_retry_after": all(header is None for code, header in results if code == 200)}))
    finally:
        for connection, stream in held:
            stream.close()
            connection.close()
        upstream.shutdown()
        upstream.server_close()


if __name__ == "__main__":
    main(sys.argv[1])
