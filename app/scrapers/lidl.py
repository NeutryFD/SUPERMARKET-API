"""
Scraper de Lidl
"""

import asyncio
import html
import json
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo
import httpx
from app.schemas.product import Product
from app.sections import classify
from app.scrapers.http import get_text

logger = logging.getLogger(__name__) # logger

WEB = "https://www.lidl.es" # URL base de la tienda de Lidl

# Lidl no vende alimentación online en España: solo publica las ofertas de sus
# tiendas. Esta página de ofertas semanales enlaza al resto de páginas "En tienda"
# (Precios imbatibles, Super finde, Deluxe...), que traen sus productos en el HTML.
OFFERS = f"{WEB}/c/store/ofertas-semanales/a10089449"
STORE_PAGE = re.compile(r'(/c/store/[a-z0-9-]+/a\d+)"')
GRID_DATA = re.compile(r'data-grid-data="([^"]*)"')

# Solo alimentación (incluye droguería); fuera las plantas (P+F) y el bazar
FOOD = {"Food", "F+V"}

# Región de precios de la península
REGION = "1"

MADRID = ZoneInfo("Europe/Madrid")

# Envase de un peso o volumen ("1 kg", "1,5 l", "6x125 g"), para el precio por kg/l
PACKAGING = re.compile(r"^\s*(?:(\d+)\s*x\s*)?(\d+(?:[.,]\d+)?)\s*(kg|g|l|cl|ml)\s*$", re.I)
UNITS = {"kg": (1, "kg"), "g": (1000, "kg"), "l": (1, "l"), "cl": (100, "l"), "ml": (1000, "l")}

# scrape descarga las ofertas de esta semana de las tiendas Lidl, ya normalizadas
async def scrape(client: httpx.AsyncClient) -> list[Product]:
    portada = await get_text(client, OFFERS)
    urls = [OFFERS] + [f"{WEB}{ruta}" for ruta in _paginas_ofertas(portada) if f"{WEB}{ruta}" != OFFERS]
    logger.info("Lidl: %d páginas de ofertas a descargar", len(urls))

    productos: dict[str, Product] = {}
    limite = asyncio.Semaphore(2)  # máximo 2 peticiones a la vez

    async def bajar(url: str) -> str | None:
        if url == OFFERS:
            return portada
        async with limite:
            try:
                return await get_text(client, url)
            except httpx.HTTPError as error:
                logger.error("Página %s perdida: %s", url, error)
                return None

    for pagina in await asyncio.gather(*(bajar(u) for u in urls)):
        for producto in _parsear_pagina(pagina or ""):
            productos.setdefault(producto.id, producto)

    # Sin productos la web ha cambiado: mejor fallar que vaciar el catálogo guardado
    if not productos:
        raise RuntimeError("Lidl: no se ha encontrado ninguna oferta en sus páginas")
    return list(productos.values())

# _paginas_ofertas saca las páginas de ofertas en tienda de esta semana (no las de la próxima)
def _paginas_ofertas(portada: str) -> list[str]:
    rutas = dict.fromkeys(STORE_PAGE.findall(portada))
    return [r for r in rutas if "proxima-semana" not in r]

# _parsear_pagina extrae los productos de los atributos data-grid-data de una página
def _parsear_pagina(pagina: str) -> list[Product]:
    productos = []
    for crudo in GRID_DATA.findall(pagina):
        try:
            datos = json.loads(html.unescape(crudo))
        except json.JSONDecodeError:
            continue
        if producto := _parsear_producto(datos):
            productos.append(producto)
    return productos

# _parsear_producto normaliza un producto de Lidl a nuestro esquema Product
def _parsear_producto(datos: dict) -> Product | None:
    keyfacts = datos.get("keyfacts") or {}
    nombre = datos.get("fullTitle") or keyfacts.get("fullTitle") or datos.get("title")
    if not nombre or not datos.get("productId") or datos.get("category") not in FOOD:
        return None

    oferta = _oferta_actual(datos)
    if not oferta:
        return None  # solo tiene precio de la semana que viene
    precio, lidl_plus = oferta
    actual = _a_float(precio.get("price"))
    if actual is None:
        return None
    tachado = _a_float(precio.get("oldPrice") or (precio.get("discount") or {}).get("deletedPrice"))
    anterior = tachado if tachado is not None and tachado > actual else None

    envase = (precio.get("packaging") or {}).get("text")
    por_unidad, unidad = _precio_por_unidad(actual, envase)

    # La categoría es una ruta ("Mundos de necesidad/Comida y cerca de la comida/Frutas y hortalizas/Fruta"):
    # se quita la raíz común y el último tramo es el más concreto
    ruta = (keyfacts.get("wonCategoryPrimary") or "").split("/")[2:]
    url = datos.get("canonicalUrl")

    return Product(
        id=str(datos["productId"]),
        supermarket="lidl",
        name=nombre,
        description=_sin_html(keyfacts.get("description")) or envase,
        brand=(datos.get("brand") or {}).get("name"),
        price=actual,
        price_per_unit=por_unidad,
        unit=unidad,
        previous_price=anterior,
        promotion=_promocion(precio, lidl_plus, actual, anterior),
        image_url=datos.get("image"),
        category=ruta[-1] if ruta else None,
        section=_seccion(ruta, nombre),
        url=f"{WEB}{url}" if url else None,
    )

# _seccion clasifica primero por la categoría concreta ("Yogur" dentro de "Quesos, productos
# lácteos y huevos" es lácteo) y, si no encaja en ninguna, por la ruta entera
def _seccion(ruta: list[str], nombre: str) -> str:
    seccion = classify(ruta[-1:], nombre)
    return classify(ruta, nombre) if seccion == "otros" else seccion

# _oferta_actual devuelve el precio vigente de la península y si es solo con Lidl Plus
def _oferta_actual(datos: dict) -> tuple[dict, bool] | None:
    regiones = datos.get("regionsPrices") or {}
    region = regiones.get(REGION) or next(iter(regiones.values()), {})
    if isinstance(region.get("currentPrice"), dict):
        return region["currentPrice"], False
    lidl_plus = region.get("currentLidlPlusPrice")
    if isinstance(lidl_plus, dict) and isinstance(lidl_plus.get("price"), dict):
        return lidl_plus["price"], True
    return None

# _promocion compone la etiqueta: "Con Lidl Plus -27% · hasta el 8/11"
def _promocion(precio: dict, lidl_plus: bool, actual: float, anterior: float | None) -> str:
    partes = ["Con Lidl Plus"] if lidl_plus else []
    if anterior:
        partes.append(f"-{round((1 - actual / anterior) * 100)}%")
    texto = " ".join(partes) or "Oferta"
    fin = _fecha(precio.get("endDate"))
    return f"{texto} · hasta el {fin.day}/{fin.month}" if fin else texto

# _fecha convierte una fecha ISO de Lidl ("2026-11-08T22:59:59Z") a hora de Madrid
def _fecha(texto: str | None) -> datetime | None:
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00")).astimezone(MADRID)
    except ValueError:
        return None

# _precio_por_unidad calcula el precio por kg o l cuando el envase dice su peso o volumen
def _precio_por_unidad(precio: float, envase: str | None) -> tuple[float | None, str | None]:
    coincidencia = PACKAGING.match(envase or "")
    if not coincidencia:
        return None, None
    cantidad = int(coincidencia.group(1) or 1) * float(coincidencia.group(2).replace(",", "."))
    divisor, unidad = UNITS[coincidencia.group(3).lower()]
    if cantidad <= 0:
        return None, None
    return round(precio * divisor / cantidad, 2), unidad

# _sin_html limpia etiquetas y entidades HTML de un texto
def _sin_html(texto: str | None) -> str | None:
    if not texto:
        return None
    limpio = re.sub(r"<[^>]+>", " ", html.unescape(texto))
    return " ".join(limpio.split()) or None

# _a_float convierte un valor a float, devolviendo None si no es posible
def _a_float(valor) -> float | None:
    try:
        return float(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None
