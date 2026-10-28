# Collecte CFE

Application Windows pour les cabinets d’expertise comptable : elle récupère les avis de CFE des dossiers clients sur l’espace professionnel impots.gouv.fr, les range par collaborateur et par dossier, et tient un classeur Excel de suivi.

Tout se passe sur le poste. Les avis, la base de suivi et les mots de passe ne quittent jamais l’ordinateur, et l’application ne communique qu’avec impots.gouv.fr.

> Collecte CFE n’est ni éditée ni approuvée par la DGFiP. Le site impots.gouv.fr peut changer à tout moment et demander une adaptation de l’application. Vérifiez les avis récupérés avant de vous en servir.

## Ce que fait l’application

1. **Collecte.** Vous importez le portefeuille (CSV ou Excel : `Nom du dossier`, `SIREN`, `Collaborateur`, et en option l’e-mail du client), puis vous choisissez une ou plusieurs années. L’application se connecte avec les comptes experts du cabinet et cherche chaque SIREN.
2. **Dossiers.** Chaque dossier reçoit une situation : à payer, prélevé, rien à payer, sans avis, à relancer, à vérifier manuellement. Après deux échecs, un dossier passe en vérification manuelle et peut être coché « Vérifié ». Les avis sont classés dans le dossier de sortie choisi :

   ```text
   Collaborateur/
     Dossier/
       Dossier_CFE_2025_SOLDE.pdf
   ```

   Quand aucun avis n’existe, une capture de la page consultée sert de constat. Ce n’est pas une attestation officielle.
3. **Courriels.** L’application prépare un brouillon par client avec ses avis en pièces jointes. Vous le relisez et l’envoyez vous-même : rien n’est envoyé automatiquement.

Une campagne terminée peut être archivée dans un classeur Excel.

## Comptes impots.gouv.fr et connexion

Les comptes experts s’ajoutent dans **Réglages**. Le mot de passe est rangé dans le coffre de Windows (Gestionnaire d’identification). La base de l’application garde seulement le nom du compte et son adresse de connexion.

L’application ouvre quatre fenêtres de navigation en parallèle, ou une par compte expert au-delà de quatre, et répartit les comptes entre elles à tour de rôle. Elle remplit l’adresse et le mot de passe, puis lit le CAPTCHA sur le poste avec [ddddocr](https://github.com/sml2h3/ddddocr). L’image reste en mémoire et n’est envoyée nulle part. Si le CAPTCHA reste illisible après quatre nouvelles images, l’onglet Collecte demande au collaborateur de le saisir et ouvre la fenêtre en un clic. Un compte refusé par impots.gouv.fr n’est plus utilisé tant qu’il n’a pas été corrigé.

Microsoft Edge est utilisé s’il est installé, sinon Chromium.

## Installation

### Avec l’exécutable

Décompressez `Collecte-CFE.zip` et lancez `Collecte CFE.exe`. Les données sont rangées dans `%LOCALAPPDATA%\Collecte CFE`.

La version portable (`Collecte-CFE-portable.zip`) se copie sur une clé USB et garde ses données dans le dossier `Donnees` à côté du programme. Ce comportement vient du fichier `portable.txt` : supprimez-le pour ranger les données sur le poste.

### Depuis le code source

Il faut Windows et Python 3.11 ou plus récent.

```powershell
./installer.ps1
```

Puis double-cliquez sur `Collecte CFE.cmd`.

Pour construire l’exécutable et les deux paquets (poste et portable) :

```powershell
./construire.ps1
```

Le script lance les tests avant la construction.

## Confidentialité

- Les avis, la base SQLite et les classeurs restent sur le poste, dans les dossiers que vous choisissez. Si vous choisissez un dossier synchronisé (OneDrive, partage réseau), c’est la règle de ce dossier qui s’applique.
- Les mots de passe sont dans le coffre de Windows, jamais dans la base ni dans les journaux.
- Les fenêtres de navigation ne gardent ni cookies ni session après fermeture.
- Aucune statistique d’usage n’est envoyée. Le journal d’activité reste local ; vous pouvez l’exporter vous-même pour une demande d’assistance, sans identifiants ni mots de passe.

## Tests

```powershell
./.venv/Scripts/python.exe -m pip install -e ".[dev]"
./.venv/Scripts/python.exe -m pytest -q
```

Les tests utilisent uniquement des données fictives. Le portail impots.gouv.fr y est simulé : ils ne s’y connectent jamais.

## Licence

Collecte CFE est distribué sous [licence publique de l’Union européenne (EUPL) v1.2](LICENSE). Vous pouvez l’utiliser, l’étudier, le modifier et le redistribuer. Si vous distribuez une version modifiée, son code source doit rester disponible sous la même licence ou une licence compatible.
