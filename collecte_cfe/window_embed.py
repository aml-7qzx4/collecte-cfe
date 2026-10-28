"""Rattachement d'une fenêtre Chromium externe à un hôte Qt sous Windows."""

import ctypes
from ctypes import wintypes

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

LONG_PTR = ctypes.c_ssize_t
ULONG_PTR = ctypes.c_size_t

WS_CHILD = 0x40000000
WS_POPUP = 0x80000000
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_SYSMENU = 0x00080000
WS_CLIPCHILDREN = 0x02000000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020


def child_style(style):
    return (
        (style & ~(WS_POPUP | WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU))
        | WS_CHILD
        | WS_CLIPCHILDREN
    )


def native_child_geometry(width, height, top_crop, scale, frame_crop=9):
    """Convertit la géométrie logique Qt en pixels natifs Win32."""
    scale = max(float(scale), 0.01)
    crop = round(top_crop * scale)
    edge = round(frame_crop * scale)
    return -edge, -crop, max(1, round(width * scale) + 2 * edge), max(1, round((height + top_crop) * scale))


def choose_browser_focus_handle(root, descendants):
    for handle, class_name, visible, enabled in descendants:
        if class_name == 'Chrome_RenderWidgetHostHWND' and visible and enabled:
            return handle
    return root


def hide_chromium_window(handle):
    user32 = ctypes.windll.user32
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.ShowWindow(handle, 0)


class WindowsBackend:
    def __init__(self):
        self.user32 = ctypes.windll.user32
        self.user32.GetParent.argtypes = [wintypes.HWND]
        self.user32.GetParent.restype = wintypes.HWND
        self.user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.GetWindowLongPtrW.restype = LONG_PTR
        self.user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, LONG_PTR]
        self.user32.SetWindowLongPtrW.restype = LONG_PTR
        self.user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
        self.user32.SetParent.restype = wintypes.HWND
        self.user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        self.user32.SetWindowPos.restype = wintypes.BOOL
        self.user32.MoveWindow.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.BOOL,
        ]
        self.user32.MoveWindow.restype = wintypes.BOOL
        self.user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.ShowWindow.restype = wintypes.BOOL
        self.user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user32.GetWindowRect.restype = wintypes.BOOL
        self.user32.MapWindowPoints.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_void_p, wintypes.UINT]
        self.user32.MapWindowPoints.restype = ctypes.c_int
        self.user32.IsWindowVisible.argtypes = [wintypes.HWND]
        self.user32.IsWindowVisible.restype = wintypes.BOOL
        self.user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        self.user32.AttachThreadInput.restype = wintypes.BOOL
        self.user32.SetFocus.argtypes = [wintypes.HWND]
        self.user32.SetFocus.restype = wintypes.HWND
        self.user32.GetForegroundWindow.argtypes = []
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.IsChild.argtypes = [wintypes.HWND, wintypes.HWND]
        self.user32.IsChild.restype = wintypes.BOOL
        self.kernel32 = ctypes.windll.kernel32
        self.kernel32.GetCurrentThreadId.argtypes = []
        self.kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    def get_parent(self, h):
        return int(self.user32.GetParent(h) or 0)

    def get_style(self, h):
        return int(self.user32.GetWindowLongPtrW(h, -16))

    def set_parent(self, h, p):
        self.user32.SetParent(h, p)

    def set_style(self, h, s):
        self.user32.SetWindowLongPtrW(h, -16, s)

    def frame_changed(self, h):
        self.user32.SetWindowPos(
            h, 0, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED
        )

    def move(self, h, x, y, w, ht):
        self.user32.MoveWindow(h, x, y, w, ht, True)

    def show(self, h, value):
        self.user32.ShowWindow(h, 5 if value else 0)

    def get_geometry(self, h):
        rect = wintypes.RECT()
        if not self.user32.GetWindowRect(h, ctypes.byref(rect)):
            return None
        parent = self.user32.GetParent(h)
        if parent:
            self.user32.MapWindowPoints(0, parent, ctypes.byref(rect), 2)
        return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top

    def is_visible(self, h):
        return bool(self.user32.IsWindowVisible(h))

    def is_foreground_tree(self, h):
        foreground = self.user32.GetForegroundWindow()
        return bool(foreground and (foreground == h or self.user32.IsChild(h, foreground)))

    def focus(self, h):
        descendants = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        self.user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user32.GetClassNameW.restype = ctypes.c_int
        self.user32.IsWindowEnabled.argtypes = [wintypes.HWND]
        self.user32.IsWindowEnabled.restype = wintypes.BOOL
        self.user32.EnumChildWindows.argtypes = [wintypes.HWND, callback_type, wintypes.LPARAM]
        self.user32.EnumChildWindows.restype = wintypes.BOOL

        @callback_type
        def collect(child, _):
            name = ctypes.create_unicode_buffer(128)
            self.user32.GetClassNameW(child, name, 128)
            descendants.append(
                (
                    int(child),
                    name.value,
                    bool(self.user32.IsWindowVisible(child)),
                    bool(self.user32.IsWindowEnabled(child)),
                )
            )
            return True

        self.user32.EnumChildWindows(h, collect, 0)
        target = choose_browser_focus_handle(h, descendants)
        current_thread = self.kernel32.GetCurrentThreadId()
        browser_thread = self.user32.GetWindowThreadProcessId(target, None)
        attached = bool(
            browser_thread
            and browser_thread != current_thread
            and self.user32.AttachThreadInput(current_thread, browser_thread, True)
        )
        try:
            return bool(self.user32.SetFocus(target))
        finally:
            if attached:
                self.user32.AttachThreadInput(current_thread, browser_thread, False)


class WindowEmbedder:
    def __init__(self, backend=None):
        self.backend = backend or WindowsBackend()
        self.records = {}

    def attach(self, handle, parent):
        if handle in self.records:
            return
        self.records[handle] = (self.backend.get_parent(handle), self.backend.get_style(handle))
        self.backend.set_style(handle, child_style(self.records[handle][1]))
        self.backend.set_parent(handle, parent)
        frame_changed = getattr(self.backend, 'frame_changed', None)
        if frame_changed:
            frame_changed(handle)

    def position(self, handle, x, y, width, height):
        target = (int(x), int(y), max(1, int(width)), max(1, int(height)))
        getter = getattr(self.backend, 'get_geometry', None)
        current = getter(handle) if getter else None
        if current is None or any(abs(int(actual) - expected) > 1 for actual, expected in zip(current, target)):
            self.backend.move(handle, *target)

    def resize(self, handle, width, height):
        self.position(handle, 0, 0, width, height)

    def show(self, handle, value):
        checker = getattr(self.backend, 'is_visible', None)
        if checker is None or bool(checker(handle)) != bool(value):
            self.backend.show(handle, value)

    def focus(self, handle):
        focus = getattr(self.backend, 'focus', None)
        if focus:
            return focus(handle)

    def release(self, handle):
        record = self.records.pop(handle, None)
        if record:
            self.backend.show(handle, False)
            self.backend.set_parent(handle, record[0])
            self.backend.set_style(handle, record[1])


class EmbeddedWindowHost(QWidget):
    def __init__(self, backend=None, parent=None, top_crop=88, frame_crop=9):
        super().__init__(parent)
        self.top_crop = top_crop
        self.frame_crop = frame_crop
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.embedder = WindowEmbedder(backend)
        self.handle = None

    def attach(self, handle):
        if self.handle == handle:
            return
        self.release()
        self.handle = handle
        self.embedder.attach(handle, int(self.winId()))
        self._sync()

    def _sync(self):
        if self.handle is not None:
            geometry = native_child_geometry(
                self.width(), self.height(), self.top_crop, self.devicePixelRatioF(), self.frame_crop
            )
            self.embedder.position(self.handle, *geometry)
            self.embedder.show(self.handle, self.isVisible())

    def sync_geometry(self):
        self._sync()

    def focus_browser(self):
        if self.handle is not None:
            self.embedder.focus(self.handle)

    def application_is_foreground(self, root_handle):
        check = getattr(self.embedder.backend, 'is_foreground_tree', None)
        return bool(check(root_handle)) if check else False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync()

    def hideEvent(self, event):
        if self.handle is not None:
            self.embedder.show(self.handle, False)
        super().hideEvent(event)

    def release(self):
        if self.handle is not None:
            self.embedder.release(self.handle)
            self.handle = None

    def closeEvent(self, event):
        self.release()
        super().closeEvent(event)


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ('dwSize', wintypes.DWORD),
        ('cntUsage', wintypes.DWORD),
        ('th32ProcessID', wintypes.DWORD),
        ('th32DefaultHeapID', ULONG_PTR),
        ('th32ModuleID', wintypes.DWORD),
        ('cntThreads', wintypes.DWORD),
        ('th32ParentProcessID', wintypes.DWORD),
        ('pcPriClassBase', wintypes.LONG),
        ('dwFlags', wintypes.DWORD),
        ('szExeFile', wintypes.WCHAR * 260),
    ]


def process_tree(root_pid):
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    parents = {}
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    descendants = {int(root_pid)}
    changed = True
    while changed:
        changed = False
        for pid, parent in parents.items():
            if parent in descendants and pid not in descendants:
                descendants.add(pid)
                changed = True
    return descendants


def chromium_windows(allowed_pids=None):
    user32 = ctypes.windll.user32
    result = set()
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL

    @callback_type
    def collect(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if user32.IsWindowVisible(hwnd) and (allowed_pids is None or int(pid.value) in allowed_pids):
            name = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(hwnd, name, 128)
            if name.value == 'Chrome_WidgetWin_1':
                result.add(int(hwnd))
        return True

    user32.EnumWindows(collect, 0)
    return result
