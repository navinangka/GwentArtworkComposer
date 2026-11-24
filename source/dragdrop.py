"""Accept files dropped from Explorer onto a Tk window (Windows WM_DROPFILES, no extra packages)."""

import ctypes
from ctypes import wintypes
from queue import Queue
import sys


WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4


class FileDrop:
    """Collects (files, screen x, screen y) drops in a queue; poll it from the Tk thread."""

    def __init__(self, window):
        self.drops = Queue()
        self.enabled = False
        if sys.platform != "win32":
            return
        try:
            user32, shell32 = ctypes.windll.user32, ctypes.windll.shell32
            result = ctypes.c_ssize_t
            self.procedure_type = ctypes.WINFUNCTYPE(result, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
            user32.SetWindowLongPtrW.restype = ctypes.c_void_p
            user32.SetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_void_p)
            user32.CallWindowProcW.restype = result
            user32.CallWindowProcW.argtypes = (ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
            user32.GetParent.restype = wintypes.HWND
            user32.GetParent.argtypes = (wintypes.HWND,)
            user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
            shell32.DragAcceptFiles.argtypes = (wintypes.HWND, wintypes.BOOL)
            shell32.DragQueryFileW.restype = wintypes.UINT
            shell32.DragQueryFileW.argtypes = (wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT)
            shell32.DragFinish.argtypes = (wintypes.HANDLE,)
            window.update_idletasks()
            hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()

            def procedure(handle, message, wparam, lparam):
                if message != WM_DROPFILES:
                    return user32.CallWindowProcW(self.previous, handle, message, wparam, lparam)
                try:
                    files = []
                    for index in range(shell32.DragQueryFileW(wparam, 0xFFFFFFFF, None, 0)):
                        length = shell32.DragQueryFileW(wparam, index, None, 0)
                        buffer = ctypes.create_unicode_buffer(length + 1)
                        shell32.DragQueryFileW(wparam, index, buffer, length + 1)
                        files.append(buffer.value)
                    point = wintypes.POINT()
                    user32.GetCursorPos(ctypes.byref(point))
                    self.drops.put((files, point.x, point.y))
                finally:
                    shell32.DragFinish(wparam)
                return 0

            # Keep the callback alive for the lifetime of the window.
            self.procedure = self.procedure_type(procedure)
            self.previous = user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC, ctypes.cast(self.procedure, ctypes.c_void_p))
            if self.previous:
                shell32.DragAcceptFiles(hwnd, True)
                self.enabled = True
        except (AttributeError, OSError):
            self.enabled = False
