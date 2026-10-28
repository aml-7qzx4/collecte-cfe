import re
from zipfile import ZipFile

import pytest

from collecte_cfe.diagnostics import DiagnosticLog


def test_diagnostic_log_timestamps_structured_events_without_secrets(tmp_path):
    log = DiagnosticLog(tmp_path / 'journal.txt')
    log.append('session_connected', session=2)
    log.append('captcha_refreshed', session=1, refresh=3)
    report = log.recent()
    assert 'Session connectée | Session 3' in report
    assert 'CAPTCHA régénéré | Session 2 | Essai 3' in report
    assert re.search(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}', report)
    with pytest.raises(ValueError):
        log.append('password=ne-pas-conserver')
    assert 'ne-pas-conserver' not in log.recent()


def test_support_bundle_contains_journal_and_worker_stack_only(tmp_path):
    log = DiagnosticLog(tmp_path / 'journal.txt')
    log.append('application_started')
    stack = tmp_path / 'moteur-erreurs.txt'
    stack.write_text('2026-09-23T10:00:00 | RuntimeError | sessions.py:20 operate\n', encoding='utf-8')
    destination = tmp_path / 'diagnostic.zip'
    log.export(destination, stack)
    with ZipFile(destination) as bundle:
        assert set(bundle.namelist()) == {'journal.txt', 'moteur-erreurs.txt'}
        assert b'Application ouverte' in bundle.read('journal.txt')
        assert b'sessions.py:20' in bundle.read('moteur-erreurs.txt')
