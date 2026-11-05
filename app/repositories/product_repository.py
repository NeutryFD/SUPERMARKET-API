"""
Capa de repositorio
"""

from datetime import datetime, timezone
import re
import unicodedata
from sqlalchemy import and_, case, delete, func, or_, select
from app.core.database import SessionLocal
from app.models.product import ProductRow
from app.repositories import backup_repository
from app.schemas.product import Product
from app.sections import classify


def _search_terms(query: str) -> list[str]:
    """Normaliza la consulta y separa sus palabras significativas."""
    normalized = "".join(
        character
        for character in unicodedata.normalize("NFKD", query.casefold())
        if not unicodedata.combining(character)
    )
    return re.findall(r"[a-z0-9]+", normalized)


def _normalized_column(column):
    """Expresión SQL sin mayúsculas ni tildes, compatible con SQLite y MySQL."""
    expression = func.lower(func.coalesce(column, ""))
    for source, replacement in (
        ("áàäâÁÀÄÂ", "a"),
        ("éèëêÉÈËÊ", "e"),
        ("íìïîÍÌÏÎ", "i"),
        ("óòöôÓÒÖÔ", "o"),
        ("úùüûÚÙÜÛ", "u"),
        ("ñÑ", "n"),
        ("çÇ", "c"),
    ):
        for character in source:
            expression = func.replace(expression, character, replacement)
    return expression


# Metodo de conveniencia para guardar el catálogo completo de un supermercado
def save_catalog(supermarket: str, products: list[Product]) -> None:
    """Borra el catálogo anterior del supermercado y guarda el nuevo,
    todo en una transacción (si algo falla a medias, no se pierde nada)"""
    now = datetime.now(timezone.utc)

    for product in products:
        if product.section is None:
            product.section = classify([product.category], product.name)

    rows = [
        ProductRow(
            external_id=product.id,
            scraped_at=now,
            **product.model_dump(exclude={"id"}),
        )
        for product in products
    ]

    with SessionLocal() as session, session.begin():
        # Backup del catálogo actual ANTES de borrarlo. Va en la misma
        # transacción: si algo falla, no hay ni backup ni reemplazo.
        backup_repository.create_backup(session, supermarket)
        session.execute(delete(ProductRow).where(ProductRow.supermarket == supermarket))
        session.add_all(rows)


# Contar productos de un supermercado
def count_products(supermarket: str) -> int:
    with SessionLocal() as session:
        query = select(func.count()).select_from(ProductRow)
        return session.scalar(query.where(ProductRow.supermarket == supermarket)) or 0


# Obtener la fecha del último scraping de un supermercado
def last_scraped_at(supermarket: str) -> datetime | None:
    with SessionLocal() as session:
        return session.scalar(
            select(func.max(ProductRow.scraped_at)).where(
                ProductRow.supermarket == supermarket
            )
        )


def get_categories(supermarket: str | None = None) -> list[str]:
    """Devuelve todas las categorías distintas, opcionalmente por supermercado."""
    query = (
        select(ProductRow.category)
        .where(ProductRow.category.is_not(None), ProductRow.category != "")
        .order_by(ProductRow.category)
        .distinct()
    )
    if supermarket:
        query = query.where(ProductRow.supermarket == supermarket)

    with SessionLocal() as session:
        return list(session.scalars(query).all())


# _orden devuelve el ORDER BY de la búsqueda según el criterio pedido
def _orden(sort: str, q: str | None) -> list:
    if sort == "price":
        return [ProductRow.price.is_(None), ProductRow.price, ProductRow.name]
    if sort == "price_per_unit":
        return [ProductRow.price_per_unit.is_(None), ProductRow.price_per_unit, ProductRow.name]
    if sort == "discount":
        # Mayor rebaja relativa primero; las promociones sin precio anterior, después
        rebaja = (ProductRow.previous_price - ProductRow.price) / ProductRow.previous_price
        return [
            ProductRow.promotion.is_(None),
            ProductRow.previous_price.is_(None),
            rebaja.desc(),
            ProductRow.name,
        ]
    terms = _search_terms(q) if q else []
    if sort == "relevance" and terms:
        nombre = _normalized_column(ProductRow.name)
        frase = " ".join(terms)
        # Primero el nombre que empieza por la frase buscada, luego el que la
        # contiene como palabra, luego el que la contiene en cualquier sitio
        frase_en_nombre = case(
            (nombre.like(f"{frase}%"), 0),
            (nombre.like(f"% {frase}%"), 1),
            (nombre.like(f"%{frase}%"), 2),
            else_=3,
        )
        # Después, los que tienen todas las palabras en el nombre (y no solo en la categoría)
        palabras_en_nombre = case(
            (and_(*(nombre.like(f"%{term}%") for term in terms)), 0), else_=1
        )
        empieza_por_primera = case((nombre.like(f"{terms[0]}%"), 0), else_=1)
        return [
            frase_en_nombre,
            palabras_en_nombre,
            empieza_por_primera,
            func.length(ProductRow.name),  # a igualdad, los nombres más cortos son más exactos
            ProductRow.name,
        ]
    if sort == "name":
        return [ProductRow.name]
    return [ProductRow.supermarket, ProductRow.name]


# _filtros compone el WHERE común a la búsqueda y al recuento por secciones
def _filtros(
    supermarket: str | None,
    q: str | None,
    category: str | None,
    promotion: bool = False,
    section: str | None = None,
    subcategory: str | None = None,
    supermarkets: list[str] | None = None,
) -> list:
    filtros = []
    if supermarket:
        filtros.append(ProductRow.supermarket == supermarket)
    if supermarkets is not None:
        # Lista vacía = ningún supermercado (el frontend los ha desactivado todos)
        filtros.append(ProductRow.supermarket.in_(supermarkets))
    if q:
        columns = (
            ProductRow.name,
            ProductRow.description,
            ProductRow.brand,
            ProductRow.category,
            ProductRow.ean,
        )
        normalized_columns = tuple(_normalized_column(column) for column in columns)
        for term in _search_terms(q):
            # Cada palabra puede estar en un campo distinto y en cualquier orden.
            filtros.append(
                or_(*(column.like(f"%{term}%") for column in normalized_columns))
            )
    if category:
        filtros.append(ProductRow.category.ilike(f"%{category}%"))
    if promotion:
        filtros.append(ProductRow.promotion.is_not(None))
    if section:
        filtros.append(ProductRow.section == section)
    if subcategory:
        filtros.append(ProductRow.category == subcategory)
    return filtros


# Buscar productos con filtros y paginación
def search_products(
    supermarket: str | None,
    q: str | None,
    category: str | None,
    page: int,
    page_size: int,
    sort: str = "relevance",
    promotion: bool = False,
    section: str | None = None,
    subcategory: str | None = None,
    supermarkets: list[str] | None = None,
) -> tuple[int, list[Product]]:
    """Devuelve (total de resultados, productos de la página pedida)
    Los filtros se aplican en SQL, que con los índices de la tabla es
    mucho más rápido que traer todo y filtrar en Python"""
    filtros = _filtros(supermarket, q, category, promotion, section, subcategory, supermarkets)

    with SessionLocal() as session:
        total = (
            session.scalar(select(func.count()).select_from(ProductRow).where(*filtros))
            or 0
        )
        rows = session.scalars(
            select(ProductRow)
            .where(*filtros)
            .order_by(*_orden(sort, q))
            .offset((page - 1) * page_size)  # salta las páginas anteriores
            .limit(page_size)
        ).all()
    return total, [row.to_schema() for row in rows]


# Contar productos por sección (y por categoría de tienda dentro de una sección)
def count_sections(
    supermarket: str | None,
    q: str | None,
    promotion: bool = False,
    section: str | None = None,
    supermarkets: list[str] | None = None,
) -> tuple[dict[str, int], dict[str, int]]:
    """Devuelve ({sección: productos}, {categoría de tienda: productos}).
    Las categorías solo se cuentan para la sección pedida."""
    filtros = _filtros(supermarket, q, None, promotion, supermarkets=supermarkets)
    with SessionLocal() as session:
        por_seccion = dict(
            session.execute(
                select(ProductRow.section, func.count())
                .where(*filtros)
                .group_by(ProductRow.section)
            ).all()
        )
        por_categoria = {}
        if section:
            por_categoria = dict(
                session.execute(
                    select(ProductRow.category, func.count())
                    .where(*filtros, ProductRow.section == section, ProductRow.category.is_not(None))
                    .group_by(ProductRow.category)
                ).all()
            )
    return por_seccion, por_categoria


# Clasificar en secciones los productos que no la tienen
def backfill_sections() -> int:
    """Asigna sección a las filas sin ella (catálogos de versiones anteriores o
    scrapers que no la calculan) con su categoría y su nombre. Devuelve cuántas."""
    with SessionLocal() as session, session.begin():
        rows = session.scalars(select(ProductRow).where(ProductRow.section.is_(None))).all()
        for row in rows:
            row.section = classify([row.category], row.name)
    return len(rows)
