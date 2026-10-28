import json
import math
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .cfe_details import CfeDetails
from .client_mail import MailTemplate
from .domain import Dossier, Notice, safe_name
from .notice_extraction import NoticeExtraction
from .portfolio import valid_client_email


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS campaigns (
              id INTEGER PRIMARY KEY, year INTEGER NOT NULL, output TEXT NOT NULL,
              created TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'EN_ATTENTE');
            CREATE TABLE IF NOT EXISTS dossiers (
              campaign INTEGER REFERENCES campaigns(id), siren TEXT, name TEXT NOT NULL,
              collaborator TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'EN_ATTENTE',
              error TEXT NOT NULL DEFAULT '', checked TEXT NOT NULL DEFAULT '',
              PRIMARY KEY(campaign,siren));
            CREATE TABLE IF NOT EXISTS notices (
              id INTEGER PRIMARY KEY, campaign INTEGER, siren TEXT, business_key TEXT,
              year INTEGER, reference TEXT, nic TEXT, department TEXT, commune TEXT,
              role TEXT, kind TEXT, due_date TEXT, path TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'EN_ATTENTE', error TEXT NOT NULL DEFAULT '',
              sha256 TEXT NOT NULL DEFAULT '',
              FOREIGN KEY(campaign,siren) REFERENCES dossiers(campaign,siren),
              UNIQUE(campaign,siren,business_key));
            """)
            notice_columns = {r['name'] for r in c.execute('PRAGMA table_info(notices)')}
            additions = {
                'tax_amount_cents': 'INTEGER',
                'amount_due_cents': 'INTEGER',
                'payment_method': "TEXT NOT NULL DEFAULT ''",
                'bank_account_display': "TEXT NOT NULL DEFAULT ''",
                'bank_account_masked': 'INTEGER NOT NULL DEFAULT 0',
                'extraction_status': "TEXT NOT NULL DEFAULT ''",
                'refund_cents': 'INTEGER',
            }
            for column, declaration in additions.items():
                if column not in notice_columns:
                    c.execute(f'ALTER TABLE notices ADD COLUMN {column} {declaration}')
            columns = {r['name'] for r in c.execute('PRAGMA table_info(dossiers)')}
            for column in ('proof_path', 'proof_sha256'):
                if column not in columns:
                    c.execute(f"ALTER TABLE dossiers ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
            if 'email' not in columns:
                c.execute("ALTER TABLE dossiers ADD COLUMN email TEXT NOT NULL DEFAULT ''")
            if 'pointed_at' not in columns:
                c.execute("ALTER TABLE dossiers ADD COLUMN pointed_at TEXT NOT NULL DEFAULT ''")
            for column in ('mail_opened_at', 'mail_sent_at', 'verified_at'):
                if column not in columns:
                    c.execute(f"ALTER TABLE dossiers ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
            # Failed attempts in a row: after two, the dossier waits for a manual check instead of a new automatic try.
            if 'failures' not in columns:
                c.execute('ALTER TABLE dossiers ADD COLUMN failures INTEGER NOT NULL DEFAULT 0')
            c.execute("""CREATE TABLE IF NOT EXISTS batches (
                id INTEGER PRIMARY KEY, created TEXT NOT NULL, source TEXT NOT NULL,
                sessions INTEGER NOT NULL, discovered INTEGER NOT NULL DEFAULT 0)""")
            batch_columns = {r['name'] for r in c.execute('PRAGMA table_info(batches)')}
            if 'name' not in batch_columns:
                c.execute("ALTER TABLE batches ADD COLUMN name TEXT NOT NULL DEFAULT ''")
            for column in ('archived_at', 'archive_path'):
                if column not in batch_columns:
                    c.execute(f"ALTER TABLE batches ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
            c.execute("UPDATE batches SET name='Campagne ' || id WHERE TRIM(name)='' ")
            if 'batch' not in {r['name'] for r in c.execute('PRAGMA table_info(campaigns)')}:
                c.execute('ALTER TABLE campaigns ADD COLUMN batch INTEGER REFERENCES batches(id)')
            # Existing annual campaigns remain visible in the new batch interface.
            for old in c.execute('SELECT * FROM campaigns WHERE batch IS NULL').fetchall():
                b = c.execute(
                    "INSERT INTO batches(created,source,sessions,discovered,name) VALUES (?,?,2,1,'')",
                    (old['created'], 'FILE'),
                ).lastrowid
                c.execute('UPDATE batches SET name=? WHERE id=?', (f'Campagne {b}', b))
                c.execute('UPDATE campaigns SET batch=? WHERE id=?', (b, old['id']))
            c.execute("""CREATE TABLE IF NOT EXISTS collection_timings (
                id INTEGER PRIMARY KEY, batch INTEGER NOT NULL REFERENCES batches(id),
                campaign INTEGER NOT NULL REFERENCES campaigns(id), siren TEXT NOT NULL,
                session INTEGER, phase TEXT NOT NULL, notice_key TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT '', reference TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL, seconds REAL NOT NULL, recorded TEXT NOT NULL,
                information TEXT NOT NULL DEFAULT '')""")
            c.execute("""CREATE TABLE IF NOT EXISTS expert_accounts (
                id INTEGER PRIMARY KEY, label TEXT NOT NULL, login TEXT NOT NULL UNIQUE)""")
            # One session per expert account beyond four: the old 0-3 bound on the session number is lifted.
            old = c.execute("SELECT sql FROM sqlite_master WHERE name='session_accounts'").fetchone()
            if old and 'BETWEEN 0 AND 3' in old['sql']:
                c.execute('ALTER TABLE session_accounts RENAME TO session_accounts_old')
            c.execute("""CREATE TABLE IF NOT EXISTS session_accounts (
                session INTEGER PRIMARY KEY CHECK(session>=0),
                account INTEGER NOT NULL REFERENCES expert_accounts(id) ON DELETE CASCADE)""")
            if old and 'BETWEEN 0 AND 3' in old['sql']:
                c.execute(
                    'INSERT INTO session_accounts(session,account) SELECT session,account FROM session_accounts_old'
                )
                c.execute('DROP TABLE session_accounts_old')
            c.execute("""CREATE TABLE IF NOT EXISTS account_login_blocks (
                account INTEGER PRIMARY KEY REFERENCES expert_accounts(id) ON DELETE CASCADE,
                created TEXT NOT NULL)""")
            c.execute('CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            # The in-app notification list was removed: its alerts are now messages in the corner.
            c.execute('DROP TABLE IF EXISTS user_notifications')
            # Calculation details read on notices, including last year's notice read only to explain changes.
            c.execute("""CREATE TABLE IF NOT EXISTS cfe_breakdowns (
                siren TEXT NOT NULL, year INTEGER NOT NULL, nic TEXT NOT NULL, reference TEXT NOT NULL,
                kind TEXT NOT NULL, details TEXT NOT NULL, source TEXT NOT NULL, read_at TEXT NOT NULL,
                PRIMARY KEY(siren,year,reference))""")

    def mail_template(self) -> MailTemplate:
        """The cabinet's wording for client mails, shared by every campaign of this database."""
        with self.connection() as c:
            rows = {
                row['key']: row['value']
                for row in c.execute("SELECT key,value FROM app_meta WHERE key IN ('mail_subject','mail_body')")
            }
        default = MailTemplate()
        return MailTemplate(rows.get('mail_subject', default.subject), rows.get('mail_body', default.body))

    def save_mail_template(self, template: MailTemplate | None):
        """Keep the cabinet's wording; None goes back to the default one."""
        with self.connection() as c:
            c.execute("DELETE FROM app_meta WHERE key IN ('mail_subject','mail_body')")
            if template is not None:
                template.check()
                c.executemany(
                    'INSERT INTO app_meta(key,value) VALUES (?,?)',
                    (('mail_subject', template.subject), ('mail_body', template.body)),
                )

    def vault_id(self) -> str:
        """This database's name in the Windows vault; it stays the same when the data folder moves (USB key, rename)."""
        with self.connection() as c:
            row = c.execute("SELECT value FROM app_meta WHERE key='vault_id'").fetchone()
            if row:
                return row['value']
            value = uuid.uuid4().hex[:20]
            c.execute("INSERT INTO app_meta(key,value) VALUES ('vault_id',?)", (value,))
            return value

    @contextmanager
    def connection(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        try:
            with c:
                yield c
        finally:
            c.close()

    def expert_accounts(self):
        with self.connection() as c:
            return [dict(row) for row in c.execute('SELECT id,label,login FROM expert_accounts ORDER BY id')]

    def add_expert_account(self, label, login):
        with self.connection() as c:
            return c.execute('INSERT INTO expert_accounts(label,login) VALUES (?,?)', (label, login)).lastrowid

    def update_expert_account(self, account, label, login, clear_block=False):
        with self.connection() as c:
            if not c.execute('SELECT 1 FROM expert_accounts WHERE id=?', (account,)).fetchone():
                raise ValueError('Compte expert introuvable.')
            c.execute('UPDATE expert_accounts SET label=?,login=? WHERE id=?', (label, login, account))
            if clear_block:
                c.execute('DELETE FROM account_login_blocks WHERE account=?', (account,))

    def block_account_login(self, account):
        with self.connection() as c:
            c.execute(
                'INSERT OR REPLACE INTO account_login_blocks(account,created) VALUES (?,?)',
                (account, datetime.now().isoformat(timespec='seconds')),
            )

    def account_login_blocked(self, account):
        with self.connection() as c:
            return c.execute('SELECT 1 FROM account_login_blocks WHERE account=?', (account,)).fetchone() is not None

    def delete_expert_account(self, account):
        with self.connection() as c:
            c.execute('DELETE FROM expert_accounts WHERE id=?', (account,))

    def assign_expert_account(self, session, account):
        if not isinstance(session, int) or session < 0:
            raise ValueError('Numéro de session invalide.')
        with self.connection() as c:
            if account is None:
                c.execute('DELETE FROM session_accounts WHERE session=?', (session,))
            else:
                if not c.execute('SELECT 1 FROM expert_accounts WHERE id=?', (account,)).fetchone():
                    raise ValueError('Compte expert introuvable.')
                c.execute('INSERT OR REPLACE INTO session_accounts(session,account) VALUES (?,?)', (session, account))

    def assigned_expert_account(self, session):
        if not isinstance(session, int) or session < 0:
            raise ValueError('Numéro de session invalide.')
        with self.connection() as c:
            row = c.execute(
                """SELECT a.id,a.label,a.login FROM expert_accounts a
                JOIN session_accounts s ON s.account=a.id WHERE s.session=?""",
                (session,),
            ).fetchone()
            return dict(row) if row else None

    def create_campaign(self, year: int, output: Path | str, dossiers: list[Dossier]) -> int:
        if not dossiers or not 2010 <= year <= 2100:
            raise ValueError('Portefeuille et année valides obligatoires.')
        with self.connection() as c:
            created = datetime.now().isoformat(timespec='seconds')
            batch = c.execute(
                "INSERT INTO batches(created,source,sessions,discovered,name) VALUES (?,?,2,1,'')", (created, 'FILE')
            ).lastrowid
            c.execute('UPDATE batches SET name=? WHERE id=?', (f'Campagne {batch}', batch))
            campaign = c.execute(
                'INSERT INTO campaigns(year,output,created,batch) VALUES (?,?,?,?)',
                (year, str(Path(output).resolve()), created, batch),
            ).lastrowid
            c.executemany(
                'INSERT INTO dossiers(campaign,siren,name,collaborator,email) VALUES (?,?,?,?,?)',
                [(campaign, d.siren, d.name, d.collaborator, d.email) for d in dossiers],
            )
            return campaign

    def create_batch(self, years, output, dossiers, sessions=2, name=''):
        years = sorted(set(years))
        if (
            not years
            or any(not isinstance(y, int) or not 2016 <= y <= 2100 for y in years)
            or not isinstance(sessions, int)
            or sessions < 1
        ):
            raise ValueError('Choisissez au moins une année (2016–2100) et une session au moins.')
        self.validate_dossiers(dossiers)
        with self.connection() as c:
            created = datetime.now().isoformat(timespec='seconds')
            clean_name = ' '.join(str(name or '').split())[:100]
            b = c.execute(
                "INSERT INTO batches(created,source,sessions,discovered,name) VALUES (?,?,?,?, '')",
                (created, 'FILE' if dossiers else 'ACCOUNT', sessions, int(bool(dossiers))),
            ).lastrowid
            c.execute('UPDATE batches SET name=? WHERE id=?', (clean_name or f'Campagne {b}', b))
            for year in years:
                campaign = c.execute(
                    'INSERT INTO campaigns(year,output,created,batch) VALUES (?,?,?,?)',
                    (year, str(Path(output).resolve()), created, b),
                ).lastrowid
                c.executemany(
                    'INSERT INTO dossiers(campaign,siren,name,collaborator,email) VALUES (?,?,?,?,?)',
                    [(campaign, d.siren, d.name, d.collaborator, d.email) for d in dossiers],
                )
            return b

    @staticmethod
    def validate_dossiers(dossiers):
        sirens = set()
        paths = set()
        collaborators = {}
        for d in dossiers:
            path = (safe_name(d.collaborator).casefold(), safe_name(d.name).casefold())
            if (
                not d.name.strip()
                or not d.collaborator.strip()
                or not re.fullmatch(r'[0-9]{9}', d.siren)
                or d.siren in sirens
                or path in paths
            ):
                raise ValueError('Portefeuille invalide : données manquantes, doublon ou collision de classement.')
            if d.email and not valid_client_email(d.email):
                raise ValueError('Adresse e-mail du client invalide.')
            if path[0] in collaborators and collaborators[path[0]] != d.collaborator:
                raise ValueError('Collision de classement entre collaborateurs.')
            collaborators[path[0]] = d.collaborator
            sirens.add(d.siren)
            paths.add(path)

    def batches(self):
        with self.connection() as c:
            return [dict(r) for r in c.execute('SELECT * FROM batches ORDER BY id DESC')]

    def batch(self, batch):
        with self.connection() as c:
            r = c.execute('SELECT * FROM batches WHERE id=?', (batch,)).fetchone()
            if r is None:
                raise ValueError('Campagne introuvable.')
            return dict(r)

    def update_batch_sessions(self, batch, sessions):
        if not isinstance(sessions, int) or sessions < 1:
            raise ValueError('Il faut au moins une session.')
        with self.connection() as c:
            if not c.execute('SELECT 1 FROM batches WHERE id=?', (batch,)).fetchone():
                raise ValueError('Campagne introuvable.')
            c.execute('UPDATE batches SET sessions=? WHERE id=?', (sessions, batch))

    def rename_batch(self, batch, name):
        clean_name = ' '.join(str(name or '').split())[:100]
        if not clean_name:
            raise ValueError('Indiquez un nom de campagne.')
        with self.connection() as c:
            if not c.execute('SELECT 1 FROM batches WHERE id=?', (batch,)).fetchone():
                raise ValueError('Campagne introuvable.')
            c.execute('UPDATE batches SET name=? WHERE id=?', (clean_name, batch))

    def update_client_email(self, batch, siren, email):
        email = str(email or '').strip()
        if email and not valid_client_email(email):
            raise ValueError('Adresse e-mail du client invalide.')
        with self.connection() as c:
            found = c.execute(
                """SELECT 1 FROM dossiers d JOIN campaigns a ON a.id=d.campaign
                WHERE a.batch=? AND d.siren=? LIMIT 1""",
                (batch, siren),
            ).fetchone()
            if not found:
                raise ValueError('Dossier introuvable dans cette campagne.')
            c.execute(
                """UPDATE dossiers SET email=? WHERE siren=? AND campaign IN
                (SELECT id FROM campaigns WHERE batch=?)""",
                (email, siren, batch),
            )

    def mark_mail_opened(self, batch, siren) -> str:
        """Record that the client's draft was opened, for every year of the campaign."""
        stamp = datetime.now().isoformat(timespec='seconds')
        with self.connection() as c:
            c.execute(
                """UPDATE dossiers SET mail_opened_at=? WHERE siren=? AND campaign IN
                (SELECT id FROM campaigns WHERE batch=?)""",
                (stamp, siren, batch),
            )
        return stamp

    def mark_mail_sent(self, batch, siren, sent: bool) -> str:
        """Record that the collaborator sent the client's mail, for every year of the campaign; an empty stamp removes the mark."""
        stamp = datetime.now().isoformat(timespec='seconds') if sent else ''
        with self.connection() as c:
            c.execute(
                """UPDATE dossiers SET mail_sent_at=? WHERE siren=? AND campaign IN
                (SELECT id FROM campaigns WHERE batch=?)""",
                (stamp, siren, batch),
            )
        return stamp

    def delete_batch(self, batch):
        """Supprime les données locales de campagne, jamais les fichiers de sortie."""
        with self.connection() as c:
            if not c.execute('SELECT 1 FROM batches WHERE id=?', (batch,)).fetchone():
                raise ValueError('Campagne introuvable.')
            c.execute('DELETE FROM collection_timings WHERE batch=?', (batch,))
            campaign_ids = [row['id'] for row in c.execute('SELECT id FROM campaigns WHERE batch=?', (batch,))]
            for campaign in campaign_ids:
                c.execute('DELETE FROM notices WHERE campaign=?', (campaign,))
                c.execute('DELETE FROM dossiers WHERE campaign=?', (campaign,))
            c.execute('DELETE FROM campaigns WHERE batch=?', (batch,))
            c.execute('DELETE FROM batches WHERE id=?', (batch,))

    def batch_campaigns(self, batch):
        with self.connection() as c:
            return [dict(r) for r in c.execute('SELECT * FROM campaigns WHERE batch=? ORDER BY year', (batch,))]

    def populate_batch(self, batch, dossiers):
        if not dossiers:
            raise ValueError('Aucun dossier découvert : vérifiez les droits du compte expert.')
        self.validate_dossiers(dossiers)
        with self.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            b = c.execute('SELECT * FROM batches WHERE id=?', (batch,)).fetchone()
            if not b or b['source'] != 'ACCOUNT' or b['discovered']:
                raise ValueError('Le portefeuille de cette campagne est déjà enregistré.')
            for campaign in c.execute('SELECT id FROM campaigns WHERE batch=?', (batch,)).fetchall():
                c.executemany(
                    'INSERT INTO dossiers(campaign,siren,name,collaborator,email) VALUES (?,?,?,?,?)',
                    [(campaign['id'], d.siren, d.name, d.collaborator, d.email) for d in dossiers],
                )
            c.execute('UPDATE batches SET discovered=1 WHERE id=?', (batch,))

    def campaigns(self):
        with self.connection() as c:
            return [dict(r) for r in c.execute('SELECT * FROM campaigns ORDER BY id DESC')]

    def campaign(self, campaign: int):
        with self.connection() as c:
            r = c.execute('SELECT * FROM campaigns WHERE id=?', (campaign,)).fetchone()
            if r is None:
                raise ValueError('Campagne introuvable.')
            return dict(r)

    def campaign_status(self, campaign: int, status: str):
        with self.connection() as c:
            c.execute('UPDATE campaigns SET status=? WHERE id=?', (status, campaign))

    def dossiers(self, campaign: int):
        with self.connection() as c:
            return [
                dict(r)
                for r in c.execute(
                    'SELECT * FROM dossiers WHERE campaign=? ORDER BY collaborator,name,siren', (campaign,)
                )
            ]

    def dossier_result(self, campaign: int, siren: str, status: str, error: str = ''):
        with self.connection() as c:
            c.execute(
                'UPDATE dossiers SET status=?,error=?,checked=? WHERE campaign=? AND siren=?',
                (status, error, datetime.now().isoformat(timespec='seconds'), campaign, siren),
            )
            # An error counts one more failure; a dossier collected or proved without notice starts again from zero.
            if status == 'ERREUR':
                c.execute('UPDATE dossiers SET failures=failures+1 WHERE campaign=? AND siren=?', (campaign, siren))
            elif status in ('TERMINE', 'ABSENT', 'SANS_HABILITATION'):
                c.execute(
                    "UPDATE dossiers SET failures=0,verified_at='' WHERE campaign=? AND siren=?", (campaign, siren)
                )

    def verify_dossier(self, campaign: int, siren: str, verified: bool) -> str:
        """Record that the collaborator checked by hand a dossier the collection could not handle; an empty stamp removes the mark."""
        with self.connection() as c:
            row = c.execute('SELECT status FROM dossiers WHERE campaign=? AND siren=?', (campaign, siren)).fetchone()
            if row is None or row['status'] != 'ERREUR':
                raise ValueError('Seuls les dossiers en échec peuvent être marqués vérifiés.')
            stamp = datetime.now().isoformat(timespec='seconds') if verified else ''
            c.execute('UPDATE dossiers SET verified_at=? WHERE campaign=? AND siren=?', (stamp, campaign, siren))
            return stamp

    def archive_batch(self, batch, path=None) -> str:
        """Mark the campaign archived with the workbook that keeps it; without a path, take it out of the archive."""
        stamp = datetime.now().isoformat(timespec='seconds') if path else ''
        with self.connection() as c:
            if not c.execute('SELECT 1 FROM batches WHERE id=?', (batch,)).fetchone():
                raise ValueError('Campagne introuvable.')
            c.execute('UPDATE batches SET archived_at=?,archive_path=? WHERE id=?', (stamp, str(path or ''), batch))
        return stamp

    def point_dossier(self, campaign: int, siren: str, pointed: bool) -> str:
        """Record that the collaborator handled this dossier and year; an empty stamp removes the mark."""
        with self.connection() as c:
            row = c.execute('SELECT status FROM dossiers WHERE campaign=? AND siren=?', (campaign, siren)).fetchone()
            if row is None or row['status'] != 'TERMINE':
                raise ValueError('Seuls les dossiers avec avis classés peuvent être pointés.')
            stamp = datetime.now().isoformat(timespec='seconds') if pointed else ''
            c.execute('UPDATE dossiers SET pointed_at=? WHERE campaign=? AND siren=?', (stamp, campaign, siren))
            return stamp

    def register_notice(self, campaign: int, siren: str, n: Notice):
        kind = n.kind
        with self.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            existing = c.execute(
                'SELECT * FROM notices WHERE campaign=? AND siren=? AND business_key=?', (campaign, siren, n.key)
            ).fetchone()
            if existing:
                return dict(existing)
            d = c.execute('SELECT * FROM dossiers WHERE campaign=? AND siren=?', (campaign, siren)).fetchone()
            campaign_row = c.execute('SELECT * FROM campaigns WHERE id=?', (campaign,)).fetchone()
            if d is None or campaign_row is None or campaign_row['year'] != n.year:
                raise ValueError('Avis incompatible avec le dossier ou l’année de campagne.')
            previous_hash = ''
            for number in range(1, 10001):
                suffix = '' if number == 1 else f'_{number:02}'
                name = f'{safe_name(d["name"])}_CFE_{n.year}_{kind}{suffix}.pdf'
                path = Path(campaign_row['output']) / safe_name(d['collaborator']) / safe_name(d['name']) / name
                reservations = c.execute(
                    'SELECT * FROM notices WHERE path=? COLLATE NOCASE ORDER BY id DESC', (str(path),)
                ).fetchall()
                if reservations:
                    if all(old['business_key'] == n.key and old['siren'] == siren for old in reservations):
                        previous_hash = next((old['sha256'] for old in reservations if old['sha256']), '')
                        break
                    continue
                if path.exists():
                    continue
                break
            else:
                raise ValueError('Trop de fichiers portant ce nom dans le dossier de sortie.')
            row_id = c.execute(
                """INSERT INTO notices(campaign,siren,business_key,year,reference,nic,department,commune,role,kind,due_date,path,sha256)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    campaign,
                    siren,
                    n.key,
                    n.year,
                    n.reference,
                    n.nic,
                    n.department,
                    n.commune,
                    n.role,
                    kind,
                    n.due_date,
                    str(path),
                    previous_hash,
                ),
            ).lastrowid
            return dict(c.execute('SELECT * FROM notices WHERE id=?', (row_id,)).fetchone())

    def notices(self, campaign: int, siren: str | None = None):
        with self.connection() as c:
            sql = 'SELECT * FROM notices WHERE campaign=?'
            args = [campaign]
            if siren is not None:
                sql += ' AND siren=?'
                args.append(siren)
            return [dict(r) for r in c.execute(sql + ' ORDER BY id', args)]

    def notice_result(self, notice_id: int, status: str, error: str = '', sha256: str = ''):
        with self.connection() as c:
            c.execute('UPDATE notices SET status=?,error=?,sha256=? WHERE id=?', (status, error, sha256, notice_id))

    def notice_extraction_result(self, notice_id: int, result: NoticeExtraction):
        with self.connection() as c:
            c.execute(
                """UPDATE notices SET tax_amount_cents=?,amount_due_cents=?,payment_method=?,
                bank_account_display=?,bank_account_masked=?,extraction_status=?,refund_cents=? WHERE id=?""",
                (
                    result.tax_amount_cents,
                    result.amount_due_cents,
                    result.payment_method,
                    result.bank_account_display or '',
                    int(result.bank_account_masked),
                    result.review_status,
                    result.refund_cents,
                    notice_id,
                ),
            )

    def save_breakdown(self, siren: str, n: Notice, details: CfeDetails, source: str):
        with self.connection() as c:
            c.execute(
                """INSERT INTO cfe_breakdowns(siren,year,nic,reference,kind,details,source,read_at) VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(siren,year,reference) DO UPDATE SET nic=excluded.nic,kind=excluded.kind,details=excluded.details,
                source=excluded.source,read_at=excluded.read_at""",
                (
                    siren,
                    n.year,
                    n.nic,
                    n.reference,
                    n.kind,
                    json.dumps(details.to_dict(), ensure_ascii=False),
                    source,
                    datetime.now().isoformat(timespec='seconds'),
                ),
            )

    def breakdown(self, siren: str, year: int, nic: str, reference: str | None = None) -> CfeDetails | None:
        """Details of a notice; without a reference, the single main (« SOLDE ») notice of that establishment and year."""
        with self.connection() as c:
            if reference is not None:
                rows = c.execute(
                    'SELECT details FROM cfe_breakdowns WHERE siren=? AND year=? AND reference=?',
                    (siren, year, reference),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT details FROM cfe_breakdowns WHERE siren=? AND year=? AND nic=? AND kind='SOLDE'",
                    (siren, year, nic),
                ).fetchall()
        return CfeDetails.from_dict(json.loads(rows[0]['details'])) if len(rows) == 1 else None

    def record_timing(
        self,
        batch: int,
        campaign: int,
        siren: str,
        session: int | None,
        phase: str,
        status: str,
        seconds: float,
        *,
        notice: Notice | None = None,
        information: str = '',
    ):
        duration = float(seconds)
        if not math.isfinite(duration) or duration < 0:
            raise ValueError('Durée de collecte invalide.')
        with self.connection() as c:
            c.execute(
                """INSERT INTO collection_timings
                (batch,campaign,siren,session,phase,notice_key,kind,reference,status,seconds,recorded,information)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    batch,
                    campaign,
                    siren,
                    session,
                    str(phase),
                    notice.key if notice else '',
                    notice.kind if notice else '',
                    notice.reference if notice else '',
                    str(status),
                    duration,
                    datetime.now().isoformat(timespec='milliseconds'),
                    str(information or '')[:1000],
                ),
            )

    def timings(self, batch: int):
        with self.connection() as c:
            return [
                dict(row)
                for row in c.execute(
                    """
                SELECT t.*, d.name AS dossier, d.collaborator, c.year, b.name AS batch_name
                FROM collection_timings t
                JOIN campaigns c ON c.id=t.campaign
                JOIN dossiers d ON d.campaign=t.campaign AND d.siren=t.siren
                JOIN batches b ON b.id=t.batch
                WHERE t.batch=? ORDER BY t.id""",
                    (batch,),
                )
            ]

    def register_proof(self, campaign: int, siren: str, kind: str = 'ABSENCE') -> Path:
        """Path of the dossier's proof (« ABSENCE » or « SANS_HABILITATION ») in the client's folder."""
        with self.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            d = c.execute('SELECT * FROM dossiers WHERE campaign=? AND siren=?', (campaign, siren)).fetchone()
            info = c.execute('SELECT * FROM campaigns WHERE id=?', (campaign,)).fetchone()
            if d['proof_path'] and f'_CFE_{info["year"]}_{kind}' in Path(d['proof_path']).name:
                return Path(d['proof_path'])
            for number in range(1, 10001):
                suffix = '' if number == 1 else f'_{number:02}'
                name = f'{safe_name(d["name"])}_CFE_{info["year"]}_{kind}{suffix}.pdf'
                path = Path(info['output']) / safe_name(d['collaborator']) / safe_name(d['name']) / name
                reserved = c.execute(
                    'SELECT 1 FROM dossiers WHERE proof_path=? COLLATE NOCASE', (str(path),)
                ).fetchone()
                if not reserved and not path.exists():
                    break
            else:
                raise ValueError('Trop de preuves portant ce nom dans le dossier de sortie.')
            # A proof of the other kind stays in the folder; the dossier now points to the new one.
            c.execute(
                "UPDATE dossiers SET proof_path=?,proof_sha256='' WHERE campaign=? AND siren=?",
                (str(path), campaign, siren),
            )
            return path

    def proof_result(self, campaign: int, siren: str, sha256: str):
        with self.connection() as c:
            c.execute('UPDATE dossiers SET proof_sha256=? WHERE campaign=? AND siren=?', (sha256, campaign, siren))
