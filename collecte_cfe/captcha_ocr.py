"""Reconnaissance locale d'une image CAPTCHA, entièrement en mémoire."""

import asyncio
import gc
import io
import re

from PIL import Image


def _recognize_local(image_bytes):
    import ddddocr

    model = ddddocr.DdddOcr(show_ad=False)
    try:
        return model.classification(image_bytes)
    finally:
        del model
        gc.collect()


async def recognize_captcha(image_bytes):
    """Retourne six caractères sûrs ou None ; ne conserve ni image ni résultat."""
    try:
        source = Image.open(io.BytesIO(image_bytes))
        if not (50 <= source.width <= 1000 and 20 <= source.height <= 300):
            return None
        result = await asyncio.to_thread(_recognize_local, image_bytes)
        cleaned = re.sub(r'[^A-Za-z0-9]', '', result)
        return cleaned if re.fullmatch(r'[A-Za-z0-9]{6}', cleaned) else None
    except Exception:
        pass
    return None
