import asyncio
import base64
import io

from PIL import Image, ImageDraw, ImageFont

from collecte_cfe.captcha_ocr import recognize_captcha
from collecte_cfe.login import attempt_login


class Field:
    def __init__(self, value='', attribute=None):
        self.value = value
        self.attribute = attribute
        self.filled = []
        self.clicked = False

    async def wait_for(self, **_kwargs):
        return None

    async def evaluate(self, expression):
        return bool(self.value) if 'Boolean(' in expression else self.value

    async def fill(self, value):
        self.value = value
        self.filled.append(value)

    async def get_attribute(self, _name):
        return self.attribute

    async def click(self):
        self.clicked = True

    async def is_visible(self):
        return False

    async def inner_text(self):
        return ''


class LoginPage:
    url = 'https://cfspro-idp.impots.gouv.fr/oauth2/authorize'

    def __init__(self):
        self.fields = {
            'input[name="user"]': Field(),
            'input[name="password"]': Field(),
            '#input-captcha': Field(),
            'img#captcha': Field(attribute='data:image/png;base64,' + base64.b64encode(b'png-image').decode()),
            '#auth-credentials-messages': Field(),
        }
        self.button = Field()
        self.refresh = Field()

    def locator(self, selector):
        return self.fields[selector]

    def get_by_role(self, role, name, **_kwargs):
        assert role == 'button'
        if name == 'Se connecter':
            return self.button
        raise AssertionError(name)


class Portal:
    def __init__(self, page, connected=True):
        self.main = page
        self._connected = connected

    async def connected(self):
        return self._connected and self.main.button.clicked


def test_login_uses_only_selected_account_and_captcha_from_current_page():
    page = LoginPage()
    observed = []

    async def recognize(data):
        observed.append(data)
        return 'ABC123'

    result = asyncio.run(attempt_login(Portal(page), 'expert@example.fr', 'local secret', recognize))
    assert result == 'connected'
    assert page.fields['input[name="user"]'].filled == ['expert@example.fr']
    assert page.fields['input[name="password"]'].filled == ['local secret']
    assert page.fields['#input-captcha'].filled == ['ABC123']
    assert page.button.clicked and observed == [b'png-image']


def test_login_leaves_user_typing_and_failed_captcha_for_manual_recovery():
    page = LoginPage()
    page.fields['input[name="user"]'].value = 'human@example.fr'

    async def recognize(_data):
        raise AssertionError('Do not read a CAPTCHA after a person has started typing')

    assert asyncio.run(attempt_login(Portal(page), 'expert@example.fr', 'secret', recognize)) == 'manual'
    assert not page.button.clicked
    assert page.fields['input[name="user"]'].value == 'human@example.fr'

    page = LoginPage()
    assert (
        asyncio.run(
            attempt_login(Portal(page), 'expert@example.fr', 'secret', lambda _data: asyncio.sleep(0, result=None))
        )
        == 'manual'
    )
    assert not page.button.clicked


def test_login_never_fills_credentials_on_unexpected_host():
    page = LoginPage()
    page.url = 'https://example.org/login'

    async def recognize(_data):
        raise AssertionError('No CAPTCHA from external site')

    assert asyncio.run(attempt_login(Portal(page), 'expert@example.fr', 'secret', recognize)) == 'manual'
    assert page.fields['input[name="password"]'].filled == []


def test_unreadable_captcha_is_regenerated_at_most_four_times():
    page = LoginPage()

    class Refresh(Field):
        def __init__(self):
            super().__init__()
            self.clicks = 0

        async def click(self):
            self.clicks += 1
            page.fields['img#captcha'].attribute = (
                'data:image/png;base64,' + base64.b64encode(f'image-{self.clicks}'.encode()).decode()
            )

    page.refresh = Refresh()
    page.locator = lambda selector: page.refresh if selector == '.renewcaptchaclick' else page.fields[selector]

    async def unreadable(_data):
        return None

    refreshes = []
    assert (
        asyncio.run(attempt_login(Portal(page), 'expert@example.fr', 'secret', unreadable, on_refresh=refreshes.append))
        == 'captcha_unreadable'
    )
    assert page.refresh.clicks == 4
    assert refreshes == [1, 2, 3, 4]
    assert not page.button.clicked
    assert page.fields['input[name="password"]'].filled == []


def test_explicit_captcha_error_refreshes_then_can_connect():
    page = LoginPage()

    class CountingButton(Field):
        clicks = 0

        async def click(self):
            self.clicks += 1

    page.button = CountingButton()

    class CaptchaError(Field):
        async def is_visible(self):
            return page.button.clicks == 1

        async def inner_text(self):
            return 'Le code CAPTCHA est incorrect.'

    page.fields['#auth-credentials-messages'] = CaptchaError()

    class Refresh(Field):
        clicks = 0

        async def click(self):
            self.clicks += 1
            page.fields['img#captcha'].attribute = 'data:image/png;base64,' + base64.b64encode(b'new-image').decode()

    page.refresh = Refresh()
    page.locator = lambda selector: page.refresh if selector == '.renewcaptchaclick' else page.fields[selector]

    class PortalAfterRetry(Portal):
        async def connected(self):
            return page.button.clicks >= 2

    async def recognize(_data):
        return 'ABC123'

    assert asyncio.run(attempt_login(PortalAfterRetry(page), 'expert@example.fr', 'secret', recognize)) == 'connected'
    assert page.refresh.clicks == 1
    assert page.button.clicks == 2


def test_password_refusal_never_refreshes_or_retypes_password():
    page = LoginPage()

    class CredentialError(Field):
        async def is_visible(self):
            return page.button.clicked

        async def inner_text(self):
            return "L'identifiant ou le mot de passe renseigné est incorrect."

    page.fields['#auth-credentials-messages'] = CredentialError()

    class Refresh(Field):
        clicks = 0

        async def click(self):
            self.clicks += 1

    page.refresh = Refresh()
    page.locator = lambda selector: page.refresh if selector == '.renewcaptchaclick' else page.fields[selector]

    async def recognize(_data):
        return 'ABC123'

    assert (
        asyncio.run(attempt_login(Portal(page, connected=False), 'expert@example.fr', 'wrong password', recognize))
        == 'refused'
    )
    assert page.refresh.clicks == 0
    assert page.fields['input[name="password"]'].filled == ['wrong password']


def test_local_captcha_recognition_works_without_persisting_image(tmp_path):
    image = Image.new('RGB', (200, 50), 'white')
    font = ImageFont.truetype(r'C:\Windows\Fonts\arialbd.ttf', 35)
    ImageDraw.Draw(image).text((12, 2), 'aBc123', font=font, fill='#243c87')
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    assert asyncio.run(recognize_captcha(stream.getvalue())) == 'aBc123'
    assert list(tmp_path.iterdir()) == []


def test_login_selectors_work_in_a_real_browser_without_contacting_the_portal():
    from playwright.async_api import async_playwright

    async def run():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()

            async def fulfill(route):
                image = base64.b64encode(b'png-image').decode()
                await route.fulfill(
                    content_type='text/html; charset=utf-8',
                    body=f"""
                    <input name="user" type="email">
                    <input name="password" type="password">
                    <input id="input-captcha">
                    <img id="captcha" src="data:image/png;base64,{image}">
                    <button onclick="document.body.innerHTML='<a>Me déconnecter</a>'">Se connecter</button>""",
                )

            await context.route('https://cfspro-idp.impots.gouv.fr/fake-login', fulfill)
            page = await context.new_page()
            await page.goto('https://cfspro-idp.impots.gouv.fr/fake-login')

            class BrowserPortal:
                main = page

                async def connected(self):
                    return await page.get_by_text('Me déconnecter').count() > 0

            async def recognize(_data):
                return 'ABC123'

            result = await attempt_login(BrowserPortal(), 'expert@example.fr', 'local secret', recognize)
            await browser.close()
            return result

    assert asyncio.run(run()) == 'connected'
