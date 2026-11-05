"""
Scraper de Aldi
"""

import asyncio
import json
import logging
import re
import httpx
from app.schemas.product import Product
from app.sections import classify
from app.scrapers.http import get_text

logger = logging.getLogger(__name__) # logger

BASE = "https://www.aldi.es"
SITEMAP = f"{BASE}/sitemaps/.aldi-nord-sitemap-pages.xml" # Lista de páginas de categoría

# Categorías que no se scrapean: "marcas" repite productos de otras categorías
# y el resto no tiene sentido para una lista de la compra
EXCLUDED = {"marcas", "verano", "juguetes", "bazar"}

NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

# Ofertas por cantidad ("2ª ud al -50%", "4x1 €"): Aldi da como precio el de la
# unidad con descuento, así que el precio de una sola unidad es el tachado
MULTIBUY = re.compile(r"\d\s*ª\s*ud|\d+\s*x\s*\d", re.I)

# scrape descarga y devuelve el catálogo de Aldi (precios de península), ya normalizado.
# Cada página de categoría trae sus productos en los datos de Next.js (estado de Algolia).
async def scrape(client: httpx.AsyncClient) -> list[Product]:
    urls = _paginas_categoria(await get_text(client, SITEMAP))
    logger.info("Aldi: %d páginas de categoría a descargar", len(urls))

    productos: dict[str, Product] = {}
    limite = asyncio.Semaphore(3)  # máximo 3 peticiones a la vez

    async def bajar(url: str) -> None:
        async with limite:
            try:
                html = await get_text(client, url)
            except httpx.HTTPError as error:
                logger.error("Página %s perdida: %s", url, error)
                return
        for producto in _parsear_pagina(html):
            productos.setdefault(producto.id, producto)

    await asyncio.gather(*(bajar(u) for u in urls))
    return list(productos.values())

# _paginas_categoria saca del sitemap las páginas /productos/... que interesan
def _paginas_categoria(sitemap: str) -> list[str]:
    urls = re.findall(r"<loc>\s*(https://www\.aldi\.es/productos/[^<\s]+\.html)\s*</loc>", sitemap)
    return [u for u in urls if u.removeprefix(f"{BASE}/productos/").removesuffix(".html").split("/")[0] not in EXCLUDED]

# _parsear_pagina extrae los productos de los datos de Next.js de una página de categoría
def _parsear_pagina(html: str) -> list[Product]:
    coincidencia = NEXT_DATA.search(html)
    if not coincidencia:
        return []
    try:
        datos = json.loads(coincidencia.group(1))
    except json.JSONDecodeError:
        return []
    estado = ((datos.get("props") or {}).get("pageProps") or {}).get("algoliaState")
    return [p for hit in _hits(estado) if (p := _parsear_hit(hit))]

# _hits recorre el estado de Algolia y devuelve todos los resultados
def _hits(nodo) -> list[dict]:
    if isinstance(nodo, dict):
        if isinstance(nodo.get("hits"), list):
            return [h for h in nodo["hits"] if isinstance(h, dict)]
        return [h for valor in nodo.values() for h in _hits(valor)]
    if isinstance(nodo, list):
        return [h for valor in nodo for h in _hits(valor)]
    return []

# _parsear_hit normaliza un producto de Aldi a nuestro esquema Product
def _parsear_hit(hit: dict) -> Product | None:
    if not hit.get("objectID") or not hit.get("name"):
        return None
    if hit.get("isComingSoon") or hit.get("isAvailable") is False:
        return None

    precio = hit.get("currentPrice") or {}
    actual = _a_float(precio.get("priceValue"))
    tachado = _a_float((precio.get("strikePrice") or {}).get("strikePriceValue"))
    anterior = tachado if actual is not None and tachado is not None and tachado > actual else None
    etiqueta = ((precio.get("priceTagLabels") or {}).get("promoText1") or "").strip()
    if anterior is not None and MULTIBUY.search(etiqueta):
        actual, anterior = anterior, None

    base = (precio.get("basePrice") or [{}])[0]
    imagen = next((a.get("url") for a in hit.get("assets") or [] if a.get("type") == "primary"), None)
    jerarquia = hit.get("hierarchicalCategories") or {}
    categorias = jerarquia.get("lvl1") or []
    ruta = categorias[0].split(" > ") if categorias else (jerarquia.get("lvl0") or [hit.get("mainCategoryID")])[:1]

    return Product(
        id=str(hit["objectID"]),
        supermarket="aldi",
        name=hit["name"],
        description=hit.get("salesUnit"),
        brand=(hit.get("brandName") or "").replace("®", "").strip() or None,
        price=actual,
        price_per_unit=_a_float(base.get("basePriceValue")),
        unit=base.get("basePriceScale"),
        previous_price=anterior,
        promotion=etiqueta or ("Oferta" if anterior else None),
        image_url=f"{imagen}?wid=400&hei=400" if imagen else None,
        category=categorias[0].split(" > ")[-1] if categorias else hit.get("mainCategoryID"),
        section=classify(ruta, hit["name"]),
        url=f"{BASE}/producto/{hit['productSlug']}.html" if hit.get("productSlug") else None,
    )

# _a_float convierte un valor a float, devolviendo None si no es posible
def _a_float(valor) -> float | None:
    try:
        return float(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None
