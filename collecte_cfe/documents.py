import csv
import hashlib
import os
import re
import tempfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from .cfe_details import CfeDetails, explain, explanation_lines, read_details
from .domain import Dossier, Notice, safe_name
from .integrity import matches_sha256
from .notice_extraction import NoticeExtraction, extract_notice_pdf


def validate_pdf(path: Path) -> PdfReader:
    try:
        if path.stat().st_size > 100 * 1024 * 1024:
            raise ValueError('PDF trop volumineux pour le traitement automatique.')
        with path.open('rb') as stream:
            if not stream.read(1024).lstrip().startswith(b'%PDF-'):
                raise ValueError('Le fichier reçu n’est pas un PDF.')
        reader = PdfReader(path, strict=True)
        if reader.is_encrypted or not reader.pages:
            raise ValueError('PDF chiffré ou sans page : contrôle manuel nécessaire.')
        return reader
    except ValueError:
        raise
    except Exception:
        raise ValueError('PDF illisible ou incomplet : relancez cet avis.') from None


def identification(d: Dossier, year: int, kind: str, reference: str = '', absence=False) -> bytes:
    buffer = BytesIO()
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        'CollecteTitle',
        parent=styles['Title'],
        fontSize=26,
        leading=32,
        textColor=colors.HexColor('#163C57'),
        alignment=0,
    )
    body = ParagraphStyle('CollecteBody', parent=styles['BodyText'], fontSize=12, leading=18, spaceAfter=12)
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=48, leftMargin=48, topMargin=58, bottomMargin=48)
    label = 'Constat d’absence d’avis' if absence else 'Identification de l’avis'
    story = [Paragraph('Constat CFE', title), Spacer(1, 32), Paragraph(label, styles['Heading1']), Spacer(1, 18)]
    for text in [
        f'Dossier : {d.name}',
        f'SIREN : {d.siren}',
        f'Année d’imposition : {year}',
        f'Type : {kind}',
        f'Collaborateur : {d.collaborator}',
    ]:
        story.append(Paragraph(escape(text), body))
    if reference:
        story.append(Paragraph(escape(f'Référence de l’avis : {reference}'), body))
    story.append(Spacer(1, 28))
    if absence:
        story.append(
            Paragraph(
                f'Aucun avis correspondant à l’année d’imposition {year} n’a été trouvé lors de la consultation du compte fiscal dans la session connectée.',
                body,
            )
        )
        story.append(
            Paragraph(
                'Ce constat local est établi par l’outil de collecte du cabinet. Il ne constitue pas une attestation délivrée par la DGFiP et ne prouve pas une exonération.',
                body,
            )
        )
    else:
        story.append(
            Paragraph(
                'Le document fiscal téléchargé suit cette page d’identification. Son contenu n’a pas été recouvert par les informations de classement.',
                body,
            )
        )
    story.append(Spacer(1, 20))
    story.append(Paragraph(escape('Consultation : ' + datetime.now().strftime('%d/%m/%Y à %H:%M:%S')), body))
    doc.build(story)
    return buffer.getvalue()


def atomic_new(target: Path, write):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise ValueError('Un fichier existe déjà à cet emplacement : aucun écrasement effectué.')
    fd, tmp = tempfile.mkstemp(prefix='.collecte-', suffix='.part', dir=target.parent)
    os.close(fd)
    try:
        write(Path(tmp))
        # Publish the complete file atomically without replacing any existing document.
        if os.name == 'nt':
            os.rename(tmp, target)
        else:
            os.link(tmp, target)
    finally:
        Path(tmp).unlink(missing_ok=True)


def classify_pdf(source: Path, target: Path, d: Dossier, n: Notice) -> str:
    """File the notice exactly as the portal sent it; its identity is kept in the PDF properties only."""
    reader = validate_pdf(source)
    writer = PdfWriter()
    writer.append(reader)
    # Notices filed before this version start with an identification page; « NON » tells them apart.
    writer.add_metadata(
        {
            '/CollecteNoticeKey': n.key,
            '/CollecteSiren': d.siren,
            '/CollecteCoverPage': 'NON',
            '/Title': f'{d.name} CFE {n.year} {n.kind}',
        }
    )

    def write(path):
        with path.open('wb') as stream:
            writer.write(stream)
        validate_pdf(path)

    atomic_new(target, write)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    reader.close()
    writer.close()
    source.unlink()
    return digest


def verify_original_notice(source: Path, d: Dossier, n: Notice):
    reader = validate_pdf(source)
    try:
        text = '\n'.join(page.extract_text() or '' for page in reader.pages)

        def identifier(value):
            chars = re.sub(r'\s+', '', value)
            return re.search(r'(?<![A-Za-z0-9])' + r'\s*'.join(re.escape(c) for c in chars) + r'(?![A-Za-z0-9])', text)

        year_pattern = rf"(?:ann[ée]e\s+(?:d[’']?\s*)?imposition\s*[:\-]?\s*|cotisations?\s+){n.year}(?!\d)"
        # Some notices print no readable « année d'imposition »: their reference, printed on the PDF, starts with the year.
        reference_year = re.sub(r'\s+', '', n.reference)[:2] == str(n.year)[2:]
        year_confirmed = re.search(year_pattern, text, re.IGNORECASE) or (reference_year and identifier(n.reference))
        if not identifier(d.siren) or not identifier(n.reference) or not year_confirmed:
            raise ValueError(
                'Identité du PDF non confirmée (SIREN, année ou référence). Avis non classé : contrôle manuel nécessaire.'
            )
    finally:
        reader.close()


def pdf_mark(metadata, name):
    """A mark written in a classified PDF; PDFs from before the rename carry it under the old prefix."""
    return metadata.get(f'/Collecte{name}', metadata.get(f'/Bubot{name}'))


def recover_pdf(target: Path, d: Dossier, n: Notice) -> str | None:
    if not target.exists():
        return None
    reader = validate_pdf(target)
    try:
        metadata = reader.metadata or {}
        if pdf_mark(metadata, 'NoticeKey') != n.key or pdf_mark(metadata, 'Siren') != d.siren:
            raise ValueError('Le PDF existant ne correspond pas à cet avis : contrôle manuel nécessaire.')
    finally:
        reader.close()
    return hashlib.sha256(target.read_bytes()).hexdigest()


def proof_pdf(target: Path, d: Dossier, year: int):
    data = identification(d, year, 'ABSENCE CONSTATÉE', absence=True)
    writer = PdfWriter()
    writer.append(PdfReader(BytesIO(data)))
    writer.add_metadata({'/CollecteSiren': d.siren, '/CollecteYear': str(year), '/CollecteProof': 'ABSENCE'})

    def write(path):
        with path.open('wb') as stream:
            writer.write(stream)

    atomic_new(target, write)
    writer.close()
    return hashlib.sha256(target.read_bytes()).hexdigest()


def recover_proof(target: Path, d: Dossier, year: int, kind: str = 'ABSENCE') -> str | None:
    if not target.exists():
        return None
    reader = validate_pdf(target)
    try:
        metadata = reader.metadata or {}
        if (
            pdf_mark(metadata, 'Siren') != d.siren
            or pdf_mark(metadata, 'Year') != str(year)
            or pdf_mark(metadata, 'Proof') != kind
        ):
            raise ValueError('La preuve existante ne correspond pas au dossier et à l’année.')
    finally:
        reader.close()
    return hashlib.sha256(target.read_bytes()).hexdigest()


PROOFS = {
    'ABSENCE': ('absence d’avis', "Constat d'absence d'avis CFE", 'PORTAL_CFE'),
    'SANS_HABILITATION': ('absence d’habilitation', "Constat d'absence d'habilitation CFE", 'PORTAL_RECHERCHE'),
}


def proof_from_portal_capture(target: Path, d: Dossier, year: int, capture: Path) -> str:
    """Preserve the confirmed impots.gouv.fr absence page inside the dossier proof."""
    return proof_from_captures(
        target, d, year, [('Page du portail impots.gouv.fr affichée pour ce dossier :', capture)]
    )


def proof_from_captures(
    target: Path, d: Dossier, year: int, captures: list[tuple[str, Path]], kind: str = 'ABSENCE'
) -> str:
    """One page per portal capture, under a header naming the dossier; written to the client's folder."""
    subject, heading, source = PROOFS[kind]
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.setTitle(f'{d.name} CFE {year} - {subject}')
    for caption, capture in captures:
        with Image.open(capture) as image:
            if image.format not in ('PNG', 'JPEG') or image.width < 100 or image.height < 100:
                raise ValueError('Capture du portail illisible : preuve non créée.')
            width, height = image.size
            page_width, page_height = max(842, width + 72), max(595, height + 150)
            pdf.setPageSize((page_width, page_height))
            pdf.setFont('Helvetica-Bold', 16)
            pdf.drawString(36, page_height - 40, heading)
            pdf.setFont('Helvetica', 11)
            pdf.drawString(36, page_height - 62, f'Dossier : {d.name}  |  SIREN : {d.siren}  |  Année CFE : {year}')
            pdf.drawString(36, page_height - 80, caption)
            pdf.drawImage(ImageReader(image), 36, page_height - 110 - height, width=width, height=height)
            pdf.showPage()
    pdf.save()
    writer = PdfWriter()
    writer.append(PdfReader(BytesIO(buffer.getvalue())))
    writer.add_metadata(
        {
            '/CollecteSiren': d.siren,
            '/CollecteYear': str(year),
            '/CollecteProof': kind,
            '/CollecteProofSource': source,
            '/CollecteSourceSha256': ' '.join(
                hashlib.sha256(capture.read_bytes()).hexdigest() for _caption, capture in captures
            ),
        }
    )

    def write(path):
        with path.open('wb') as stream:
            writer.write(stream)
        validate_pdf(path)

    atomic_new(target, write)
    writer.close()
    return hashlib.sha256(target.read_bytes()).hexdigest()


def ensure_notice_details(store, row: dict, *, force=False) -> dict:
    """Read a classified notice once; an unreadable PDF remains a review item."""
    if row['status'] != 'TERMINE' or (row.get('extraction_status') and not force):
        return row
    path = Path(row['path'])
    if not path.is_file():
        return row
    try:
        result = extract_notice_pdf(path)
    except Exception:
        result = NoticeExtraction()
    store.notice_extraction_result(row['id'], result)
    return {
        **row,
        'tax_amount_cents': result.tax_amount_cents,
        'amount_due_cents': result.amount_due_cents,
        'payment_method': result.payment_method,
        'bank_account_display': result.bank_account_display or '',
        'bank_account_masked': int(result.bank_account_masked),
        'extraction_status': result.review_status,
        'refund_cents': result.refund_cents,
    }


def row_notice(row: dict) -> Notice:
    return Notice(
        row['year'], row['reference'], row['nic'], row['department'], row['commune'], row['role'], row['due_date']
    )


def ensure_breakdown(store, row: dict) -> CfeDetails | None:
    """Calculation details of a classified notice, read from its PDF the first time they are needed."""
    if row['status'] != 'TERMINE':
        return None
    known = store.breakdown(row['siren'], row['year'], row['nic'], row['reference'])
    if known is not None or not Path(row['path']).is_file():
        return known
    try:
        details = read_details(row['path'])
    except Exception:
        details = None
    if details is not None:
        store.save_breakdown(row['siren'], row_notice(row), details, 'CAMPAGNE')
    return details


def change_explanation(store, row: dict) -> list[str]:
    """Why this notice differs from last year's, when its calculation details can be read."""
    if row.get('kind') != 'SOLDE':
        return []
    current = ensure_breakdown(store, row)
    if current is None:
        return []
    return explanation_lines(explain(current, store.breakdown(row['siren'], row['year'] - 1, row['nic'])))


def export_batch_actions(store, batch: int) -> list[Path]:
    campaigns = store.batch_campaigns(batch)
    if not campaigns:
        raise ValueError('Campagne introuvable.')
    years = '_'.join(str(c['year']) for c in campaigns)
    return export_actions(
        store, campaigns[0]['id'], campaign_ids=[c['id'] for c in campaigns], label=f'{years}_campagne_{batch}'
    )


def export_batch_timings(store, batch: int) -> Path:
    campaigns = store.batch_campaigns(batch)
    if not campaigns:
        raise ValueError('Campagne introuvable.')
    batch_info = store.batch(batch)
    directory = Path(campaigns[0]['output'])
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f'Temps_collecte_CFE_{safe_name(batch_info["name"])}.csv'
    headers = [
        'Horodatage',
        'Session',
        'Nom du dossier',
        'SIREN',
        'Collaborateur',
        'Année',
        'Étape',
        'Type d’avis',
        'Référence',
        'Résultat',
        'Durée (secondes)',
        'Information',
    ]

    def text_cell(value):
        value = '' if value is None else str(value)
        return "'" + value if value.startswith(('=', '+', '-', '@')) else value

    fd, tmp = tempfile.mkstemp(prefix='.collecte-', suffix='.csv', dir=directory)
    os.close(fd)
    try:
        with open(tmp, 'w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream, delimiter=';')
            writer.writerow(headers)
            for row in store.timings(batch):
                writer.writerow(
                    [
                        text_cell(row['recorded']),
                        row['session'] or '',
                        text_cell(row['dossier']),
                        text_cell(row['siren']),
                        text_cell(row['collaborator']),
                        row['year'],
                        text_cell(row['phase']),
                        text_cell(row['kind']),
                        text_cell(row['reference']),
                        text_cell(row['status']),
                        f'{row["seconds"]:.3f}'.replace('.', ','),
                        text_cell(row['information']),
                    ]
                )
        os.replace(tmp, target)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return target


def workbook_name(campaign_name: str, scope: str) -> str:
    """« Suivi CFE 2025 · Portefeuille — Léa.xlsx », readable in the client folders."""
    return safe_name(f'Suivi {campaign_name} — {scope}') + '.xlsx'


def campaign_groups(store, campaign_ids) -> dict:
    """The dossiers and notices of the campaigns, by collaborator, after checking that every filed document is intact."""
    selected_campaigns = [
        (store.campaign(campaign_id), store.notices(campaign_id), store.dossiers(campaign_id))
        for campaign_id in campaign_ids
    ]
    for _campaign, notices, dossiers in selected_campaigns:
        for notice in notices:
            if notice['status'] == 'TERMINE' and not matches_sha256(notice.get('path', ''), notice.get('sha256')):
                raise ValueError(
                    f'Avis classé absent, modifié ou sans empreinte pour le SIREN {notice["siren"]} : relancez la collecte avant l’export.'
                )
        for dossier in dossiers:
            if dossier['status'] == 'ABSENT' and not matches_sha256(
                dossier.get('proof_path', ''), dossier.get('proof_sha256')
            ):
                raise ValueError(
                    f'Preuve d’absence absente, modifiée ou sans empreinte pour le SIREN {dossier["siren"]} : relancez la collecte avant l’export.'
                )
    groups = {}
    for campaign_info, notices, dossiers in selected_campaigns:
        year = campaign_info['year']
        by_siren = {}
        for notice in notices:
            if notice['status'] == 'TERMINE':
                notice = ensure_notice_details(store, notice)
            by_siren.setdefault(notice['siren'], []).append(notice)
        for dossier in dossiers:
            group = groups.setdefault(dossier['collaborator'], {'records': [], 'notices': []})
            found = by_siren.get(dossier['siren'], [])
            classified = [notice for notice in found if notice['status'] == 'TERMINE']
            # Last year's explanation of the main notice, as in the dossier sheet of the application.
            evolution = next((lines for notice in classified if (lines := change_explanation(store, notice))), [])
            folder = Path(campaign_info['output']) / safe_name(dossier['collaborator']) / safe_name(dossier['name'])
            group['records'].append(
                {'dossier': dossier, 'year': year, 'notices': classified, 'evolution': evolution, 'folder': folder}
            )
            group['notices'].extend({'dossier': dossier, 'year': year, 'notice': notice} for notice in found)
    return groups


def save_workbook(directory: Path, name: str, workbook) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    fd, tmp = tempfile.mkstemp(prefix='.collecte-', suffix='.xlsx', dir=directory)
    os.close(fd)
    try:
        workbook.save(tmp)
        os.replace(tmp, target)
    finally:
        Path(tmp).unlink(missing_ok=True)
        workbook.close()
    return target


def export_actions(store, campaign: int, *, campaign_ids=None, label=None) -> list[Path]:
    """One follow-up workbook per collaborator in their folder, and one for the whole cabinet when several share the campaign."""
    info = store.campaign(campaign)
    campaign_name = store.batch(info['batch'])['name'] if info.get('batch') else f'CFE {info["year"]}'
    groups = campaign_groups(store, campaign_ids or [campaign])
    # Excel support loads in about half a second: only when exporting, never at start-up.
    from .reporting import action_workbook

    output = Path(info['output'])
    books = []
    for collaborator, group in sorted(groups.items(), key=lambda item: item[0].casefold()):
        books.append(
            (
                output / safe_name(collaborator),
                workbook_name(campaign_name, collaborator or 'À affecter'),
                action_workbook(
                    campaign_name, f'Collaborateur : {collaborator or "À affecter"}', group['records'], group['notices']
                ),
            )
        )
    if len(groups) > 1:
        books.append(
            (
                output,
                workbook_name(campaign_name, 'cabinet'),
                cabinet_workbook(campaign_name, groups, f'Cabinet · {len(groups)} collaborateurs'),
            )
        )
    return [save_workbook(directory, name, workbook) for directory, name, workbook in books]


def cabinet_workbook(campaign_name, groups, scope):
    from .reporting import action_workbook

    records = [record for group in groups.values() for record in group['records']]
    notices = [notice for group in groups.values() for notice in group['notices']]
    return action_workbook(campaign_name, scope, records, notices, cabinet=True)


def archive_batch_workbook(store, batch: int) -> Path:
    """The whole campaign in one workbook, at the root of its output folder, to keep once it is over."""
    campaigns = store.batch_campaigns(batch)
    if not campaigns:
        raise ValueError('Campagne introuvable.')
    name = store.batch(batch)['name']
    groups = campaign_groups(store, [campaign['id'] for campaign in campaigns])
    workbook = cabinet_workbook(
        name,
        groups,
        f'Archive du {datetime.now():%d/%m/%Y} · '
        + (f'{len(groups)} collaborateurs' if len(groups) > 1 else 'un collaborateur'),
    )
    return save_workbook(
        Path(campaigns[0]['output']), safe_name(f'Archive {name} — {datetime.now():%Y-%m-%d}') + '.xlsx', workbook
    )
