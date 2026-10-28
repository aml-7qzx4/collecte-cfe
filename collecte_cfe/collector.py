import tempfile
import time
from datetime import datetime
from pathlib import Path

from .cfe_details import read_details
from .documents import (
    classify_pdf,
    ensure_breakdown,
    ensure_notice_details,
    export_actions,
    proof_from_portal_capture,
    proof_pdf,
    recover_pdf,
    recover_proof,
    verify_original_notice,
)
from .domain import Dossier, safe_name
from .integrity import matches_sha256
from .portal import AuthorizationError, PortalError, SessionExpired


class Collector:
    def __init__(self, store, portal):
        self.store = store
        self.portal = portal

    def run(
        self,
        campaign,
        stop,
        progress,
        *,
        dossiers=None,
        finalize=True,
        verify_source=False,
        session_number=None,
        capture_dir=None,
    ):
        info = self.store.campaign(campaign)
        if finalize:
            self.store.campaign_status(campaign, 'EN_COURS')
        dossiers = self.store.dossiers(campaign) if dossiers is None else dossiers
        try:
            for position, row in enumerate(dossiers, 1):
                if stop.is_set():
                    if finalize:
                        self.store.campaign_status(campaign, 'PAUSE')
                    return 'PAUSE'
                if row['status'] == 'ABSENT' and self.proof_completed(row):
                    continue
                known = self.store.notices(campaign, row['siren'])
                if row['status'] == 'TERMINE' and known and all(self.completed(n) for n in known):
                    continue
                d = Dossier(row['name'], row['siren'], row['collaborator'])
                progress(f'Dossier {position} / {len(dossiers)} · {d.name}')
                self.store.dossier_result(campaign, d.siren, 'EN_COURS')
                search_started = time.perf_counter()
                try:
                    notices = self.portal.open_dossier(d.siren, info['year'])
                    if any(n.year != info['year'] for n in notices):
                        raise PortalError('Avis d’une autre année détecté : collecte du dossier bloquée.')
                    if not notices:
                        if known:
                            raise PortalError(
                                'Des avis connus ne sont plus visibles : aucune preuve d’absence produite.'
                            )
                        target = self.store.register_proof(campaign, d.siren)
                        # On a crash between the file and DB writes, absence must still be rechecked
                        # before the existing local proof can be accepted.
                        try:
                            digest = recover_proof(target, d, info['year'])
                            if digest and row['proof_sha256'] and digest != row['proof_sha256']:
                                raise ValueError('Preuve d’absence modifiée.')
                        except ValueError:
                            self.quarantine(target)
                            digest = None
                        if digest is None:
                            if hasattr(self.portal, 'capture_cfe_screen'):
                                # The proof is always the portal page itself: without it, the dossier is retried rather than filed.
                                digest = self.capture_absence(target, d, info['year'])
                                if digest is None:
                                    raise PortalError(
                                        'Page « Avis CFE » du portail non capturée : relancez ce dossier.'
                                    )
                            else:
                                digest = proof_pdf(target, d, info['year'])
                        self.store.proof_result(campaign, d.siren, digest)
                        self.store.dossier_result(
                            campaign, d.siren, 'ABSENT', 'Aucun avis disponible pour l’année sélectionnée.'
                        )
                        self.record_timing(
                            info,
                            campaign,
                            d.siren,
                            session_number,
                            'RECHERCHE',
                            'AUCUN_AVIS',
                            search_started,
                            information='Aucun avis disponible pour l’année sélectionnée.',
                        )
                        continue
                    self.record_timing(
                        info,
                        campaign,
                        d.siren,
                        session_number,
                        'RECHERCHE',
                        'AVIS_TROUVES',
                        search_started,
                        information=f'{len(notices)} avis détecté(s).',
                    )
                    notice_rows = [(n, self.store.register_notice(campaign, d.siren, n)) for n in notices]
                    visible = {n.key for n in notices}
                    for old in known:
                        if old['business_key'] not in visible and not self.completed(old):
                            self.store.notice_result(
                                old['id'], 'ERREUR', 'Avis connu non visible lors de la reprise. Vérifiez le portail.'
                            )
                    pending = [(n, r) for n, r in notice_rows if not self.completed(r)]
                    with tempfile.TemporaryDirectory(prefix='Collecte-CFE-') as temporary:
                        for index, (n, r) in enumerate(pending, 1):
                            if stop.is_set():
                                self.store.dossier_result(campaign, d.siren, 'EN_ATTENTE', 'Collecte mise en pause.')
                                if finalize:
                                    self.store.campaign_status(campaign, 'PAUSE')
                                return 'PAUSE'
                            progress(f'{d.name} · Téléchargement {index} / {len(pending)} · {n.kind}')
                            notice_started = time.perf_counter()
                            try:
                                target = Path(r['path'])
                                note = ''
                                if r['sha256'] and target.is_file() and not matches_sha256(target, r['sha256']):
                                    self.quarantine(target)
                                    note = 'PDF précédent modifié ou incomplet, conservé dans _A_verifier ; avis téléchargé à nouveau.'
                                    progress(f'{d.name} · {note}')
                                try:
                                    recovered = recover_pdf(target, d, n)
                                except ValueError:
                                    self.quarantine(target)
                                    recovered = None
                                    note = 'PDF précédent non conforme, conservé dans _A_verifier ; avis téléchargé à nouveau.'
                                if recovered:
                                    self.store.notice_result(r['id'], 'TERMINE', '', recovered)
                                    ensure_notice_details(self.store, {**r, 'status': 'TERMINE'})
                                    self.record_timing(
                                        info,
                                        campaign,
                                        d.siren,
                                        session_number,
                                        'AVIS',
                                        'DEJA_CLASSE',
                                        notice_started,
                                        notice=n,
                                    )
                                    continue
                                source = Path(temporary) / f'{r["id"]}.pdf'
                                self.portal.download(n, source)
                                if verify_source:
                                    verify_original_notice(source, d, n)
                                digest = classify_pdf(source, target, d, n)
                                self.store.notice_result(r['id'], 'TERMINE', note, digest)
                                ensure_notice_details(self.store, {**r, 'status': 'TERMINE'}, force=True)
                                self.record_timing(
                                    info,
                                    campaign,
                                    d.siren,
                                    session_number,
                                    'AVIS',
                                    'TERMINE',
                                    notice_started,
                                    notice=n,
                                    information=note,
                                )
                            except SessionExpired:
                                self.record_timing(
                                    info,
                                    campaign,
                                    d.siren,
                                    session_number,
                                    'AVIS',
                                    'CONNEXION_REQUISE',
                                    notice_started,
                                    notice=n,
                                )
                                raise
                            except (PortalError, ValueError) as e:
                                self.store.notice_result(r['id'], 'ERREUR', str(e))
                                self.record_timing(
                                    info,
                                    campaign,
                                    d.siren,
                                    session_number,
                                    'AVIS',
                                    'ERREUR',
                                    notice_started,
                                    notice=n,
                                    information=str(e),
                                )
                            except Exception:
                                message = 'Écriture ou classement impossible. Vérifiez le dossier de sortie puis relancez cet avis.'
                                self.store.notice_result(r['id'], 'ERREUR', message)
                                self.record_timing(
                                    info,
                                    campaign,
                                    d.siren,
                                    session_number,
                                    'AVIS',
                                    'ERREUR',
                                    notice_started,
                                    notice=n,
                                    information=message,
                                )
                    self.read_breakdowns(campaign, d, notices, progress)
                    all_rows = self.store.notices(campaign, d.siren)
                    has_error = any(n['status'] != 'TERMINE' for n in all_rows)
                    self.store.dossier_result(
                        campaign,
                        d.siren,
                        'ERREUR' if has_error else 'TERMINE',
                        'Un ou plusieurs avis doivent être relancés.' if has_error else '',
                    )
                except AuthorizationError:
                    self.record_timing(
                        info, campaign, d.siren, session_number, 'RECHERCHE', 'SANS_HABILITATION', search_started
                    )
                    self.store.dossier_result(campaign, d.siren, 'EN_ATTENTE', 'Recherche d’un autre compte habilité.')
                    raise
                except SessionExpired:
                    self.record_timing(
                        info, campaign, d.siren, session_number, 'RECHERCHE', 'CONNEXION_REQUISE', search_started
                    )
                    self.store.dossier_result(campaign, d.siren, 'EN_ATTENTE', 'Connexion manuelle requise.')
                    if finalize:
                        self.store.campaign_status(campaign, 'CONNEXION_REQUISE')
                    progress('Session expirée : reconnectez-vous manuellement, puis reprenez la campagne.')
                    return 'CONNEXION_REQUISE'
                except (PortalError, ValueError) as e:
                    self.record_timing(
                        info,
                        campaign,
                        d.siren,
                        session_number,
                        'RECHERCHE',
                        'ERREUR',
                        search_started,
                        information=str(e),
                    )
                    self.store.dossier_result(campaign, d.siren, 'ERREUR', str(e))
                except Exception:
                    self.record_timing(
                        info,
                        campaign,
                        d.siren,
                        session_number,
                        'RECHERCHE',
                        'ERREUR',
                        search_started,
                        information='Collecte du dossier interrompue.',
                    )
                    self.store.dossier_result(
                        campaign,
                        d.siren,
                        'ERREUR',
                        'Collecte du dossier interrompue. Vérifiez le portail et le dossier de sortie.',
                    )
                finally:
                    if capture_dir is not None and hasattr(self.portal, 'capture_cfe_screen'):
                        capture = Path(capture_dir) / f'{safe_name(d.name)}_{d.siren}_CFE_{info["year"]}.png'
                        try:
                            if self.portal.capture_cfe_screen(capture):
                                progress(f'{d.name} · Capture CFE enregistrée : {capture}')
                            else:
                                progress(f'{d.name} · Capture indisponible : aucune page CFE confirmée pour ce SIREN.')
                        except Exception:
                            progress(f'{d.name} · Capture impossible ; le résultat du dossier reste enregistré.')
                    try:
                        self.portal.close_dossier()
                    except Exception:
                        pass
            status = (
                'AVEC_ERREURS' if any(d['status'] == 'ERREUR' for d in self.store.dossiers(campaign)) else 'TERMINE'
            )
            if finalize:
                self.store.campaign_status(campaign, status)
                progress(
                    'Campagne terminée avec des avis à relancer.' if status == 'AVEC_ERREURS' else 'Campagne terminée.'
                )
            return 'OK'
        finally:
            if finalize:
                try:
                    export_actions(self.store, campaign)
                except ValueError as error:
                    progress(f'Les résultats sont enregistrés. Export Excel impossible : {error}')
                except Exception:
                    progress(
                        'Les résultats sont enregistrés. Export Excel impossible : vérifiez la sortie et fermez les classeurs ouverts.'
                    )

    def capture_absence(self, target, d, year, attempts=2):
        """Capture the confirmed « Avis CFE » page of the dossier into its proof; None when no attempt succeeds."""
        for _attempt in range(attempts):
            try:
                with tempfile.TemporaryDirectory(prefix='Collecte-CFE-absence-') as temporary:
                    capture = Path(temporary) / 'page-cfe.png'
                    if self.portal.capture_cfe_screen(capture):
                        return proof_from_portal_capture(target, d, year, capture)
            except Exception:
                pass
        return None

    def read_breakdowns(self, campaign, d, notices, progress):
        """Read the calculation details of this year's main notices and of last year's, to explain the change."""
        rows = {row['business_key']: row for row in self.store.notices(campaign, d.siren)}
        with tempfile.TemporaryDirectory(prefix='Collecte-CFE-N-1-') as temporary:
            for n in notices:
                row = rows.get(n.key)
                if row is None or row['status'] != 'TERMINE' or row['kind'] != 'SOLDE':
                    continue
                ensure_breakdown(self.store, row)
                if (
                    not hasattr(self.portal, 'previous_notice')
                    or self.store.breakdown(d.siren, n.year - 1, n.nic) is not None
                ):
                    continue
                # Explaining the change is a bonus: it never fails the collection of the dossier.
                try:
                    previous = self.portal.previous_notice(n)
                except Exception:
                    previous = None
                if previous is None:
                    continue
                # Last year's notice is only read: nothing is added to the client's folder.
                source = Path(temporary) / f'{previous.key[:16]}.pdf'
                try:
                    self.portal.download(previous, source)
                    verify_original_notice(source, d, previous)
                    details = read_details(source)
                    if details is None:
                        raise ValueError('Éléments de calcul introuvables.')
                    self.store.save_breakdown(d.siren, previous, details, 'ANNEE_PRECEDENTE')
                except SessionExpired:
                    raise
                except Exception:
                    progress(
                        f'{d.name} · Avis {previous.year} non lu : l’écart avec l’an dernier ne sera pas détaillé.'
                    )

    @staticmethod
    def completed(row) -> bool:
        if row['status'] != 'TERMINE' or not row['sha256']:
            return False
        return matches_sha256(row['path'], row['sha256'])

    def record_timing(
        self, info, campaign, siren, session_number, phase, status, started, *, notice=None, information=''
    ):
        try:
            self.store.record_timing(
                info['batch'],
                campaign,
                siren,
                session_number,
                phase,
                status,
                time.perf_counter() - started,
                notice=notice,
                information=information,
            )
        except Exception:
            # Une mesure ne doit jamais interrompre la collecte fiscale elle-même.
            pass

    @staticmethod
    def proof_completed(row) -> bool:
        if not row['proof_path']:
            return False
        return matches_sha256(row['proof_path'], row['proof_sha256'])

    @staticmethod
    def quarantine(target: Path):
        directory = target.parent / '_A_verifier'
        directory.mkdir(exist_ok=True)
        destination = directory / f'{target.stem}_{datetime.now():%Y%m%d_%H%M%S_%f}.pdf'
        target.rename(destination)
