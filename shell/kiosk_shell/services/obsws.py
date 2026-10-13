"""A small obs-websocket 5 client (the protocol built into OBS Studio 28 and later), standard library only.

WebSocket (RFC 6455) over a local TCP socket, text frames carrying obs-websocket JSON messages:
Hello (op 0) -> Identify (op 1, with the password's challenge answer) -> Identified (op 2), then
Request (op 6) / RequestResponse (op 7). Events are not subscribed to.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import uuid

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"  # RFC 6455's fixed key suffix


class OBSError(Exception):
    """OBS is unreachable, refused the password, or a request failed."""


def auth_answer(password: str, salt: str, challenge: str) -> str:
    """obs-websocket 5: base64(sha256(base64(sha256(password + salt)) + challenge))."""
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


def encode_frame(payload: bytes, opcode: int = 0x1) -> bytes:
    """One final, masked client frame (clients must mask)."""
    header = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126:
        header += bytes([0x80 | n])
    elif n < 1 << 16:
        header += bytes([0x80 | 126]) + struct.pack("!H", n)
    else:
        header += bytes([0x80 | 127]) + struct.pack("!Q", n)
    mask = os.urandom(4)
    return header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


class Client:
    def __init__(self, host: str = "127.0.0.1", port: int = 4455, password: str = "", timeout: float = 5.0) -> None:
        self.host, self.port, self.password, self.timeout = host, port, password, timeout
        self.sock: socket.socket | None = None
        self.buffer = b""
        self.version = ""

    # --- the WebSocket layer ---

    def _recv_exact(self, n: int) -> bytes:
        while len(self.buffer) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise OBSError("OBS closed the connection.")
            self.buffer += chunk
        data, self.buffer = self.buffer[:n], self.buffer[n:]
        return data

    def _read_frame(self) -> tuple[int, bytes]:
        b1, b2 = self._recv_exact(2)
        opcode, n = b1 & 0x0F, b2 & 0x7F
        if n == 126:
            n = struct.unpack("!H", self._recv_exact(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4) if b2 & 0x80 else b""
        payload = self._recv_exact(n)
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return (opcode | (0x80 if b1 & 0x80 else 0)), payload

    def _read_message(self) -> dict:
        parts = []
        while True:
            head, payload = self._read_frame()
            opcode, final = head & 0x0F, bool(head & 0x80)
            if opcode == 0x8:
                raise OBSError("OBS closed the connection" + (
                    f": {payload[2:].decode(errors='replace')}" if len(payload) > 2 else "."))
            if opcode == 0x9:  # ping
                self.sock.sendall(encode_frame(payload, 0xA))
                continue
            if opcode in (0x1, 0x2, 0x0):
                parts.append(payload)
                if final:
                    return json.loads(b"".join(parts))

    def _send(self, message: dict) -> None:
        self.sock.sendall(encode_frame(json.dumps(message).encode()))

    def connect(self) -> "Client":
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError:
            raise OBSError("OBS is not running (no answer on its WebSocket port).") from None
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET / HTTP/1.1\r\nHost: {self.host}:{self.port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                           "Sec-WebSocket-Protocol: obswebsocket.json\r\n\r\n").encode())
        while b"\r\n\r\n" not in self.buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise OBSError("OBS refused the WebSocket connection.")
            self.buffer += chunk
        head, self.buffer = self.buffer.split(b"\r\n\r\n", 1)
        lines = head.decode(errors="replace").split("\r\n")
        headers = {k.strip().lower(): v.strip() for k, _, v in (line.partition(":") for line in lines[1:])}
        accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        if " 101 " not in lines[0] + " " or headers.get("sec-websocket-accept") != accept:
            raise OBSError("OBS refused the WebSocket connection.")

        hello = self._read_message()
        if hello.get("op") != 0:
            raise OBSError("Unexpected first message from OBS.")
        self.version = hello["d"].get("obsWebSocketVersion", "")
        identify = {"rpcVersion": 1, "eventSubscriptions": 0}
        auth = hello["d"].get("authentication")
        if auth:
            identify["authentication"] = auth_answer(self.password, auth["salt"], auth["challenge"])
        self._send({"op": 1, "d": identify})
        try:
            reply = self._read_message()
        except OBSError as error:
            raise OBSError("OBS refused the password.") if "4009" in str(error) or "Authentication" in str(
                error) else error
        if reply.get("op") != 2:
            raise OBSError("OBS did not accept the connection.")
        return self

    def call(self, request: str, data: dict | None = None) -> dict:
        """Send one request and return its responseData; raises OBSError when OBS says it failed."""
        request_id = uuid.uuid4().hex
        message = {"requestType": request, "requestId": request_id}
        if data is not None:
            message["requestData"] = data
        self._send({"op": 6, "d": message})
        while True:
            reply = self._read_message()
            if reply.get("op") == 7 and reply["d"].get("requestId") == request_id:
                status = reply["d"].get("requestStatus", {})
                if not status.get("result"):
                    raise OBSError(f"{request} failed: {status.get('comment') or status.get('code')}")
                return reply["d"].get("responseData") or {}

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.sendall(encode_frame(struct.pack("!H", 1000), 0x8))
            except OSError:
                pass
            self.sock.close()
            self.sock = None

    def __enter__(self) -> "Client":
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.close()
