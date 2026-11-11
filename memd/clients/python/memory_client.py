"""Small synchronous client for the memd JSONL protocol."""

from __future__ import annotations

import json
import socket
import threading
from typing import Any


class MemoryServiceError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


class MemdClient:
    def __init__(self, *, socket_path: str | None = None, tcp_address: tuple[str, int] | None = None,
                 timeout: float = 5.0):
        if (socket_path is None) == (tcp_address is None):
            raise ValueError("provide exactly one of socket_path or tcp_address")
        self.socket_path = socket_path
        self.tcp_address = tcp_address
        self.timeout = timeout
        self._socket: socket.socket | None = None
        self._reader: Any = None
        self._lock = threading.Lock()
        self._request_id = 0

    def _connect(self) -> None:
        if self._socket is not None:
            return
        if self.socket_path is not None:
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.settimeout(self.timeout)
            try:
                connection.connect(self.socket_path)
            except Exception:
                connection.close()
                raise
        else:
            connection = socket.create_connection(self.tcp_address, timeout=self.timeout)
        self._socket = connection
        self._reader = connection.makefile("rb")

    def _disconnect(self) -> None:
        if self._reader is not None:
            self._reader.close()
        if self._socket is not None:
            self._socket.close()
        self._reader = self._socket = None

    def request(self, op: str, **params: Any) -> dict[str, Any]:
        with self._lock:
            self._request_id += 1
            request_id = str(self._request_id)
            payload = {"v": 1, "id": request_id, "op": op, **params}
            try:
                self._connect()
                self._socket.sendall(json.dumps(payload, separators=(",", ":")).encode() + b"\n")
                line = self._reader.readline(1_048_577)
                if not line or len(line) > 1_048_576:
                    raise ConnectionError("memd closed the connection or returned an oversized response")
                response = json.loads(line)
                if not isinstance(response, dict) or response.get("v") != 1:
                    raise ConnectionError("memd returned an invalid protocol response")
                if response.get("id") != request_id:
                    raise ConnectionError("memd response id did not match the request")
                if response.get("ok") is not True:
                    error = response.get("error") or {}
                    raise MemoryServiceError(str(error.get("code", "error")), str(error.get("message", "request failed")))
                return response["result"]
            except Exception:
                self._disconnect()
                raise

    def ping(self) -> dict[str, Any]:
        return self.request("ping")

    def store(self, entry: dict[str, Any]) -> str:
        return self.request("store", entry=entry)["id"]

    def retrieve(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        return self.request("retrieve", query=query, k=k)["hits"]

    def search_sessions(self, query: str) -> list[dict[str, Any]]:
        return self.request("search_sessions", query=query)["hits"]

    def close(self) -> None:
        with self._lock:
            self._disconnect()

    def __enter__(self) -> "MemdClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
