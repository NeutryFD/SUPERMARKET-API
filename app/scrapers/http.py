"""
Utilidades HTTP compartidas por todos los scrapers
"""

import asyncio
import logging
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__) # logger

def new_client() -> httpx.AsyncClient:
    """Cliente HTTP con los ajustes comunes (timeout, user-agent)"""
    return httpx.AsyncClient(
        timeout=30,
        headers={"User-Agent": settings.user_agent},
        follow_redirects=True,
    )

async def get_json(client: httpx.AsyncClient, url: str, **params):
    """GET que reintenta hasta 3 veces con espera creciente entre intentos"""
    return await _con_reintentos(url, lambda: client.get(url, params=params or None))

async def post_form_json(client: httpx.AsyncClient, url: str, **data):
    """POST de formulario que devuelve JSON, con los mismos reintentos que get_json"""
    return await _con_reintentos(url, lambda: client.post(url, data=data))

async def get_text(client: httpx.AsyncClient, url: str) -> str:
    """GET que devuelve el cuerpo como texto (HTML, XML), con los mismos reintentos que get_json"""
    return await _con_reintentos(url, lambda: client.get(url), leer=lambda r: r.text)

async def _con_reintentos(url: str, peticion, leer=lambda r: r.json()):
    for intento in range(3):
        try:
            respuesta = await peticion()
            respuesta.raise_for_status()
            return leer(respuesta)
        except httpx.HTTPError as error:
            if intento == 2:  # tercer fallo: se rinde y avisa a quien llamó
                raise
            logger.warning("Fallo en %s (intento %d): %s", url, intento + 1, error)
            await asyncio.sleep(2 * (intento + 1))
