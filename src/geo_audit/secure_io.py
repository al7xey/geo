"""Confined handle-based file access. No-follow leaves and pinned directory ancestors.

Windows uses documented CreateFile/FILE_FLAG_OPEN_REPARSE_POINT and denies directory
delete sharing while names are used. POSIX uses openat/O_NOFOLLOW directory FDs.
No root/admin privileges or custom filesystem driver required.
"""

import contextlib
import os
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _Info(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("creation", wintypes.FILETIME),
            ("access", wintypes.FILETIME),
            ("write", wintypes.FILETIME),
            ("volume", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("links", wintypes.DWORD),
            ("index_high", wintypes.DWORD),
            ("index_low", wintypes.DWORD),
        ]

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _create = _kernel.CreateFileW
    _create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _create.restype = wintypes.HANDLE
    _get_info = _kernel.GetFileInformationByHandle
    _get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Info)]
    _get_info.restype = wintypes.BOOL
    _close = _kernel.CloseHandle
    _close.argtypes = [wintypes.HANDLE]
    _close.restype = wintypes.BOOL

    def _win_open(
        path: Path, *, directory: bool, writing: bool = False, modify_acl: bool = False
    ) -> int:
        # FILE_READ_ATTRIBUTES alone does not participate in Windows sharing checks.
        # GENERIC_READ includes FILE_LIST_DIRECTORY, so denying DELETE sharing pins names.
        access = 0x80000000 if directory else 0x40000000 if writing else 0x80000000
        if modify_acl:
            access |= 0x40000
        handle = _create(
            str(path),
            access,
            3 if directory else 1,  # Permit child writes, deny directory DELETE sharing.
            None,
            1 if writing else 3,
            0x00200000 | (0x02000000 if directory else 0),
            None,
        )
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        info = _Info()
        if not _get_info(handle, ctypes.byref(info)):
            code = ctypes.get_last_error()
            _close(handle)
            raise ctypes.WinError(code)
        if (
            info.attributes & 0x400
            or bool(info.attributes & 0x10) != directory
            or not directory
            and info.links != 1
        ):
            _close(handle)
            raise OSError("reparse point, hardlink or wrong file kind")
        return int(handle)


def restrict_private_directory(path: Path) -> None:
    """Protect newly created run directories against reads by other local users."""
    with pinned_directory(path.parent):
        if os.name == "nt":
            advapi = ctypes.WinDLL("advapi32", use_last_error=True)
            convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
            convert.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.c_void_p,
            ]
            convert.restype = wintypes.BOOL
            set_security = advapi.SetKernelObjectSecurity
            set_security.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p]
            set_security.restype = wintypes.BOOL
            descriptor = ctypes.c_void_p()
            if not convert("D:P(A;OICI;FA;;;OW)", 1, ctypes.byref(descriptor), None):
                raise ctypes.WinError(ctypes.get_last_error())
            handle = _win_open(path, directory=True, modify_acl=True)
            try:
                if not set_security(handle, 0x80000004, descriptor):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                _close(handle)
                local_free = _kernel.LocalFree
                local_free.argtypes = [ctypes.c_void_p]
                local_free.restype = ctypes.c_void_p
                local_free(descriptor)
        else:
            with pinned_directory(path) as directory_fd:
                if directory_fd is None:
                    raise OSError("directory handle unavailable")
                getattr(os, "fchmod")(directory_fd, 0o700)


@contextlib.contextmanager
def pinned_directory(path: Path, *, create: bool = False) -> Iterator[int | None]:
    path = Path(os.path.abspath(path))
    if os.name == "nt" and (not path.drive or path.drive.startswith("\\")):
        raise OSError("only local drive paths are supported")
    handles: list[int] = []
    current = Path(path.anchor)
    try:
        if os.name == "nt":
            handles.append(_win_open(current, directory=True))
            for part in path.parts[1:]:
                current = current / part
                if create:
                    try:
                        current.mkdir()
                    except FileExistsError:
                        pass
                handles.append(_win_open(current, directory=True))
            yield None
        else:
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY") | getattr(os, "O_NOFOLLOW")
            handles.append(os.open(current, flags))
            for part in path.parts[1:]:
                if create:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=handles[-1])
                    except FileExistsError:
                        pass
                handles.append(os.open(part, flags, dir_fd=handles[-1]))
            yield handles[-1]
    finally:
        for handle in reversed(handles):
            if os.name == "nt":
                _close(handle)
            else:
                os.close(handle)


@contextlib.contextmanager
def open_confined(path: Path, *, writing: bool = False) -> Iterator[BinaryIO]:
    with pinned_directory(path.parent) as directory_fd:
        if os.name == "nt":
            handle = _win_open(path, directory=False, writing=writing)
            try:
                fd = msvcrt.open_osfhandle(
                    handle, (os.O_WRONLY if writing else os.O_RDONLY) | os.O_BINARY
                )
            except BaseException:
                _close(handle)
                raise
        else:
            flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL if writing else os.O_RDONLY) | getattr(
                os, "O_NOFOLLOW"
            )
            if not writing:
                flags |= getattr(os, "O_NONBLOCK")  # A FIFO must not block before fstat rejects it.
            fd = os.open(path.name, flags, 0o600, dir_fd=directory_fd)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError("only ordinary unlinked files are accepted")
            stream = os.fdopen(fd, "wb" if writing else "rb")
        except BaseException:
            os.close(fd)
            raise
        with stream:
            yield stream
