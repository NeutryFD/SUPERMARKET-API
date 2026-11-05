"""
Scraper de bonÀrea
"""

import asyncio
import logging
import re
import httpx
from app.schemas.product import Product
from app.sections import classify
from app.scrapers.http import post_form_json

logger = logging.getLogger(__name__) # logger

API = "https://www.bonarea-online.com/es/shop" # Base de la tienda online (en castellano)
IMAGES = "https://images.bonarea.com" # Servidor de imágenes de los productos
ROOT_REFERENCE = "13_300" # Cualquier categoría sirve: la respuesta trae el árbol completo

# Secciones de primer nivel que se scrapean (alimentación, cocinados, bebidas,
# droguería, higiene, perfumería y mascotas). El resto (bricolaje, automoción,
# ropa...) no tiene sentido para una lista de la compra.
SECTIONS = {"300", "310", "320", "330", "340", "350", "030"}

# scrape descarga y devuelve el catálogo de bonÀrea, ya normalizado.
# Los precios de bonÀrea no dependen del código postal.
async def scrape(client: httpx.AsyncClient) -> list[Product]:
    raiz = await post_form_json(client, f"{API}/ShoppingBody", reference=ROOT_REFERENCE)
    hojas = _categorias_hoja(raiz.get("nivells", []))
    logger.info("bonÀrea: %d categorías a descargar", len(hojas))

    productos: dict[str, Product] = {}
    fallidas: list[tuple[str, str, list[str]]] = []
    limite = asyncio.Semaphore(3)  # máximo 3 peticiones a la vez

    async def bajar_categoria(identificador: str, nombre: str, ruta: list[str]) -> None:
        async with limite:
            try:
                data = await post_form_json(
                    client, f"{API}/ShoppingBody", reference=identificador.replace("*", "_")
                )
            except httpx.HTTPError:
                fallidas.append((identificador, nombre, ruta))
                return
        for producto in _parsear_articulos(data.get("articles", []), nombre, ruta):
            productos.setdefault(producto.id, producto)

    await asyncio.gather(*(bajar_categoria(i, n, r) for i, n, r in hojas))

    # Las categorías que fallaron se reintentan al final, de una en una y con pausa
    for identificador, nombre, ruta in fallidas:
        await asyncio.sleep(5)
        try:
            data = await post_form_json(
                client, f"{API}/ShoppingBody", reference=identificador.replace("*", "_")
            )
        except httpx.HTTPError as error:
            logger.error("Categoría %s perdida: %s", identificador, error)
            continue
        for producto in _parsear_articulos(data.get("articles", []), nombre, ruta):
            productos.setdefault(producto.id, producto)

    return list(productos.values())

# _categorias_hoja recorre el árbol de categorías y devuelve (identificador, nombre, ruta)
# de las categorías finales de las secciones que interesan
def _categorias_hoja(nivells: list[dict]) -> list[tuple[str, str, list[str]]]:
    hojas = []

    def recorrer(nodo: dict, ruta: list[str]) -> None:
        nombre = nodo.get("descripcio") or nodo.get("description")
        hijos = nodo.get("children") or []
        if hijos:
            for hijo in hijos:
                recorrer(hijo, [*ruta, nombre])
        elif nodo.get("identifier"):
            hojas.append((nodo["identifier"], nombre, [*ruta, nombre]))

    for seccion in nivells:
        for nodo in seccion.get("children") or []:
            recorrer(nodo, [seccion.get("descripcio")])

    # El identificador es "13*300*010*...": la segunda parte es la sección
    return [h for h in hojas if h[0].split("*")[1] in SECTIONS]

# Distintivos de promoción de bonÀrea (en "caracteristiques"), por orden de prioridad
PROMOTIONS = {
    "SUPERPREU": "Superprecio",
    "PREU_IMBATIBLE": "Precio imbatible",
    "BAIXADA_PREU": "Bajada de precio",
}

# _promocion devuelve la etiqueta de promoción del artículo, o None
def _promocion(articulo: dict) -> str | None:
    descuento = _a_float(articulo.get("discount")) or 0
    if descuento > 0:
        return f"-{descuento:g}%"
    distintivos = {c.get("descripcio") for c in articulo.get("caracteristiques") or []}
    return next((etiqueta for codigo, etiqueta in PROMOTIONS.items() if codigo in distintivos), None)

# _parsear_articulos normaliza los artículos de una categoría de bonÀrea a nuestro esquema Product
def _parsear_articulos(
    articulos: list[dict], categoria: str | None, ruta: list[str] | None = None
) -> list[Product]:
    resultado = []
    for a in articulos:
        if not a.get("isValid", True) or not a.get("identifier") or not a.get("description"):
            continue
        precio_unidad, unidad = _parsear_precio_unidad(a.get("unitPrice"))
        imagenes = a.get("image") or []
        resultado.append(
            Product(
                id=a["identifier"].replace("*", "_"),
                supermarket="bonarea",
                name=a["description"],
                description=a.get("measurementUnit") or None,
                price=_a_float(a.get("priceToPay")),
                price_per_unit=precio_unidad,
                unit=unidad,
                promotion=_promocion(a),
                image_url=f"{IMAGES}/{imagenes[0]}?width=400&height=400" if imagenes else None,
                category=categoria,
                section=classify(ruta or [categoria], a["description"]),
                url=a.get("urlFriendly"),
            )
        )
    return resultado

# _parsear_precio_unidad convierte "10,15 €/kg" en (10.15, "kg")
def _parsear_precio_unidad(texto: str | None) -> tuple[float | None, str | None]:
    if not texto:
        return None, None
    coincidencia = re.match(r"\s*([\d.]+(?:,\d+)?)\s*€\s*/\s*(.+?)\s*$", texto)
    if not coincidencia:
        return None, None
    numero = coincidencia.group(1).replace(".", "").replace(",", ".")
    return _a_float(numero), coincidencia.group(2).rstrip(".")

# _a_float convierte un valor a float, devolviendo None si no es posible
def _a_float(valor) -> float | None:
    try:
        return float(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None
