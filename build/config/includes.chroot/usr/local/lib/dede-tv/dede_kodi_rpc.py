"""Minimal, bounded client for Kodi's loopback JSON-RPC TCP port (9090).

Shared by the Dede TV helpers (autoplay, guard). Never connects anywhere
but 127.0.0.1. Kodi interleaves notifications (no "id") with replies on
the same socket; replies are matched strictly by integer id.
"""
import json
import socket
import time

HOST, PORT = "127.0.0.1", 9090
MAX_REPLY = 4 * 1024 * 1024
_request_id = [31000]


class RpcError(RuntimeError):
    pass


def iter_documents(text, position=0):
    """Yield (document, end) for each complete JSON value in text."""
    decoder = json.JSONDecoder()
    while True:
        while position < len(text) and text[position].isspace():
            position += 1
        if position >= len(text):
            return
        try:
            document, position = decoder.raw_decode(text, position)
        except json.JSONDecodeError:
            return
        yield document, position


def rpc(method, params=None, timeout=5.0):
    _request_id[0] += 1
    request_id = _request_id[0]
    request = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        request["params"] = params
    deadline = time.monotonic() + timeout
    with socket.create_connection((HOST, PORT), timeout=timeout) as connection:
        connection.sendall(json.dumps(request, separators=(",", ":")).encode() + b"\n")
        buffer = b""
        while len(buffer) <= MAX_REPLY:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            connection.settimeout(remaining)
            part = connection.recv(65536)
            if not part:
                break
            buffer += part
            try:
                text = buffer.decode("utf-8")
            except UnicodeDecodeError:
                continue
            for document, _ in iter_documents(text):
                if isinstance(document, dict) and type(document.get("id")) is int \
                        and document["id"] == request_id:
                    if "error" in document:
                        error = document["error"]
                        code = error.get("code") if isinstance(error, dict) else None
                        raise RpcError(f"{method}: error {code}")
                    if "result" not in document:
                        raise RpcError(f"{method}: missing result")
                    return document["result"]
    raise RpcError(f"{method}: no bounded reply")


class Notifications:
    """Persistent connection receiving Kodi notifications (e.g. Player.OnPlay)."""

    def __init__(self, timeout=5.0):
        self.sock = socket.create_connection((HOST, PORT), timeout=timeout)
        self.sock.setblocking(False)
        self.buffer = b""

    def fileno(self):
        return self.sock.fileno()

    def read(self):
        """Return complete notification dicts received so far; raise on EOF."""
        try:
            part = self.sock.recv(65536)
        except BlockingIOError:
            return []
        if not part:
            raise ConnectionError("Kodi closed the notification connection")
        self.buffer += part
        if len(self.buffer) > MAX_REPLY:
            self.buffer = b""
            return []
        try:
            text = self.buffer.decode("utf-8")
        except UnicodeDecodeError:
            return []
        documents, consumed = [], 0
        for document, end in iter_documents(text):
            consumed = end
            if isinstance(document, dict) and "id" not in document \
                    and isinstance(document.get("method"), str):
                documents.append(document)
        self.buffer = text[consumed:].encode("utf-8")
        return documents

    def close(self):
        self.sock.close()
