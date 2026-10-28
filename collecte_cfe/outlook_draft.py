"""Ouverture d'un mail prêt à envoyer dans Outlook, avis en pièces jointes, sans jamais l'envoyer."""

import base64
import ctypes
import html
import json
import queue
import subprocess
import sys
import threading
from ctypes import POINTER, Structure, c_ulong, c_void_p, c_wchar_p
from pathlib import Path

MAPI_TO = 1
MAPI_LOGON_UI = 0x1
MAPI_DIALOG_MODELESS = 0x4
MAPI_DIALOG = 0x8
MAPI_ERRORS = {
    1: 'Le mail a été fermé sans être envoyé.',
    2: 'La messagerie a refusé le mail.',
    3: 'La messagerie demande une connexion.',
    11: 'Une pièce jointe est introuvable.',
    15: 'La messagerie n’accepte pas l’adresse du client.',
}


class DraftUnavailable(Exception):
    """The mail application could not open the message with its attachments."""


class _Recipient(Structure):
    _fields_ = [
        ('ulReserved', c_ulong),
        ('ulRecipClass', c_ulong),
        ('lpszName', c_wchar_p),
        ('lpszAddress', c_wchar_p),
        ('ulEIDSize', c_ulong),
        ('lpEntryID', c_void_p),
    ]


class _File(Structure):
    _fields_ = [
        ('ulReserved', c_ulong),
        ('flFlags', c_ulong),
        ('nPosition', c_ulong),
        ('lpszPathName', c_wchar_p),
        ('lpszFileName', c_wchar_p),
        ('lpFileType', c_void_p),
    ]


class _Message(Structure):
    _fields_ = [
        ('ulReserved', c_ulong),
        ('lpszSubject', c_wchar_p),
        ('lpszNoteText', c_wchar_p),
        ('lpszMessageType', c_wchar_p),
        ('lpszDateReceived', c_wchar_p),
        ('lpszConversationID', c_wchar_p),
        ('flFlags', c_ulong),
        ('lpOriginator', POINTER(_Recipient)),
        ('nRecipCount', c_ulong),
        ('lpRecips', POINTER(_Recipient)),
        ('nFileCount', c_ulong),
        ('lpFiles', POINTER(_File)),
    ]


# Classic Outlook driven the way the cabinet's Excel macros do it: create the mail, keep the signature, display it.
# One PowerShell stays open with Outlook attached; each mail is then one line of data, not a new start.
BRIDGE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$script:outlook = $null
function Get-Outlook {
    if ($script:outlook) { try { [void]$script:outlook.Version; return $script:outlook } catch { $script:outlook = $null } }
    try { $script:outlook = [Runtime.InteropServices.Marshal]::GetActiveObject('Outlook.Application') }
    catch { $script:outlook = New-Object -ComObject Outlook.Application }
    return $script:outlook
}
try { [void](Get-Outlook); [Console]::Out.WriteLine('READY') } catch { [Console]::Out.WriteLine('NO_OUTLOOK') }
[Console]::Out.Flush()
while ($null -ne ($line = [Console]::In.ReadLine())) {
    try {
        $data = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($line)) | ConvertFrom-Json
        $mail = (Get-Outlook).CreateItem(0)
        $mail.Display($false)
        $signature = $mail.HTMLBody
        $mail.To = $data.to
        $mail.Subject = $data.subject
        $mail.HTMLBody = $data.html + $signature
        foreach ($file in $data.attachments) { [void]$mail.Attachments.Add([string]$file) }
        [Console]::Out.WriteLine('OK')
    } catch { $script:outlook = $null; [Console]::Out.WriteLine('ERROR') }
    [Console]::Out.Flush()
}
"""


def mail_payload(recipient: str, subject: str, body: str, attachments: list[Path]) -> str:
    """The mail as one line of base64 data: it never reaches PowerShell as code."""
    text = html.escape(body.replace('\r\n', '\n')).replace('\n', '<br>')
    data = json.dumps(
        {
            'to': recipient,
            'subject': subject,
            'attachments': [str(Path(path).resolve()) for path in attachments],
            'html': f'<div style="font-family:Calibri,Arial,sans-serif;font-size:11pt">{text}</div><br>',
        }
    )
    return base64.b64encode(data.encode('utf-8')).decode('ascii')


def bridge_command() -> list[str]:
    encoded = base64.b64encode(BRIDGE_SCRIPT.encode('utf-16-le')).decode('ascii')
    return ['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded]


class OutlookBridge:
    """Keeps classic Outlook within reach: started ahead of the first mail, reused for the next ones."""

    def __init__(self, command=bridge_command):
        self.command = command
        self.process = None
        self.answers = None
        self.lock = threading.Lock()

    def start(self) -> bool:
        with self.lock:
            return self._start()

    def _start(self) -> bool:
        if self.process is not None and self.process.poll() is None:
            return True
        try:
            self.process = subprocess.Popen(
                self.command(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding='utf-8',
                bufsize=1,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        except OSError:
            self.process = None
            return False
        self.answers = queue.Queue()
        threading.Thread(target=self._listen, args=(self.process, self.answers), daemon=True).start()
        # Starting Outlook itself can take a while on a busy workstation.
        if self._answer(60) != 'READY':
            self._stop()
            return False
        return True

    @staticmethod
    def _listen(process, answers):
        for line in process.stdout:
            answers.put(line.strip())
        answers.put(None)

    def _answer(self, timeout):
        try:
            return self.answers.get(timeout=timeout)
        except queue.Empty:
            return None

    def open(self, recipient: str, subject: str, body: str, attachments: list[Path]) -> None:
        with self.lock:
            if not self._start():
                raise DraftUnavailable('Outlook classique n’a pas répondu.')
            try:
                self.process.stdin.write(mail_payload(recipient, subject, body, attachments) + '\n')
                self.process.stdin.flush()
            except (OSError, ValueError):
                self._stop()
                raise DraftUnavailable('Outlook classique n’a pas répondu.') from None
            answer = self._answer(90)
            if answer != 'OK':
                if answer is None:
                    self._stop()
                raise DraftUnavailable('Outlook classique n’a pas pu préparer le mail.')

    def _stop(self):
        if self.process is not None:
            try:
                self.process.kill()
            except OSError:
                pass
        self.process = None

    def close(self):
        with self.lock:
            self._stop()


BRIDGE = OutlookBridge()


def prepare_outlook() -> None:
    """Start the bridge ahead of the first mail, so that it opens without waiting for Outlook."""
    if sys.platform == 'win32':
        threading.Thread(target=BRIDGE.start, daemon=True).start()


def open_draft(recipient: str, subject: str, body: str, attachments: list[Path]) -> None:
    """Show a compose window, filled in and with the notices attached; nothing is sent.

    Classic Outlook is asked first, as the cabinet's Excel macros do; then the default Windows mail application."""
    if sys.platform != 'win32':
        raise DraftUnavailable('L’ouverture dans Outlook n’est possible que sous Windows.')
    try:
        BRIDGE.open(recipient, subject, body, attachments)
        return
    except DraftUnavailable:
        pass
    open_with_default_mail(recipient, subject, body, attachments)


def open_with_default_mail(recipient: str, subject: str, body: str, attachments: list[Path]) -> None:
    """Hand the mail to the default Windows mail application (Simple MAPI), with its compose window."""
    try:
        send_mail = ctypes.WinDLL('mapi32.dll').MAPISendMailW
    except (OSError, AttributeError):
        raise DraftUnavailable(
            'Aucune messagerie compatible n’est installée (Outlook classique par exemple).'
        ) from None
    send_mail.restype = c_ulong
    send_mail.argtypes = [c_void_p, c_void_p, POINTER(_Message), c_ulong, c_ulong]
    recipients = (_Recipient * 1)(_Recipient(0, MAPI_TO, recipient, f'SMTP:{recipient}', 0, None))
    files = (_File * max(1, len(attachments)))()
    for index, path in enumerate(attachments):
        files[index] = _File(0, 0, 0xFFFFFFFF, str(Path(path).resolve()), Path(path).name, None)
    message = _Message(
        0,
        subject,
        body.replace('\r\n', '\n').replace('\n', '\r\n'),
        None,
        None,
        None,
        0,
        None,
        1,
        recipients,
        len(attachments),
        files if attachments else None,
    )
    result = send_mail(None, None, ctypes.byref(message), MAPI_LOGON_UI | MAPI_DIALOG | MAPI_DIALOG_MODELESS, 0)
    if result not in (0, 1):
        raise DraftUnavailable(MAPI_ERRORS.get(result, f'La messagerie n’a pas pu ouvrir le mail (code {result}).'))
