import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass


def safe_name(value: str) -> str:
    value = unicodedata.normalize('NFC', str(value)).strip()
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value).strip(' .')
    if not value:
        value = 'Sans_nom'
    if value.split('.')[0].upper() in {
        'CON',
        'PRN',
        'AUX',
        'NUL',
        *(f'COM{i}' for i in range(1, 10)),
        *(f'LPT{i}' for i in range(1, 10)),
    }:
        value = '_' + value
    # Bounded paths also remain usable on legacy Windows configurations.
    return value[:65].rstrip(' .')


@dataclass(frozen=True)
class Dossier:
    name: str
    siren: str
    collaborator: str
    email: str = ''


@dataclass(frozen=True)
class Notice:
    year: int
    reference: str
    nic: str
    department: str
    commune: str
    role: str
    due_date: str

    @property
    def kind(self) -> str:
        role = ''.join(c for c in unicodedata.normalize('NFD', self.role.lower()) if not unicodedata.combining(c))
        if 'acompte' in role:
            return 'ACOMPTE'
        if 'supplementaire' in role:
            return 'SUPPLEMENTAIRE'
        if 'general' in role or 'solde' in role:
            return 'SOLDE'
        raise ValueError('Type de rôle non reconnu : contrôle manuel nécessaire.')

    @property
    def key(self) -> str:
        # No URL, JSF view state or token is retained in this business identity.
        data = [self.year, self.reference, self.nic, self.department, self.commune, self.role]
        return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()


def summarize_dossiers(rows):
    """Chaque dossier/année contribue exactement à un seul état."""
    result = {'total': 0, 'finished': 0, 'absent': 0, 'no_access': 0, 'retry': 0, 'pending': 0}
    keys = {'TERMINE': 'finished', 'ABSENT': 'absent', 'SANS_HABILITATION': 'no_access', 'ERREUR': 'retry'}
    for row in rows:
        result['total'] += 1
        result[keys.get(row['status'], 'pending')] += 1
    return result
