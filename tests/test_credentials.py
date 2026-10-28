import pytest

from collecte_cfe.credentials import AccountManager
from collecte_cfe.store import Store


class MemoryVault:
    def __init__(self):
        self.secrets = {}

    def set_password(self, service, key, password):
        self.secrets[(service, key)] = password

    def get_password(self, service, key):
        return self.secrets.get((service, key))

    def delete_password(self, service, key):
        self.secrets.pop((service, key), None)


def test_accounts_keep_password_out_of_sqlite_and_allow_same_account_in_multiple_sessions(tmp_path):
    path = tmp_path / 'collecte-cfe.sqlite'
    manager = AccountManager(Store(path), MemoryVault())
    account = manager.add('Cabinet principal', 'expert@example.fr', 'Secret local 2026')
    manager.assign(0, account)
    manager.assign(1, account)
    assert manager.account_for_session(0)['id'] == account
    assert manager.account_for_session(1)['id'] == account
    assert manager.password(account) == 'Secret local 2026'
    assert b'Secret local 2026' not in path.read_bytes()
    assert manager.list_accounts() == [{'id': account, 'label': 'Cabinet principal', 'login': 'expert@example.fr'}]


def test_account_removal_clears_assignments_and_vault(tmp_path):
    vault = MemoryVault()
    manager = AccountManager(Store(tmp_path / 'collecte-cfe.sqlite'), vault)
    account = manager.add('Cabinet', 'expert@example.fr', 'secret')
    manager.assign(2, account)
    manager.remove(account)
    assert manager.account_for_session(2) is None
    assert manager.password(account) is None
    assert not vault.secrets


def test_invalid_accounts_and_sessions_are_rejected_without_storing_secrets(tmp_path):
    vault = MemoryVault()
    manager = AccountManager(Store(tmp_path / 'collecte-cfe.sqlite'), vault)
    with pytest.raises(ValueError):
        manager.add('Cabinet', 'not-an-email', 'secret')
    with pytest.raises(ValueError):
        manager.add('Cabinet', 'expert@example.fr', '')
    assert manager.list_accounts() == [] and not vault.secrets
    account = manager.add('Cabinet', 'expert@example.fr', 'secret')
    with pytest.raises(ValueError):
        manager.assign(-1, account)
    manager.assign(6, account)
    assert manager.account_for_session(6)['id'] == account, 'as many sessions as expert accounts, beyond four'
    with pytest.raises(ValueError):
        manager.assign(0, account + 100)


def test_vault_failure_rolls_back_account_metadata(tmp_path):
    class BrokenVault(MemoryVault):
        def set_password(self, *args):
            raise RuntimeError('vault unavailable')

    manager = AccountManager(Store(tmp_path / 'collecte-cfe.sqlite'), BrokenVault())
    with pytest.raises(RuntimeError):
        manager.add('Cabinet', 'expert@example.fr', 'secret')
    assert manager.list_accounts() == []


def test_account_update_can_change_label_login_and_password(tmp_path):
    manager = AccountManager(Store(tmp_path / 'collecte-cfe.sqlite'), MemoryVault())
    account = manager.add('Cabinet', 'expert@example.fr', 'secret')
    manager.update(account, 'Autre cabinet', 'autre@example.fr', 'new secret')
    assert manager.list_accounts() == [{'id': account, 'label': 'Autre cabinet', 'login': 'autre@example.fr'}]
    assert manager.password(account) == 'new secret'


def test_different_local_databases_do_not_share_vault_secrets(tmp_path):
    vault = MemoryVault()
    first = AccountManager(Store(tmp_path / 'first.sqlite'), vault)
    second = AccountManager(Store(tmp_path / 'second.sqlite'), vault)
    first_account = first.add('Cabinet A', 'a@example.fr', 'secret A')
    second_account = second.add('Cabinet B', 'b@example.fr', 'secret B')
    assert first.password(first_account) == 'secret A'
    assert second.password(second_account) == 'secret B'


def test_refused_password_stays_blocked_until_credentials_change(tmp_path):
    path = tmp_path / 'collecte-cfe.sqlite'
    manager = AccountManager(Store(path), MemoryVault())
    account = manager.add('Cabinet', 'expert@example.fr', 'bad secret')
    manager.block_login(account)
    assert AccountManager(Store(path), manager.vault).login_blocked(account)
    manager.update(account, 'Nom corrigé', 'expert@example.fr')
    assert manager.login_blocked(account)
    manager.update(account, 'Nom corrigé', 'expert@example.fr', 'new secret')
    assert not manager.login_blocked(account)


def test_a_portable_copy_keeps_its_data_beside_the_program_only_with_its_marker(tmp_path):
    from collecte_cfe.paths import PORTABLE_MARKER, portable_data_dir

    assert portable_data_dir(tmp_path) is None
    (tmp_path / PORTABLE_MARKER).write_text('Données à côté du programme.', encoding='utf-8')
    assert portable_data_dir(tmp_path) == tmp_path / 'Donnees'
    assert portable_data_dir() is None, 'running from the source tree never switches to portable data'


def test_an_account_without_a_password_on_this_pc_asks_for_it(tmp_path, qapp):
    from collecte_cfe.app import MainWindow
    from tests.test_dashboard import MemorySettings

    store = Store(tmp_path / 'db.sqlite')
    store.create_batch([2025], tmp_path / 'out', [], 1)
    vault = MemoryVault()
    manager = AccountManager(store, vault)
    manager.add('Cabinet', 'expert@example.fr', 'secret')
    vault.secrets.clear()
    window = MainWindow(store, settings=MemorySettings(), accounts=manager)
    assert window.account_rows[0].state.text() == 'Mot de passe à saisir sur ce poste'
    window.close()


def test_passwords_filed_under_the_old_name_move_to_the_new_one(tmp_path):
    from collecte_cfe.credentials import LEGACY_SERVICE, _path_digest

    store = Store(tmp_path / 'collecte-cfe.sqlite')
    vault = MemoryVault()
    account = store.add_expert_account('Cabinet', 'expert@example.fr')
    old_location = tmp_path / 'Bubot' / 'bubot.sqlite'
    vault.set_password(f'{LEGACY_SERVICE} - {_path_digest(old_location)}', f'compte-{account}', 'ancien secret')
    manager = AccountManager(store, vault, legacy_paths=[old_location])
    assert manager.password(account) == 'ancien secret'
    assert vault.secrets == {(manager.service, f'compte-{account}'): 'ancien secret'}, 'moved once, not copied'
    assert manager.service.startswith('Collecte CFE - comptes experts - ')


def test_the_vault_name_stays_when_the_data_folder_moves(tmp_path):
    first = tmp_path / 'E' / 'Donnees'
    first.mkdir(parents=True)
    store = Store(first / 'collecte-cfe.sqlite')
    service = AccountManager(store, MemoryVault()).service
    moved = tmp_path / 'F' / 'Donnees'
    moved.parent.mkdir()
    first.rename(moved)
    assert AccountManager(Store(moved / 'collecte-cfe.sqlite'), MemoryVault()).service == service


def test_the_old_data_folder_and_database_move_to_their_new_names(tmp_path, monkeypatch):
    from collecte_cfe import paths

    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    legacy = tmp_path / 'Bubot'
    legacy.mkdir()
    (legacy / 'bubot.sqlite').write_bytes(b'base')
    (legacy / 'journal-activite.txt').write_text('journal', encoding='utf-8')
    directory = paths.prepare_data_dir()
    assert directory == tmp_path / 'Collecte CFE' and not legacy.exists()
    assert (directory / 'collecte-cfe.sqlite').read_bytes() == b'base' and (
        directory / 'journal-activite.txt'
    ).is_file()
    assert paths.prepare_data_dir() == directory, 'a second launch leaves everything in place'


def test_notices_marked_before_the_rename_are_still_recognised(tmp_path):
    from pypdf import PdfWriter

    from collecte_cfe.documents import recover_pdf
    from collecte_cfe.domain import Dossier, Notice

    notice = Notice(2025, 'R1', '00001', '75', 'Paris', 'Rôle général', '15/12/2025')
    dossier = Dossier('Alpha', '000000001', 'Léa')
    writer = PdfWriter()
    writer.add_blank_page(200, 200)
    writer.add_metadata({'/BubotNoticeKey': notice.key, '/BubotSiren': dossier.siren})
    target = tmp_path / 'avis.pdf'
    with target.open('wb') as stream:
        writer.write(stream)
    assert recover_pdf(target, dossier, notice)


def test_a_database_made_for_four_sessions_takes_more(tmp_path):
    import sqlite3

    path = tmp_path / 'db.sqlite'
    with sqlite3.connect(path) as c:
        c.execute(
            'CREATE TABLE expert_accounts (id INTEGER PRIMARY KEY, label TEXT NOT NULL, login TEXT NOT NULL UNIQUE)'
        )
        c.execute("""CREATE TABLE session_accounts (session INTEGER PRIMARY KEY CHECK(session BETWEEN 0 AND 3),
            account INTEGER NOT NULL REFERENCES expert_accounts(id) ON DELETE CASCADE)""")
        c.execute("INSERT INTO expert_accounts VALUES (1,'Cabinet','a@example.fr')")
        c.execute('INSERT INTO session_accounts VALUES (2,1)')
    store = Store(path)
    assert store.assigned_expert_account(2)['label'] == 'Cabinet', 'the sessions already chosen are kept'
    store.assign_expert_account(7, 1)
    assert store.assigned_expert_account(7)['id'] == 1
