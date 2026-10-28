import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_real_outlook(monkeypatch):
    """A test must never open a real Outlook window on the machine running it."""

    def refuse(*_args, **_kwargs):
        raise RuntimeError('Outlook would open during a test: give the window a draft_opener stub.')

    monkeypatch.setattr('collecte_cfe.ui_mail.open_draft', refuse)
    # Mails open on the test's own thread, so that its checks see the result.
    monkeypatch.setattr('collecte_cfe.ui_mail.DRAFTS_IN_BACKGROUND', False)
    monkeypatch.setattr('collecte_cfe.ui_mail.prepare_outlook', lambda: None)
