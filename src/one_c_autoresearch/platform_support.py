from __future__ import annotations

import os
import importlib
import subprocess
from pathlib import Path
from typing import BinaryIO, Protocol, runtime_checkable


@runtime_checkable
class _WindowsLocking(Protocol):
    LK_LOCK: int
    LK_NBLCK: int
    LK_UNLCK: int

    def locking(self, descriptor: int, mode: int, length: int) -> None: ...


@runtime_checkable
class _PosixLocking(Protocol):
    LOCK_EX: int
    LOCK_NB: int
    LOCK_UN: int

    def flock(self, descriptor: int, operation: int) -> None: ...
    def ioctl(self, descriptor: int, operation: int, argument: int) -> int: ...


def _is_windows() -> bool:
    namespace: dict[str, object] = os.__dict__
    name = namespace.get("name")
    return isinstance(name, str) and name == "nt"


def _locking_module() -> _WindowsLocking | _PosixLocking:
    module = importlib.import_module("msvcrt" if _is_windows() else "fcntl")
    if isinstance(module, (_WindowsLocking, _PosixLocking)):
        return module
    raise RuntimeError("platform file locking is unavailable")


def lock_fd(descriptor: int, *, nonblocking: bool = False) -> None:
    module = _locking_module()
    if isinstance(module, _WindowsLocking):
        _ = os.lseek(descriptor, 0, os.SEEK_SET)
        module.locking(descriptor, module.LK_NBLCK if nonblocking else module.LK_LOCK, 1)
    else:
        module.flock(descriptor, module.LOCK_EX | (module.LOCK_NB if nonblocking else 0))


def unlock_fd(descriptor: int) -> None:
    module = _locking_module()
    if isinstance(module, _WindowsLocking):
        _ = os.lseek(descriptor, 0, os.SEEK_SET)
        module.locking(descriptor, module.LK_UNLCK, 1)
    else:
        module.flock(descriptor, module.LOCK_UN)


def lock_file(stream: BinaryIO, *, nonblocking: bool = False) -> None:
    lock_fd(stream.fileno(), nonblocking=nonblocking)


def unlock_file(stream: BinaryIO) -> None:
    unlock_fd(stream.fileno())


def sync_directory(path: Path) -> None:
    if _is_windows():
        return
    namespace: dict[str, object] = os.__dict__
    directory_flag = namespace.get("O_DIRECTORY", 0)
    descriptor = os.open(path, os.O_RDONLY | (directory_flag if isinstance(directory_flag, int) else 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def clone_file(source: BinaryIO, target: BinaryIO) -> bool:
    module = None if _is_windows() else importlib.import_module("fcntl")
    if not isinstance(module, _PosixLocking):
        return False
    try:
        _ = module.ioctl(target.fileno(), 0x40049409, source.fileno())
        return True
    except OSError:
        return False


def current_uid() -> int | None:
    value = os.__dict__.get("getuid")
    result = value() if callable(value) else None
    return result if isinstance(result, int) else None


def process_group(pid: int) -> int:
    value = os.__dict__.get("getpgid")
    result = value(pid) if callable(value) else pid
    return result if isinstance(result, int) else pid


def terminate_process(process: subprocess.Popen[bytes] | subprocess.Popen[str], *, force: bool = False) -> None:
    if not _is_windows():
        value = os.__dict__.get("killpg")
        if callable(value):
            _ = value(process.pid, 9 if force else 15)
            return
    if force:
        process.kill()
    else:
        process.terminate()


def terminate_process_identity(pid: int, process_group_id: int, *, force: bool = False) -> None:
    if _is_windows():
        command = ["taskkill", "/PID", str(pid), "/T", *( ["/F"] if force else [])]
        _ = subprocess.run(command, capture_output=True, check=False)
        return
    value = os.__dict__.get("killpg")
    if not callable(value):
        raise RuntimeError("process-group termination is unavailable")
    _ = value(process_group_id, 9 if force else 15)
