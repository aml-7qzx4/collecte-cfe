import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication

from collecte_cfe.window_embed import (
    EmbeddedWindowHost,
    child_style,
    choose_browser_focus_handle,
    native_child_geometry,
)


class Backend:
    def __init__(self):
        self.calls = []
        self.style = 0x10CF0000

    def get_parent(self, h):
        return 44

    def get_style(self, h):
        return self.style

    def set_parent(self, h, p):
        self.calls.append(('parent', h, p))

    def set_style(self, h, s):
        self.calls.append(('style', h, s))

    def frame_changed(self, h):
        self.calls.append(('frame_changed', h))

    def move(self, h, x, y, w, ht):
        self.calls.append(('move', h, x, y, w, ht))

    def show(self, h, value):
        self.calls.append(('show', h, value))

    def focus(self, h):
        self.calls.append(('focus', h))

    def is_foreground_tree(self, h):
        self.calls.append(('foreground', h))
        return True


class StatefulBackend(Backend):
    def __init__(self):
        super().__init__()
        self.geometry = (0, 0, 1, 1)
        self.visible = False

    def get_geometry(self, _h):
        return self.geometry

    def is_visible(self, _h):
        return self.visible

    def move(self, h, x, y, w, ht):
        super().move(h, x, y, w, ht)
        self.geometry = (x, y, w, ht)

    def show(self, h, value):
        super().show(h, value)
        self.visible = bool(value)


def test_embed_resize_hide_and_restore():
    app = QApplication.instance() or QApplication([])
    backend = Backend()
    host = EmbeddedWindowHost(backend)
    host.resize(640, 420)
    host.attach(123)
    app.processEvents()
    assert ('parent', 123, int(host.winId())) in backend.calls
    assert ('style', 123, child_style(backend.style)) in backend.calls
    assert ('frame_changed', 123) in backend.calls
    assert ('move', 123, -9, -88, 658, 508) in backend.calls
    host.hide()
    app.processEvents()
    assert ('show', 123, False) in backend.calls
    host.release()
    assert ('parent', 123, 44) in backend.calls and ('style', 123, backend.style) in backend.calls


def test_explicit_geometry_sync_uses_the_current_container_size():
    app = QApplication.instance() or QApplication([])
    backend = Backend()
    host = EmbeddedWindowHost(backend)
    host.resize(1180, 650)
    host.attach(123)
    backend.calls.clear()
    host.resize(1320, 720)
    app.processEvents()
    backend.calls.clear()
    host.sync_geometry()
    assert backend.calls == [('move', 123, -9, -88, 1338, 808), ('show', 123, host.isVisible())]


def test_native_geometry_accounts_for_windows_display_scaling():
    geometry = native_child_geometry(1180, 747, 88, 1.25)
    assert geometry == (-11, -110, 1497, 1044)
    assert geometry[1] + geometry[3] == round(747 * 1.25)


def test_repeated_geometry_sync_does_not_disturb_an_unchanged_browser_window():
    app = QApplication.instance() or QApplication([])
    backend = StatefulBackend()
    host = EmbeddedWindowHost(backend)
    host.resize(640, 420)
    host.show()
    host.attach(123)
    app.processEvents()
    backend.calls.clear()
    host.sync_geometry()
    host.sync_geometry()
    assert backend.calls == []


def test_browser_host_can_explicitly_transfer_keyboard_focus_to_chromium(qapp):
    backend = Backend()
    host = EmbeddedWindowHost(backend)
    host.attach(123)
    backend.calls.clear()
    host.focus_browser()
    assert backend.calls == [('focus', 123)]


def test_keyboard_focus_targets_chromiums_render_widget_instead_of_its_frame():
    descendants = [
        (124, 'Intermediate D3D Window', True, False),
        (125, 'Chrome_RenderWidgetHostHWND', True, True),
    ]
    assert choose_browser_focus_handle(123, descendants) == 125


def test_browser_host_can_check_if_its_application_owns_the_foreground(qapp):
    backend = Backend()
    host = EmbeddedWindowHost(backend)
    assert host.application_is_foreground(456) is True
    assert backend.calls == [('foreground', 456)]
