"""
Capa de scrapers
"""

from app.core.config import settings
from app.schemas.product import Product
from app.scrapers import aktios, lidl, mercadona
from app.scrapers.http import new_client

ALL_SUPERMARKETS = ["mercadona", "consum", "masymas", "lidl"]

# Solo los supermercados activados en la configuración se pueden scrapear
SUPERMARKETS = [s for s in ALL_SUPERMARKETS if s in settings.enabled_supermarkets]

# El código postal solo lo usa Mercadona; el resto lo ignora
async def scrape(supermarket: str, postcode: str) -> list[Product]:
    async with new_client() as client:
        if supermarket == "mercadona":
            return await mercadona.scrape(client, postcode)
        if supermarket == "consum":
            return await aktios.scrape(client, "consum", "https://tienda.consum.es")
        if supermarket == "masymas":
            return await aktios.scrape(client, "masymas", "https://tienda.masymas.com")
        if supermarket == "lidl":
            return await lidl.scrape(client)
        raise ValueError(f"Supermarket not found: {supermarket}")
