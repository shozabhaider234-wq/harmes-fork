"""Windows byte-stream pipe I/O with cancellable per-operation deadlines."""
import ctypes
from ctypes import wintypes
import io
import os
import time


class NamedPipe(io.RawIOBase):
    def __init__(self, name: str, timeout: float):
        super().__init__()
        if os.name != "nt":
            raise OSError("named pipes require Windows")
        if not name or any(not (c.isascii() and (c.isalnum() or c in "._-")) for c in name):
            raise ValueError("invalid local pipe name")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.timeout = timeout
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        definitions = {
            "CreateFileW": ([wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE],wintypes.HANDLE),
            "WaitNamedPipeW": ([wintypes.LPCWSTR,wintypes.DWORD],wintypes.BOOL),
            "CreateEventW": ([ctypes.c_void_p,wintypes.BOOL,wintypes.BOOL,wintypes.LPCWSTR],wintypes.HANDLE),
            "CloseHandle": ([wintypes.HANDLE],wintypes.BOOL),
            "ReadFile": ([wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD,ctypes.c_void_p,ctypes.c_void_p],wintypes.BOOL),
            "WriteFile": ([wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD,ctypes.c_void_p,ctypes.c_void_p],wintypes.BOOL),
            "WaitForSingleObject": ([wintypes.HANDLE,wintypes.DWORD],wintypes.DWORD),
            "GetOverlappedResult": ([wintypes.HANDLE,ctypes.c_void_p,ctypes.POINTER(wintypes.DWORD),wintypes.BOOL],wintypes.BOOL),
            "CancelIoEx": ([wintypes.HANDLE,ctypes.c_void_p],wintypes.BOOL),
        }
        for method, (arguments,result) in definitions.items():
            function = getattr(self.api, method)
            function.argtypes, function.restype = arguments,result
        self.handle = None
        deadline = time.monotonic() + timeout
        path = "\\\\.\\pipe\\" + name
        while True:
            handle = self.api.CreateFileW(path, 0xC0000000, 0, None, 3, 0x40000000, None)
            if handle != ctypes.c_void_p(-1).value:
                self.handle = handle
                break
            error = ctypes.get_last_error()
            if error != 231:  # ERROR_PIPE_BUSY
                raise ctypes.WinError(error)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("memd pipe connect timed out")
            if not self.api.WaitNamedPipeW(path, max(1,int(remaining * 1000))):
                raise ctypes.WinError(ctypes.get_last_error())

    def readable(self):
        return True

    def writable(self):
        return True

    def _transfer(self, buffer, size, writing):
        if self.closed or self.handle is None:
            raise ValueError("pipe is closed")
        class Overlapped(ctypes.Structure):
            _fields_ = [("Internal",ctypes.c_size_t),("InternalHigh",ctypes.c_size_t),
                        ("Offset",wintypes.DWORD),("OffsetHigh",wintypes.DWORD),("hEvent",wintypes.HANDLE)]
        event = self.api.CreateEventW(None, True, False, None)
        if not event:
            raise ctypes.WinError(ctypes.get_last_error())
        operation = Overlapped(hEvent=event)
        transferred = wintypes.DWORD()
        try:
            function = self.api.WriteFile if writing else self.api.ReadFile
            result = function(self.handle, buffer, size, None, ctypes.byref(operation))
            if not result:
                error = ctypes.get_last_error()
                if error == 109 and not writing:  # ERROR_BROKEN_PIPE
                    return 0
                if error != 997:  # ERROR_IO_PENDING
                    raise ctypes.WinError(error)
            if self.api.WaitForSingleObject(event, max(1,int(self.timeout * 1000))) != 0:
                # Keep OVERLAPPED and buffer alive until kernel cancellation completes.
                self.api.CancelIoEx(self.handle, ctypes.byref(operation))
                self.api.GetOverlappedResult(self.handle, ctypes.byref(operation), ctypes.byref(transferred), True)
                raise TimeoutError("memd pipe I/O timed out")
            if not self.api.GetOverlappedResult(self.handle, ctypes.byref(operation), ctypes.byref(transferred), False):
                error = ctypes.get_last_error()
                if error == 109 and not writing:
                    return 0
                raise ctypes.WinError(error)
            return transferred.value
        finally:
            self.api.CloseHandle(event)

    def readinto(self, target):
        if not target:
            return 0
        buffer = ctypes.create_string_buffer(len(target))
        count = self._transfer(buffer, len(target), False)
        target[:count] = buffer.raw[:count]
        return count

    def write(self, data):
        if not data:
            return 0
        buffer = ctypes.create_string_buffer(bytes(data))
        return self._transfer(buffer, len(data), True)

    def close(self):
        if getattr(self,"handle",None) is not None:
            self.api.CloseHandle(self.handle)
            self.handle = None
        super().close()
