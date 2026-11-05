"""
Servicio de catálogo
"""

from math import ceil
from app.repositories import product_repository
from app.schemas.product import ProductPage, SectionCategory, SectionInfo, SupermarketInfo
from app.sections import SECTIONS
from app.scrapers import SUPERMARKETS


def get_supermarkets(name: str | None = None) -> list[SupermarketInfo]:
    """Estado del catálogo de cada supermercado
    Con name, solo los supermercados cuyo nombre lo contenga"""
    nombres = SUPERMARKETS
    if name:
        nombres = [s for s in nombres if name.lower() in s]
    return [
        SupermarketInfo(
            name=s,
            product_count=product_repository.count_products(s),
            last_scraped_at=product_repository.last_scraped_at(s),
        )
        for s in nombres
    ]


def get_categories(supermarket: str | None = None) -> list[str]:
    """Categorías disponibles en todo el catálogo o en un supermercado."""
    return product_repository.get_categories(supermarket)


def get_products(
    supermarket: str | None,
    q: str | None,
    category: str | None,
    page: int,
    page_size: int,
    sort: str = "relevance",
    promotion: bool = False,
    section: str | None = None,
    subcategory: str | None = None,
) -> ProductPage:
    """Búsqueda paginada de productos"""
    total, items = product_repository.search_products(
        supermarket, q, category, page, page_size, sort, promotion, section, subcategory
    )
    return ProductPage(
        total=total,
        page=page,
        page_size=page_size,
        pages=max(ceil(total / page_size), 1),
        items=items,
    )


def get_sections(
    supermarket: str | None,
    q: str | None,
    promotion: bool = False,
    section: str | None = None,
) -> list[SectionInfo]:
    """Todas las secciones comunes, en su orden, con sus productos para los filtros dados.
    La sección pedida incluye sus categorías de tienda, de más a menos productos"""
    por_seccion, por_categoria = product_repository.count_sections(supermarket, q, promotion, section)
    categorias = [
        SectionCategory(name=nombre, count=total)
        for nombre, total in sorted(por_categoria.items(), key=lambda c: (-c[1], c[0]))
    ]
    return [
        SectionInfo(
            slug=slug,
            name=nombre,
            count=por_seccion.get(slug, 0),
            categories=categorias if slug == section else [],
        )
        for slug, nombre in SECTIONS
    ]
