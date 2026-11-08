"""
Scraper de Ametller Origen
"""

import base64
import hashlib
import html
import logging
import re
import secrets
from urllib.parse import parse_qs, urlparse
import httpx
from app.schemas.product import Product
from app.sections import classify
from app.scrapers.http import get_json

logger = logging.getLogger(__name__) # logger

WEB = "https://www.ametllerorigen.com" # URL base de la tienda de Ametller Origen

# La tienda es una PWA de Salesforce Commerce Cloud: el catálogo sale de su
# API pública (SCAPI), con los datos de cliente que la propia web publica
API = "https://4jppt37a.api.commercecloud.salesforce.com"
ORGANIZATION = "f_ecom_blzv_prd"
CLIENT_ID = "fd3c9db8-2a0d-4f4b-9e74-294e068f9ae4"
SITE = "ametller"
REDIRECT_URI = f"{WEB}/callback"
AUTH = f"{API}/shopper/auth/v1/organizations/{ORGANIZATION}/oauth2"

PAGE_SIZE = 200 # máximo que admite la búsqueda por página

# Unidad del precio de referencia de la tienda -> la nuestra
UNITS = {"KG": "kg", "L": "l", "ST": "ud", "DZ": "docena"}

# scrape descarga y devuelve el catálogo completo de Ametller Origen, ya normalizado
async def scrape(client: httpx.AsyncClient) -> list[Product]:
    # El cliente es exclusivo de este scraping: el token va en sus cabeceras
    client.headers["Authorization"] = f"Bearer {await _token_invitado(client)}"
    rutas = await _rutas_categorias(client)

    productos: dict[str, Product] = {}
    offset = 0
    while True:
        data = await get_json(
            client,
            f"{API}/search/shopper-search/v1/organizations/{ORGANIZATION}/product-search",
            siteId=SITE,
            refine="cgid=root",
            locale="es",
            currency="EUR",
            expand="images,prices,promotions,represented_products",
            limit=PAGE_SIZE,
            offset=offset,
        )
        hits = data.get("hits") or []
        for hit in hits:
            if producto := _parsear_producto(hit, rutas):
                productos.setdefault(producto.id, producto)
        offset += PAGE_SIZE
        if not hits or offset >= (data.get("total") or 0):
            break

    # Sin productos la API ha cambiado: mejor fallar que vaciar el catálogo guardado
    if not productos:
        raise RuntimeError("Ametller Origen: la búsqueda no ha devuelto ningún producto")
    return list(productos.values())

# _token_invitado inicia sesión como invitado (OAuth2 con PKCE, igual que la web)
async def _token_invitado(client: httpx.AsyncClient) -> str:
    verificador = secrets.token_urlsafe(64)
    reto = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).rstrip(b"=").decode()

    # El código llega en la redirección a la web, que no hace falta seguir
    respuesta = await client.get(
        f"{AUTH}/authorize",
        params={
            "client_id": CLIENT_ID,
            "channel_id": SITE,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "hint": "guest",
            "code_challenge": reto,
        },
        follow_redirects=False,
    )
    query = parse_qs(urlparse(respuesta.headers.get("location", "")).query)
    if "code" not in query:
        raise RuntimeError(f"Ametller Origen: login de invitado rechazado ({respuesta.status_code})")

    token = await client.post(
        f"{AUTH}/token",
        data={
            "grant_type": "authorization_code_pkce",
            "client_id": CLIENT_ID,
            "channel_id": SITE,
            "redirect_uri": REDIRECT_URI,
            "code": query["code"][0],
            "usid": query.get("usid", [""])[0],
            "code_verifier": verificador,
        },
    )
    token.raise_for_status()
    return token.json()["access_token"]

# _rutas_categorias descarga el árbol de categorías y devuelve {id: ruta de nombres}
async def _rutas_categorias(client: httpx.AsyncClient) -> dict[str, list[str]]:
    try:
        raiz = await get_json(
            client,
            f"{API}/product/shopper-products/v1/organizations/{ORGANIZATION}/categories/root",
            siteId=SITE,
            levels=3,
            locale="es",
        )
    except httpx.HTTPError as error:
        logger.warning("Categorías de Ametller Origen no disponibles: %s", error)
        return {}
    rutas: dict[str, list[str]] = {}

    def recorrer(nodo: dict, ruta: list[str]) -> None:
        actual = [*ruta, nodo.get("name") or ""]
        rutas[nodo["id"]] = actual
        for hijo in nodo.get("categories") or []:
            recorrer(hijo, actual)

    for nodo in raiz.get("categories") or []:
        recorrer(nodo, [])
    return rutas

# _promocion devuelve (precio, precio anterior, etiqueta) según las promociones del producto.
# Algunas bajan el precio (-20%, precio club, lotes); otras solo se anuncian (2x3€, 2ªu -50%)
def _promocion(precio: float, promociones: list[dict]) -> tuple[float, float | None, str | None]:
    etiquetas = [t for p in promociones if (t := _sin_html(p.get("calloutMsg")))]
    rebajas = [
        p["promotionalPrice"] for p in promociones
        if isinstance(p.get("promotionalPrice"), (int, float)) and p["promotionalPrice"] < precio
    ]
    if not rebajas:
        return precio, None, " · ".join(etiquetas)[:64] or None

    oferta = min(rebajas)
    descuento = f"-{round((1 - oferta / precio) * 100)}%"
    # "Precio club" se completa con el descuento; si no hay texto, solo el descuento
    etiquetas = [f"{t} {descuento}" if t.upper() == "PRECIO CLUB" else t for t in etiquetas] or [descuento]
    return oferta, precio, " · ".join(etiquetas)[:64]

# _parsear_producto normaliza un resultado de la búsqueda a nuestro esquema Product
def _parsear_producto(hit: dict, rutas: dict[str, list[str]]) -> Product | None:
    nombre = (hit.get("productName") or "").strip()
    precio = hit.get("price")
    if not nombre or not hit.get("productId") or not isinstance(precio, (int, float)):
        return None

    actual, anterior, promocion = _promocion(precio, hit.get("productPromotions") or [])
    por_unidad = hit.get("pricePerUnit")
    if isinstance(por_unidad, (int, float)) and anterior:
        por_unidad = round(por_unidad * actual / anterior, 2)  # viene calculado sobre el precio sin rebaja

    # Los datos del buscador interno de la tienda traen la categoría concreta y el código de barras
    extra = hit.get("c_instaleapHit") or {}
    ruta = rutas.get(str((extra.get("attributes") or {}).get("category")), [])
    codigos = extra.get("barcodes") or []

    return Product(
        id=str(hit["productId"]),
        supermarket="ametller",
        name=nombre,
        price=actual,
        price_per_unit=por_unidad,
        unit=UNITS.get(extra.get("unit") or "") or None,
        previous_price=anterior,
        promotion=promocion,
        image_url=(hit.get("image") or {}).get("link"),
        category=ruta[-1] if ruta else None,
        section=_seccion(ruta, nombre),
        ean=codigos[0] if codigos else None,
        url=f"{WEB}/es/p/{hit['productId']}.html",
    )

# _seccion clasifica de la categoría más concreta a la más general: "Carne y Charcutería > Pollo"
# es carne aunque la raíz diga charcutería. Si ninguna encaja, decide el nombre
def _seccion(ruta: list[str], nombre: str) -> str:
    for inicio in range(len(ruta) - 1, -1, -1):
        if (seccion := classify(ruta[inicio:])) != "otros":
            return seccion
    return classify([], nombre)

# _sin_html limpia etiquetas y entidades HTML de un texto
def _sin_html(texto: str | None) -> str | None:
    if not texto:
        return None
    limpio = re.sub(r"<[^>]+>", " ", html.unescape(texto))
    return " ".join(limpio.split()) or None
