"""Local expert-account metadata and Windows credential vault access."""

import hashlib
import re
from pathlib import Path

SERVICE = 'Collecte CFE - comptes experts'
LEGACY_SERVICE = 'Bubot CFE - comptes experts'


def _path_digest(path):
    return hashlib.sha256(str(Path(path).resolve()).casefold().encode('utf-8')).hexdigest()[:20]


class WindowsVault:
    def __init__(self):
        from keyring.backends.Windows import WinVaultKeyring

        self.backend = WinVaultKeyring()

    def set_password(self, service, key, password):
        self.backend.set_password(service, key, password)

    def get_password(self, service, key):
        return self.backend.get_password(service, key)

    def delete_password(self, service, key):
        self.backend.delete_password(service, key)


class AccountManager:
    def __init__(self, store, vault=None, legacy_paths=()):
        self.store = store
        self.vault = vault if vault is not None else WindowsVault()
        self.service = f'{SERVICE} - {store.vault_id()}'
        # Before the rename, passwords were filed under the old name and the database's location.
        self.legacy_services = list(
            dict.fromkeys(f'{LEGACY_SERVICE} - {_path_digest(path)}' for path in (store.path, *legacy_paths))
        )

    @staticmethod
    def _valid(label, login, password=None):
        label = ' '.join(str(label or '').split())[:100]
        login = str(login or '').strip().lower()
        if not label or not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', login):
            raise ValueError('Indiquez un nom et une adresse électronique de connexion valides.')
        if password is not None and not password:
            raise ValueError('Indiquez le mot de passe du compte expert.')
        return label, login

    @staticmethod
    def _key(account):
        return f'compte-{int(account)}'

    def list_accounts(self):
        return self.store.expert_accounts()

    def add(self, label, login, password):
        label, login = self._valid(label, login, password)
        account = self.store.add_expert_account(label, login)
        try:
            self.vault.set_password(self.service, self._key(account), password)
        except Exception:
            self.store.delete_expert_account(account)
            raise
        return account

    def update(self, account, label, login, password=None):
        label, login = self._valid(label, login, password)
        previous_login = next((item['login'] for item in self.list_accounts() if item['id'] == account), None)
        previous = self.password(account) if password is not None else None
        if password is not None:
            self.vault.set_password(self.service, self._key(account), password)
        try:
            self.store.update_expert_account(
                account, label, login, clear_block=password is not None or login != previous_login
            )
        except Exception:
            if password is not None:
                if previous is None:
                    self.vault.delete_password(self.service, self._key(account))
                else:
                    self.vault.set_password(self.service, self._key(account), previous)
            raise

    def remove(self, account):
        previous = self.password(account)
        if previous is not None:
            self.vault.delete_password(self.service, self._key(account))
        try:
            self.store.delete_expert_account(account)
        except Exception:
            if previous is not None:
                self.vault.set_password(self.service, self._key(account), previous)
            raise

    def password(self, account):
        password = self.vault.get_password(self.service, self._key(account))
        if password:
            return password
        for legacy in self.legacy_services:
            password = self.vault.get_password(legacy, self._key(account))
            if password:
                self.vault.set_password(self.service, self._key(account), password)
                try:
                    self.vault.delete_password(legacy, self._key(account))
                except Exception:
                    pass
                return password
        return None

    def block_login(self, account):
        self.store.block_account_login(account)

    def login_blocked(self, account):
        return self.store.account_login_blocked(account)

    def assign(self, session, account):
        self.store.assign_expert_account(session, account)

    def account_for_session(self, session):
        return self.store.assigned_expert_account(session)
