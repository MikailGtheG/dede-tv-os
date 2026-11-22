"""Minimal, bounded client for Kodi's loopback JSON-RPC TCP port (9090).

Shared by the Dede TV helpers (autoplay, guard). Never connects anywhere
but 127.0.0.1. Kodi interleaves notifications (no "id") with replies on
the same socket; replies are matched strictly by integer id.
"""
import json
import select
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


class Connection:
    """One persistent connection for requests *and* notifications.

    Kodi 21 crashed in QEMU right after "JSONRPC Server: Disconnection
    detected" while a helper opened several short-lived connections every
    two seconds. Long-running helpers therefore keep a single socket open
    and multiplex: replies are matched by id, notifications are queued.
    """

    def __init__(self, timeout=5.0, sock=None):
        self.sock = sock or socket.create_connection((HOST, PORT), timeout=timeout)
        self.sock.setblocking(False)
        self.buffer = b""
        self.pending = []
        self.replies = {}
        self.next_id = 50000

    def fileno(self):
        return self.sock.fileno()

    def _receive(self, wait):
        readable, _, _ = select.select([self.sock], [], [], max(0.0, wait))
        if not readable:
            return
        # Drain everything available: Kodi sends notifications synchronously,
        # so a slow reader could back up Kodi's own send path.
        while True:
            try:
                part = self.sock.recv(65536)
            except BlockingIOError:
                break
            if not part:
                raise ConnectionError("Kodi closed the JSON-RPC connection")
            self.buffer += part
            if len(self.buffer) > MAX_REPLY:
                raise ConnectionError("oversized JSON-RPC data from Kodi")
        try:
            text = self.buffer.decode("utf-8")
        except UnicodeDecodeError:
            return
        consumed = 0
        for document, end in iter_documents(text):
            consumed = end
            if not isinstance(document, dict):
                continue
            if type(document.get("id")) is int:
                self.replies[document["id"]] = document
            elif isinstance(document.get("method"), str):
                self.pending.append(document)
        self.buffer = text[consumed:].encode("utf-8")

    def call(self, method, params=None, timeout=5.0):
        self.next_id += 1
        request_id = self.next_id
        request = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            request["params"] = params
        self.sock.setblocking(True)
        self.sock.settimeout(timeout)
        try:
            self.sock.sendall(json.dumps(request, separators=(",", ":")).encode() + b"\n")
        finally:
            self.sock.setblocking(False)
        deadline = time.monotonic() + timeout
        while request_id not in self.replies:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RpcError(f"{method}: no bounded reply")
            self._receive(remaining)
        document = self.replies.pop(request_id)
        self.replies.clear()  # drop stale replies of timed-out calls
        if "error" in document:
            error = document["error"]
            code = error.get("code") if isinstance(error, dict) else None
            raise RpcError(f"{method}: error {code}")
        if "result" not in document:
            raise RpcError(f"{method}: missing result")
        return document["result"]

    def notifications(self, wait=0.0):
        """Queued plus newly received notifications; raises on disconnect."""
        self._receive(wait)
        documents, self.pending = self.pending, []
        return documents

    def close(self):
        self.sock.close()
