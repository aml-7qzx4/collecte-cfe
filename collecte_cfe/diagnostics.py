"""Journal opérationnel local, sans champs libres ni secrets de connexion."""

from collections import deque
from datetime import datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

EVENTS = {
    'application_started': 'Application ouverte',
    'account_added': 'Compte expert ajouté',
    'account_updated': 'Compte expert modifié',
    'account_removed': 'Compte expert supprimé',
    'session_opening': 'Ouverture de la session',
    'session_connecting': 'Connexion en cours',
    'session_reconnecting': 'Reconnexion en cours',
    'session_reconnect_needed': 'Session à reconnecter',
    'session_connected': 'Session connectée',
    'session_refused': 'Connexion refusée',
    'session_captcha': 'CAPTCHA à vérifier',
    'captcha_refreshed': 'CAPTCHA régénéré',
    'session_manual': 'Connexion à vérifier',
    'session_closed': 'Session fermée',
    'campaign_started': 'Collecte lancée',
    'campaign_finished': 'Collecte terminée ou interrompue',
    'notice_manual': 'Avis classé à la main',
    'dossier_pointed': 'Dossier pointé',
    'dossier_unpointed': 'Pointage retiré',
    'dossier_verified': 'Dossier vérifié manuellement',
    'dossier_unverified': 'Vérification manuelle retirée',
    'campaign_archived': 'Campagne archivée',
    'campaign_unarchived': 'Campagne sortie des archives',
    'worker_error': 'Erreur du moteur de navigation',
}


class DiagnosticLog:
    def __init__(self, path):
        self.path = Path(path)

    def append(self, event, session=None, batch=None, refresh=None):
        if event not in EVENTS:
            raise ValueError('Code de journal non reconnu.')
        if session is not None and (not isinstance(session, int) or session < 0):
            raise ValueError('Session invalide.')
        if batch is not None and (not isinstance(batch, int) or batch < 1):
            raise ValueError('Campagne invalide.')
        if refresh is not None and (
            event != 'captcha_refreshed' or not isinstance(refresh, int) or not 1 <= refresh <= 4
        ):
            raise ValueError('Compteur CAPTCHA invalide.')
        fields = [datetime.now().astimezone().isoformat(timespec='seconds'), EVENTS[event]]
        if session is not None:
            fields.append(f'Session {session + 1}')
        if batch is not None:
            fields.append(f'Campagne {batch}')
        if refresh is not None:
            fields.append(f'Essai {refresh}')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open('a', encoding='utf-8') as output:
            output.write(' | '.join(fields) + '\n')

    def recent(self, limit=500):
        if not self.path.exists():
            return ''
        with self.path.open('r', encoding='utf-8') as source:
            return ''.join(deque(source, maxlen=limit))

    def export(self, destination, worker_stack=None):
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(destination, 'w', ZIP_DEFLATED) as bundle:
            bundle.writestr('journal.txt', self.path.read_text(encoding='utf-8') if self.path.exists() else '')
            if worker_stack and Path(worker_stack).is_file():
                bundle.write(worker_stack, 'moteur-erreurs.txt')
