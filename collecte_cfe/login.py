"""Connexion locale avec reprises bornées du seul CAPTCHA."""

import asyncio
import base64
import re
from urllib.parse import urlparse

from .captcha_ocr import recognize_captcha


def _captcha_error(message):
    return bool(
        re.search(r'captcha|caract[eè]res|code de s[eé]curit[eé]', message, re.I)
        and re.search(r'incorrect|erron[eé]|invalide|erreur', message, re.I)
    )


async def _refresh_captcha(page, image, previous_src):
    await page.locator('.renewcaptchaclick').click()
    for _ in range(30):
        src = await image.get_attribute('src')
        if src and src != previous_src:
            return src
        await asyncio.sleep(0.1)
    return None


async def attempt_login(portal, login, password, recognize=recognize_captcha, max_refreshes=4, on_refresh=None):
    page = portal.main
    if urlparse(page.url).hostname != 'cfspro-idp.impots.gouv.fr' or not login or not password:
        return 'manual'
    email = page.locator('input[name="user"]')
    secret = page.locator('input[name="password"]')
    answer = page.locator('#input-captcha')
    image = page.locator('img#captcha')
    try:
        await email.wait_for(state='visible', timeout=12000)
        await secret.wait_for(state='visible', timeout=12000)
        await answer.wait_for(state='visible', timeout=12000)
        # Never replace text the user has started entering in the visible page.
        for field in (email, secret, answer):
            if await field.evaluate('(element) => Boolean(element.value)'):
                return 'manual'
        refreshes = 0
        previous_values = None
        while True:
            if previous_values is not None:
                for field, previous in zip((email, secret, answer), previous_values):
                    current = await field.evaluate('(element) => element.value')
                    if current and current.casefold() != previous.casefold():
                        return 'manual'
            src = await image.get_attribute('src')
            if not src or not src.startswith('data:image/png;base64,'):
                return 'captcha_unreadable'
            image_bytes = base64.b64decode(src.partition(',')[2], validate=True)
            captcha = await recognize(image_bytes)
            if not captcha or not re.fullmatch(r'[A-Za-z0-9]{6}', captcha):
                if refreshes >= max_refreshes:
                    return 'captcha_unreadable'
                if not await _refresh_captcha(page, image, src):
                    return 'captcha_unreadable'
                refreshes += 1
                if on_refresh:
                    on_refresh(refreshes)
                continue
            await email.fill(login)
            await secret.fill(password)
            await answer.fill(captcha)
            previous_values = (login, password, captcha)
            await page.get_by_role('button', name='Se connecter', exact=True).click()
            captcha_rejected = False
            for _ in range(40):
                if await portal.connected():
                    return 'connected'
                error = page.locator('#auth-credentials-messages')
                if await error.is_visible():
                    if _captcha_error(await error.inner_text()):
                        captcha_rejected = True
                        break
                    return 'refused'
                await asyncio.sleep(0.25)
            if not captcha_rejected:
                return 'manual'
            if refreshes >= max_refreshes:
                return 'captcha_unreadable'
            if not await _refresh_captcha(page, image, src):
                return 'captcha_unreadable'
            refreshes += 1
            if on_refresh:
                on_refresh(refreshes)
    except Exception:
        return 'manual'
