import threading
from pathlib import Path

from reportlab.pdfgen import canvas

from collecte_cfe.collector import Collector
from collecte_cfe.domain import Dossier, Notice
from collecte_cfe.portal import PortalError, SessionExpired, parse_notice_cells
from collecte_cfe.store import Store


def test_parsing_uses_imposition_year_and_not_collection_year():
    n = parse_notice_cells(
        [
            '2026',
            '2025',
            '33',
            'BORDEAUX',
            '00021',
            "Avis d'imposition",
            '123',
            'Rôle général n°092',
            '31/10/2026',
            '15/12/2026',
            '100,00',
        ]
    )
    assert n.year == 2025 and n.kind == 'SOLDE'


class FakePortal:
    def __init__(self, fail=False, empty=False, expired=False):
        self.fail = fail
        self.empty = empty
        self.expired = expired
        self.downloads = []

    def open_dossier(self, siren, year):
        if self.expired:
            raise SessionExpired('Session expirée. Reconnectez-vous.')
        if self.empty:
            raise PortalError('Droits de consultation absents.')
        return [
            Notice(year, str(i), '00021', '33', 'BORDEAUX', role, '')
            for i, role in [(1, 'Rôle d’acompte'), (2, 'Rôle général')]
        ]

    def download(self, n, path):
        self.downloads.append(n.reference)
        if self.fail and n.reference == '2':
            raise PortalError('Téléchargement échoué.')
        c = canvas.Canvas(str(path))
        c.drawString(50, 700, 'Avis synthétique')
        c.showPage()
        c.save()

    def close_dossier(self):
        pass


def test_retries_only_failed_notice(tmp_path):
    s = Store(tmp_path / 'db.sqlite')
    campaign = s.create_campaign(2025, tmp_path / 'sortie', [Dossier('Exemple', '000000001', 'Léa')])
    p = FakePortal(fail=True)
    events = []
    Collector(s, p).run(campaign, threading.Event(), events.append)
    assert [r['status'] for r in s.notices(campaign)] == ['TERMINE', 'ERREUR']
    assert p.downloads == ['1', '2']
    p = FakePortal()
    Collector(s, p).run(campaign, threading.Event(), events.append)
    assert p.downloads == ['2']
    assert all(r['status'] == 'TERMINE' for r in s.notices(campaign))
    assert not list((tmp_path / 'sortie').rglob('*.part'))


def test_missing_rights_is_not_absence(tmp_path):
    s = Store(tmp_path / 'db.sqlite')
    campaign = s.create_campaign(2025, tmp_path / 'sortie', [Dossier('X', '123456789', 'A')])
    Collector(s, FakePortal(empty=True)).run(campaign, threading.Event(), lambda x: None)
    assert s.dossiers(campaign)[0]['status'] == 'ERREUR'
    assert not list((tmp_path / 'sortie').rglob('*.pdf'))


def test_capture_requested_for_failed_dossier_before_portal_closes(tmp_path):
    class FailingPortal(FakePortal):
        def __init__(self):
            super().__init__()
            self.closed = False

        def open_dossier(self, siren, year):
            raise PortalError('Message affiché sur le portail.')

        def capture_cfe_screen(self, path):
            assert not self.closed
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'PNG')
            return True

        def close_dossier(self):
            self.closed = True

    store = Store(tmp_path / 'db.sqlite')
    campaign = store.create_campaign(2025, tmp_path / 'sortie', [Dossier('Exemple', '000000001', 'Léa')])
    portal = FailingPortal()
    Collector(store, portal).run(campaign, threading.Event(), lambda _: None, capture_dir=tmp_path / 'captures')
    assert portal.closed
    assert (tmp_path / 'captures' / 'Exemple_000000001_CFE_2025.png').read_bytes() == b'PNG'


def test_empty_table_proof_uses_portal_page_when_available(tmp_path):
    from PIL import Image
    from pypdf import PdfReader

    class EmptyTablePortal(FakePortal):
        def open_dossier(self, siren, year):
            return []

        def capture_cfe_screen(self, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (400, 200), 'white').save(path)
            return True

    store = Store(tmp_path / 'db.sqlite')
    campaign = store.create_campaign(2025, tmp_path / 'sortie', [Dossier('Exemple', '000000001', 'Léa')])
    Collector(store, EmptyTablePortal()).run(campaign, threading.Event(), lambda _: None)
    row = store.dossiers(campaign)[0]
    assert row['status'] == 'ABSENT'
    assert PdfReader(row['proof_path']).metadata['/CollecteProofSource'] == 'PORTAL_CFE'


def test_without_a_capture_of_the_portal_the_dossier_is_retried_not_filed_as_absent(tmp_path):
    class BlindPortal(FakePortal):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        def open_dossier(self, siren, year):
            return []

        def capture_cfe_screen(self, path):
            self.attempts += 1
            return False

    store = Store(tmp_path / 'db.sqlite')
    campaign = store.create_campaign(2025, tmp_path / 'sortie', [Dossier('Exemple', '000000001', 'Léa')])
    portal = BlindPortal()
    Collector(store, portal).run(campaign, threading.Event(), lambda _: None)
    row = store.dossiers(campaign)[0]
    assert portal.attempts == 2, 'the page is captured a second time before giving up'
    assert row['status'] == 'ERREUR' and 'non capturée' in row['error']
    assert not list((tmp_path / 'sortie').rglob('*.pdf')), 'no proof without the portal page'


def test_expired_session_pauses_remaining_dossiers(tmp_path):
    s = Store(tmp_path / 'db.sqlite')
    campaign = s.create_campaign(
        2025, tmp_path / 'sortie', [Dossier('X', '123456789', 'A'), Dossier('Y', '987654321', 'A')]
    )
    Collector(s, FakePortal(expired=True)).run(campaign, threading.Event(), lambda x: None)
    assert s.campaign(campaign)['status'] == 'CONNEXION_REQUISE'
    assert s.dossiers(campaign)[1]['status'] == 'EN_ATTENTE'


def test_notice_details_are_saved_when_download_finishes_without_excel(tmp_path):
    class PaymentPortal(FakePortal):
        def open_dossier(self, siren, year):
            return [Notice(year, '123', '00021', '33', 'BORDEAUX', 'Rôle général', '')]

        def download(self, n, path):
            pdf = canvas.Canvas(str(path))
            pdf.drawString(50, 700, 'MONTANT DE VOTRE IMPOT 200,00 EUR')
            pdf.drawString(50, 680, 'MONTANT A PAYER 100,00 EUR')
            pdf.drawString(50, 660, "Vous avez choisi le PRELEVEMENT A L'ECHEANCE.")
            pdf.drawString(50, 640, 'sur le compte bancaire FR00 0000 0000 0000 XXXXXXXX 000.')
            pdf.showPage()
            pdf.save()

    s = Store(tmp_path / 'db.sqlite')
    campaign = s.create_campaign(2025, tmp_path / 'sortie', [Dossier('X', '123456789', 'A')])
    Collector(s, PaymentPortal()).run(campaign, threading.Event(), lambda _: None, finalize=False)
    notice = s.notices(campaign)[0]
    assert notice['status'] == 'TERMINE'
    assert notice['tax_amount_cents'] == 20000
    assert notice['amount_due_cents'] == 10000
    assert notice['payment_method'] == 'PRELEVEMENT_ECHEANCE'


def test_final_export_preserves_notice_integrity_reason(tmp_path):
    from collecte_cfe.documents import classify_pdf

    store = Store(tmp_path / 'db.sqlite')
    dossier = Dossier('Exemple', '000000001', 'Léa')
    campaign = store.create_campaign(2025, tmp_path / 'sortie', [dossier])
    notice = Notice(2025, '123', '00021', '33', 'BORDEAUX', 'Rôle général', '15/12/2025')
    row = store.register_notice(campaign, dossier.siren, notice)
    source = tmp_path / 'avis.pdf'
    pdf = canvas.Canvas(str(source))
    pdf.drawString(50, 700, 'Avis')
    pdf.showPage()
    pdf.save()
    digest = classify_pdf(source, Path(row['path']), dossier, notice)
    store.notice_result(row['id'], 'TERMINE', '', digest)
    Path(row['path']).write_bytes(Path(row['path']).read_bytes() + b'alteration')
    messages = []

    Collector(store, FakePortal()).run(campaign, threading.Event(), messages.append, dossiers=[])

    refusal = next(message for message in messages if 'Export Excel impossible' in message)
    assert '000000001' in refusal and 'relancez la collecte' in refusal.lower()
