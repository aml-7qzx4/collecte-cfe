import threading
from pathlib import Path

from collecte_cfe import integrity
from collecte_cfe.collector import Collector
from collecte_cfe.documents import classify_pdf
from collecte_cfe.domain import Dossier, Notice
from collecte_cfe.integrity import file_sha256, matches_sha256
from collecte_cfe.store import Store
from tests.test_collector import FakePortal


def test_file_sha256_reads_content_and_verification_handles_missing_or_changed_file(tmp_path):
    target = tmp_path / 'notice.pdf'
    target.write_bytes(b'original content')
    expected = 'bf573149b23303cac63c2a359b53760d919770c5d070047e76de42e2184f1046'
    digest = file_sha256(target)
    assert digest == expected
    assert matches_sha256(target, digest)
    target.write_bytes(b'modified content')
    assert not matches_sha256(target, digest)
    target.unlink()
    assert not matches_sha256(target, digest)
    assert not matches_sha256(target, None)


def test_file_hash_verification_handles_read_errors(tmp_path, monkeypatch):
    target = tmp_path / 'notice.pdf'
    target.write_bytes(b'original content')

    def fail_to_read(path):
        raise PermissionError('access denied')

    monkeypatch.setattr(integrity, 'file_sha256', fail_to_read)
    assert not matches_sha256(target, 'bf573149b23303cac63c2a359b53760d919770c5d070047e76de42e2184f1046')


def test_completed_file_recovers_after_crash_before_db_commit(tmp_path):
    d = Dossier('Exemple', '000000001', 'Léa')
    s = Store(tmp_path / 'db.sqlite')
    campaign = s.create_campaign(2025, tmp_path / 'sortie', [d])
    p = FakePortal()
    n = p.open_dossier(d.siren, 2025)[0]
    row = s.register_notice(campaign, d.siren, n)
    source = tmp_path / 'temp.pdf'
    p.download(n, source)
    classify_pdf(source, Path(row['path']), d, n)
    p = FakePortal()
    Collector(s, p).run(campaign, threading.Event(), lambda x: None)
    assert p.downloads == ['2']
    assert s.notices(campaign)[0]['status'] == 'TERMINE'


def test_missing_completed_file_is_downloaded_again(tmp_path):
    d = Dossier('Exemple', '000000001', 'Léa')
    s = Store(tmp_path / 'db.sqlite')
    campaign = s.create_campaign(2025, tmp_path / 'sortie', [d])
    Collector(s, FakePortal()).run(campaign, threading.Event(), lambda x: None)
    Path(s.notices(campaign)[0]['path']).unlink()
    p = FakePortal()
    Collector(s, p).run(campaign, threading.Event(), lambda x: None)
    assert p.downloads == ['1']


def test_pause_finishes_current_notice_and_leaves_next_pending(tmp_path):
    d = Dossier('Exemple', '000000001', 'Léa')
    s = Store(tmp_path / 'db.sqlite')
    campaign = s.create_campaign(2025, tmp_path / 'sortie', [d])
    stop = threading.Event()

    class PausingPortal(FakePortal):
        def download(self, n, path):
            super().download(n, path)
            stop.set()

    Collector(s, PausingPortal()).run(campaign, stop, lambda x: None)
    assert [n['status'] for n in s.notices(campaign)] == ['TERMINE', 'EN_ATTENTE']
    assert s.campaign(campaign)['status'] == 'PAUSE'


def test_confirmed_empty_list_creates_proof(tmp_path):
    class EmptyPortal(FakePortal):
        def open_dossier(self, siren, year):
            return []

    d = Dossier('Exemple', '000000001', 'Léa')
    s = Store(tmp_path / 'db.sqlite')
    campaign = s.create_campaign(2025, tmp_path / 'sortie', [d])
    Collector(s, EmptyPortal()).run(campaign, threading.Event(), lambda x: None)
    assert s.dossiers(campaign)[0]['status'] == 'ABSENT'
    assert len(list((tmp_path / 'sortie').rglob('*ABSENCE.pdf'))) == 1


def test_modified_pdf_is_preserved_and_downloaded_again(tmp_path):
    d = Dossier('Exemple', '000000001', 'Léa')
    s = Store(tmp_path / 'db.sqlite')
    campaign = s.create_campaign(2025, tmp_path / 'sortie', [d])
    Collector(s, FakePortal()).run(campaign, threading.Event(), lambda x: None)
    target = Path(s.notices(campaign)[0]['path'])
    original = target.read_bytes()
    target.write_bytes(original + b'\nmodified')
    p = FakePortal()
    Collector(s, p).run(campaign, threading.Event(), lambda x: None)
    assert p.downloads == ['1']
    quarantined = list(target.parent.glob('_A_verifier/*.pdf'))
    assert len(quarantined) == 1 and quarantined[0].read_bytes() == original + b'\nmodified'


def test_corrupt_classified_pdf_can_be_retried(tmp_path):
    d = Dossier('Exemple', '000000001', 'Léa')
    s = Store(tmp_path / 'db.sqlite')
    campaign = s.create_campaign(2025, tmp_path / 'sortie', [d])
    Collector(s, FakePortal()).run(campaign, threading.Event(), lambda x: None)
    target = Path(s.notices(campaign)[0]['path'])
    target.write_bytes(b'broken')
    p = FakePortal()
    Collector(s, p).run(campaign, threading.Event(), lambda x: None)
    assert p.downloads == ['1']
    assert s.dossiers(campaign)[0]['status'] == 'TERMINE'


def test_new_campaign_cannot_collide_with_another_notice(tmp_path):
    d = Dossier('Exemple', '000000001', 'Léa')
    s = Store(tmp_path / 'db.sqlite')
    first = s.create_campaign(2025, tmp_path / 'sortie', [d])
    Collector(s, FakePortal()).run(first, threading.Event(), lambda x: None)
    second = s.create_campaign(2025, tmp_path / 'sortie', [d])
    n = Notice(2025, 'other', '00022', '33', 'BORDEAUX', 'Rôle général', '')
    row = s.register_notice(second, d.siren, n)
    assert Path(row['path']).name == 'Exemple_CFE_2025_SOLDE_02.pdf'


def test_absence_proof_is_recreated_after_removal(tmp_path):
    class EmptyPortal(FakePortal):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def open_dossier(self, siren, year):
            self.calls += 1
            return []

    d = Dossier('Exemple', '000000001', 'Léa')
    s = Store(tmp_path / 'db.sqlite')
    campaign = s.create_campaign(2025, tmp_path / 'sortie', [d])
    p = EmptyPortal()
    Collector(s, p).run(campaign, threading.Event(), lambda x: None)
    proof = next((tmp_path / 'sortie').rglob('*ABSENCE.pdf'))
    proof.unlink()
    Collector(s, p).run(campaign, threading.Event(), lambda x: None)
    assert p.calls == 2 and proof.exists()


def test_two_sirens_cannot_share_an_absence_proof(tmp_path):
    from pypdf import PdfReader

    class EmptyPortal(FakePortal):
        def open_dossier(self, siren, year):
            return []

    s = Store(tmp_path / 'db.sqlite')
    for siren in ['000000001', '987654321']:
        campaign = s.create_campaign(2025, tmp_path / 'sortie', [Dossier('Exemple', siren, 'Léa')])
        Collector(s, EmptyPortal()).run(campaign, threading.Event(), lambda x: None)
    proofs = list((tmp_path / 'sortie').rglob('*ABSENCE*.pdf'))
    assert len(proofs) == 2
    assert {PdfReader(path).metadata.get('/CollecteSiren') for path in proofs} == {'000000001', '987654321'}


def test_invalid_uncommitted_pdf_can_be_retried(tmp_path):
    d = Dossier('Exemple', '000000001', 'Léa')
    s = Store(tmp_path / 'db.sqlite')
    campaign = s.create_campaign(2025, tmp_path / 'sortie', [d])
    p = FakePortal()
    n = p.open_dossier(d.siren, 2025)[0]
    row = s.register_notice(campaign, d.siren, n)
    target = Path(row['path'])
    target.parent.mkdir(parents=True)
    target.write_bytes(b'broken')
    p = FakePortal()
    Collector(s, p).run(campaign, threading.Event(), lambda x: None)
    assert p.downloads == ['1', '2']
    assert all(r['status'] == 'TERMINE' for r in s.notices(campaign))
