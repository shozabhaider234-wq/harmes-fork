"""Small synchronous client for the memd JSONL protocol."""

from __future__ import annotations

import json
import io
import socket
import threading
from typing import Any


class MemoryServiceError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


class MemdClient:
    def __init__(self, *, socket_path: str | None = None, tcp_address: tuple[str, int] | None = None,
                 timeout: float = 5.0, pipe_name: str | None = None):
        if sum(endpoint is not None for endpoint in (socket_path,tcp_address,pipe_name)) != 1:
            raise ValueError("provide exactly one of socket_path, tcp_address or pipe_name")
        self.socket_path = socket_path
        self.tcp_address = tcp_address
        self.timeout = timeout
        self.pipe_name = pipe_name
        self._pipe: Any = None
        self._socket: socket.socket | None = None
        self._reader: Any = None
        self._lock = threading.Lock()
        self._request_id = 0

    def _connect(self) -> None:
        if self._socket is not None or self._pipe is not None:
            return
        if self.pipe_name is not None:
            if __package__:
                from .named_pipe import NamedPipe
            else:
                from named_pipe import NamedPipe
            self._pipe = NamedPipe(self.pipe_name, self.timeout)
            self._reader = io.BufferedReader(self._pipe)
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
        self._pipe = None

    def request(self, op: str, **params: Any) -> dict[str, Any]:
        with self._lock:
            self._request_id += 1
            request_id = str(self._request_id)
            payload = {"v": 1, "id": request_id, "op": op, **params}
            try:
                self._connect()
                encoded = json.dumps(payload, separators=(",", ":")).encode() + b"\n"
                if self._socket is not None:
                    self._socket.sendall(encoded)
                else:
                    remaining = memoryview(encoded)
                    while remaining:
                        written = self._pipe.write(remaining)
                        if not written:
                            raise ConnectionError("memd pipe closed during write")
                        remaining = remaining[written:]
                line = self._reader.readline(1_048_577)
                if not line or len(line) > 1_048_576:
                    raise ConnectionError("memd closed the connection or returned an oversized response")
                response = json.loads(line)
                if not isinstance(response, dict) or response.get("v") != 1:
                    raise ConnectionError("memd returned an invalid protocol response")
                if response.get("id") != request_id:
                    if response.get("id") is None and response.get("error", {}).get("code") == "request_too_large":
                        raise MemoryServiceError("request_too_large",response["error"]["message"])
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

    def inventory(self, scope: dict[str, Any]) -> list[dict[str, Any]]:
        return self.request("inventory", scope=scope)["entries"]

    def compare_exchange(self, scope, expected, desired) -> bool:
        return self.request("compare_exchange", scope=scope, expected=expected, desired=desired)["committed"]

    def store(self, entry: dict[str, Any]) -> str:
        return self.request("store", entry=entry)["id"]

    def retrieve(self, query: str, k: int = 5, *, scope: dict[str, Any] | None = None,
                 mode: str | None = None, include_superseded: bool = False) -> list[dict[str, Any]]:
        options: dict[str, Any] = {}
        if scope is not None:
            options["scope"] = scope
        if mode is not None:
            options["mode"] = mode
        if include_superseded:
            options["include_superseded"] = True
        return self.request("retrieve", query=query, k=k, **options)["hits"]

    def get_memory(self, memory_id: str) -> dict[str, Any] | None:
        return self.request("get_memory", memory_id=memory_id)["entry"]

    def update_memory(self, entry: dict[str, Any]) -> str:
        """Replace an existing complete record (including text); embedding is regenerated."""
        return self.request("update_memory", entry=entry)["id"]

    def delete_memory(self, memory_id: str) -> bool:
        return self.request("delete_memory", memory_id=memory_id)["deleted"]

    def source_lookup(self, memory_id: str) -> dict[str, Any]:
        return self.request("source_lookup", memory_id=memory_id)

    def search_sessions(self, query: str) -> list[dict[str, Any]]:
        return self.request("search_sessions", query=query)["hits"]

    def close(self) -> None:
        with self._lock:
            self._disconnect()

    def __enter__(self) -> "MemdClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
