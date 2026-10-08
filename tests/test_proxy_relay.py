"""Item 1 support (v0.2): proxy resolution + the authenticated-proxy
relay, proven against a recording stub proxy on loopback."""

import base64
import socket
import threading
import urllib.request

from ghost_hands.drivers import ChromiumDriver, _AuthProxyRelay


def test_plain_proxy_passes_through():
    d = ChromiumDriver(proxy="http://proxy.local:8080")
    assert d._proxy_launch_arg() == "http://proxy.local:8080"
    assert d._relay is None


def test_proxy_disabled_with_empty_string():
    d = ChromiumDriver(proxy="")
    assert d._proxy_launch_arg() is None


def test_authenticated_proxy_uses_relay_without_leaking_creds():
    d = ChromiumDriver(proxy="http://user:topsecret@proxy.local:3128")
    try:
        arg = d._proxy_launch_arg()
        assert arg.startswith("http://127.0.0.1:")
        assert "topsecret" not in arg and "proxy.local" not in arg
        assert d._relay is not None
    finally:
        if d._relay is not None:
            d._relay.stop()


class _RecordingProxy:
    """A one-shot stub upstream proxy: records the CONNECT request line +
    Proxy-Authorization header, answers 200, then pipes to a fixed target."""

    def __init__(self, target_host: str, target_port: int):
        self.target = (target_host, target_port)
        self.connect_line = ""
        self.auth_header = None
        self._srv = socket.socket()
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(5)
        self.port = self._srv.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        try:
            client, _ = self._srv.accept()
            stream = client.makefile("rb", buffering=0)
            self.connect_line = stream.readline(65536).decode("latin1").strip()
            while True:
                line = stream.readline(65536).decode("latin1")
                if line in ("\r\n", "\n", ""):
                    break
                if line.lower().startswith("proxy-authorization:"):
                    self.auth_header = line.split(":", 1)[1].strip()
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            up = socket.create_connection(self.target, timeout=10)

            def pipe(a, b):
                try:
                    while True:
                        data = a.recv(65536)
                        if not data:
                            break
                        b.sendall(data)
                except OSError:
                    pass

            t = threading.Thread(target=pipe, args=(client, up), daemon=True)
            t.start()
            pipe(up, client)
            client.close()
            up.close()
        except OSError:
            pass

    def close(self):
        try:
            self._srv.close()
        except OSError:
            pass


def _origin_server():
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]

    def serve():
        try:
            conn, _ = srv.accept()
            conn.recv(65536)
            body = b"relay-origin-ok"
            conn.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
                + f"Content-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
            conn.close()
        except OSError:
            pass

    threading.Thread(target=serve, daemon=True).start()
    return srv, port


def test_relay_injects_proxy_authorization_on_connect():
    origin_srv, origin_port = _origin_server()
    stub = _RecordingProxy("127.0.0.1", origin_port)
    upstream = f"http://relayuser:relaypass@127.0.0.1:{stub.port}"
    relay = _AuthProxyRelay(upstream)
    relay_url = relay.start()
    try:
        # Force a CONNECT through the relay to a NON-loopback name, so the
        # relay goes upstream instead of direct-connecting. The stub proxy
        # ignores the name and pipes to the origin anyway.
        relay_port = int(relay_url.rsplit(":", 1)[1])
        raw = socket.create_connection(("127.0.0.1", relay_port), timeout=10)
        raw.sendall(b"CONNECT example.invalid:443 HTTP/1.1\r\nHost: example.invalid:443\r\n\r\n")
        stream = raw.makefile("rb", buffering=0)
        status = stream.readline(65536).decode("latin1").strip()
        assert "200" in status, status
        raw.sendall(b"GET / HTTP/1.0\r\n\r\n")  # tunneled bytes reach the origin
        raw.settimeout(10)
        chunks = []
        while sum(len(c) for c in chunks) < 65536:
            try:
                data = raw.recv(65536)
            except socket.timeout:
                break
            if not data:
                break
            chunks.append(data)
            if b"relay-origin-ok" in b"".join(chunks):
                break
        response = b"".join(chunks).decode("latin1")
        assert "relay-origin-ok" in response
        raw.close()
    finally:
        relay.stop()
        stub.close()
        origin_srv.close()
    assert stub.connect_line.startswith("CONNECT example.invalid:443")
    expected = "Basic " + base64.b64encode(b"relayuser:relaypass").decode()
    assert stub.auth_header == expected
