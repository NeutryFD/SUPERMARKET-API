"""
Scraper de las tiendas Aktios (Consum y Masymas)
"""

import logging
import re
import httpx
from app.schemas.product import Product
from app.sections import classify
from app.scrapers.http import get_json

logger = logging.getLogger(__name__) # logger

# scrape descarga y devuelve el catálogo completo de Consum, ya normalizado
async def scrape(client: httpx.AsyncClient, supermarket: str, base_url: str) -> list[Product]:
    productos: dict[str, Product] = {}
    offset = 0
    rutas = await _rutas_categorias(client, base_url)

    while True:
        data = await get_json(
            client,
            f"{base_url}/api/rest/V1.0/catalog/product",
            limit=100,
            offset=offset,
            showRecommendations="false",
        )
        for p in data.get("products", []):
            producto = _parsear_producto(p, supermarket, rutas)
            if producto:
                productos.setdefault(producto.id, producto)
        offset += 100
        if not data.get("hasMore") or not data.get("products"):
            return list(productos.values())

# Categorías raíz que agrupan ofertas de todas las secciones
OFFER_ROOTS = {"Ahora más barato", "Ofertas"}

# _rutas_categorias descarga el menú de categorías y devuelve {id: ruta de nombres}
async def _rutas_categorias(client: httpx.AsyncClient, base_url: str) -> dict[int, list[str]]:
    try:
        menu = await get_json(client, f"{base_url}/api/rest/V1.0/shopping/category/menu")
    except httpx.HTTPError as error:
        logger.warning("Menú de categorías de %s no disponible: %s", base_url, error)
        return {}
    rutas: dict[int, list[str]] = {}

    def recorrer(nodo: dict, ruta: list[str]) -> None:
        actual = [*ruta, nodo.get("name") or ""]
        if nodo.get("id") is not None:
            rutas[nodo["id"]] = actual
        for hijo in nodo.get("subcategories") or []:
            recorrer(hijo, actual)

    for nodo in menu if isinstance(menu, list) else []:
        recorrer(nodo, [])
    return rutas

# _promocion devuelve (precio anterior, etiqueta) de un producto de Aktios.
# Con precio de oferta, la etiqueta es el % de rebaja; sin él, se usa el texto
# de la oferta (packs, cheque ahorro...)
def _promocion(p: dict, precios: dict) -> tuple[float | None, str | None]:
    normal = (precios.get("PRICE") or {}).get("centAmount")
    oferta = (precios.get("OFFER_PRICE") or {}).get("centAmount")
    if normal and oferta and oferta < normal:
        return normal, f"-{round((normal - oferta) / normal * 100)}%"
    for o in p.get("offers") or []:
        texto = re.sub(r"<[^>]+>", " ", o.get("shortDescription") or "")
        texto = re.sub(r"\s+", " ", texto).strip()
        if texto:
            return None, texto[:64]
    return None, None

# _parsear_producto normaliza un producto de la API de Aktios a nuestro esquema Product
def _parsear_producto(p: dict, supermarket: str, rutas: dict[int, list[str]] | None = None) -> Product | None:
    datos = p.get("productData") or {}
    if not datos.get("name"):
        return None

    # Precio: si hay oferta vigente se usa; si no, el precio normal.
    lista_precios = (p.get("priceData") or {}).get("prices", [])
    precios = {
        entrada.get("id"): entrada.get("value") or {}
        for entrada in lista_precios
    }
    valor = precios.get("OFFER_PRICE") or precios.get("PRICE") or {}

    # Imagen: en Consum el campo imageURL está roto (404) y las buenas
    # están en "media"; en Masymas es al revés (media viene vacío).
    media = sorted(
        (m for m in p.get("media") or [] if m.get("url")),
        key=lambda m: m.get("order", 0),
    )
    imagen = media[0]["url"] if media else datos.get("imageURL")

    categorias = p.get("categories") or []
    # Un producto puede estar también en categorías de ofertas: se usa la primera que no lo es
    rutas = rutas or {}
    ruta = next(
        (rutas[c["id"]] for c in categorias if c.get("id") in rutas and rutas[c["id"]][0] not in OFFER_ROOTS),
        [categorias[0].get("name")] if categorias else [],
    )
    anterior, promocion = _promocion(p, precios)

    return Product(
        id=str(p.get("code") or p["id"]),
        supermarket=supermarket,
        name=datos["name"],
        description=datos.get("description"),
        brand=(datos.get("brand") or {}).get("name"),
        price=valor.get("centAmount"),  # pese al nombre, viene en euros
        price_per_unit=valor.get("centUnitAmount"),
        unit=(p.get("priceData") or {}).get("unitPriceUnitType") or None,
        previous_price=anterior,
        promotion=promocion,
        image_url=imagen,
        category=ruta[-1] if ruta else None,
        section=classify(ruta, datos["name"]),
        ean=p.get("ean"),
        url=datos.get("url"),
    )
