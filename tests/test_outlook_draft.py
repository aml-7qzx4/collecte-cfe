import base64
import json
import sys
import time
from pathlib import Path

import pytest

from collecte_cfe import outlook_draft
from collecte_cfe.outlook_draft import BRIDGE_SCRIPT, DraftUnavailable, OutlookBridge, mail_payload

# A stand-in for the PowerShell bridge: same line protocol, no Outlook. It writes each mail it receives to a log.
FAKE_BRIDGE = r"""
import base64, json, sys, time
log, mode = sys.argv[1], sys.argv[2]
time.sleep(float(sys.argv[3]))
print('NO_OUTLOOK' if mode == 'absent' else 'READY', flush=True)
for line in sys.stdin:
    data = json.loads(base64.b64decode(line.strip()).decode('utf-8'))
    with open(log, 'a', encoding='utf-8') as out: out.write(data['to'] + '\n')
    print('ERROR' if data['to'].startswith('refus') else 'OK', flush=True)
"""


def fake_bridge(tmp_path, mode='ready', startup=0.0):
    script = tmp_path / 'bridge.py'
    script.write_text(FAKE_BRIDGE, encoding='utf-8')
    log = tmp_path / 'mails.log'
    starts = []

    def command():
        starts.append(time.perf_counter())
        return [sys.executable, str(script), str(log), mode, str(startup)]

    return OutlookBridge(command), log, starts


def test_the_mail_reaches_outlook_as_data_and_is_only_shown(tmp_path):
    pdf = tmp_path / 'Alpha_CFE_2025_SOLDE.pdf'
    data = json.loads(
        base64.b64decode(
            mail_payload("o'brien@example.fr", 'Avis CFE 2025 — Alpha', 'Bonjour,\n\n<b>1 284,00 €</b> ; $(rm)', [pdf])
        )
    )
    assert data['to'] == "o'brien@example.fr" and data['subject'] == 'Avis CFE 2025 — Alpha'
    assert data['attachments'] == [str(pdf.resolve())]
    assert data['html'].count('<br>') >= 2 and '&lt;b&gt;' in data['html'] and '<b>' not in data['html']
    assert (
        "o'brien" not in BRIDGE_SCRIPT and '$mail.Display($false)' in BRIDGE_SCRIPT and '.Send' not in BRIDGE_SCRIPT
    ), 'the mail is data read by a fixed script, shown and never sent'


def test_the_bridge_starts_once_and_each_next_mail_is_immediate(tmp_path):
    bridge, log, starts = fake_bridge(tmp_path, startup=0.6)
    try:
        assert bridge.start(), 'started ahead of the first mail'
        began = time.perf_counter()
        for address in ('a@example.fr', 'b@example.fr', 'c@example.fr'):
            bridge.open(address, 'Objet', 'Corps', [])
        assert time.perf_counter() - began < 0.5, 'no new start for each mail'
        assert len(starts) == 1 and log.read_text(encoding='utf-8').split() == [
            'a@example.fr',
            'b@example.fr',
            'c@example.fr',
        ]
        with pytest.raises(DraftUnavailable):
            bridge.open('refus@example.fr', 'Objet', 'Corps', [])
        bridge.open('d@example.fr', 'Objet', 'Corps', [])
        assert len(starts) == 1, 'a refused mail does not restart the bridge'
        bridge.process.kill()
        bridge.process.wait()
        bridge.open('e@example.fr', 'Objet', 'Corps', [])
        assert len(starts) == 2, 'a bridge that stopped is started again'
    finally:
        bridge.close()


def test_without_classic_outlook_the_default_mail_application_takes_over(tmp_path, monkeypatch):
    bridge, _log, _starts = fake_bridge(tmp_path, mode='absent')
    calls = []
    monkeypatch.setattr(outlook_draft.sys, 'platform', 'win32')
    monkeypatch.setattr(outlook_draft, 'BRIDGE', bridge)
    monkeypatch.setattr(outlook_draft, 'open_with_default_mail', lambda *args: calls.append('mapi'))
    outlook_draft.open_draft('a@example.fr', 'Objet', 'Corps', [])
    assert calls == ['mapi']

    def no_mapi(*_args):
        raise DraftUnavailable('Aucune messagerie compatible n’est installée.')

    monkeypatch.setattr(outlook_draft, 'open_with_default_mail', no_mapi)
    with pytest.raises(DraftUnavailable, match='Aucune messagerie'):
        outlook_draft.open_draft('a@example.fr', 'Objet', 'Corps', [Path('a.pdf')])
    bridge.close()
