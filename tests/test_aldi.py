import asyncio
import json
import os
import unittest

os.environ.setdefault("SUPERMARKET_API_SCRAPE_TOKEN", "test-scrape-token")

import httpx  # noqa: E402

from app.scrapers import aldi  # noqa: E402

SITEMAP = """<?xml version="1.0"?>
<urlset>
  <url><loc>https://www.aldi.es/productos/despensa.html</loc></url>
  <url><loc>https://www.aldi.es/productos/despensa/aceites-y-vinagres.html</loc></url>
  <url><loc>https://www.aldi.es/productos/lacteos-y-huevos/leche.html</loc></url>
  <url><loc>https://www.aldi.es/productos/marcas/milsani.html</loc></url>
  <url><loc>https://www.aldi.es/productos/juguetes.html</loc></url>
  <url><loc>https://www.aldi.es/folleto.html</loc></url>
</urlset>"""

OIL = {
    "objectID": "662500",
    "name": "Aceite de oliva virgen extra",
    "brandName": "OLEARIA DEL OLIVAR®",
    "salesUnit": "3 l unidad",
    "isAvailable": True,
    "isComingSoon": False,
    "currentPrice": {"priceValue": 12.15, "basePrice": [{"basePriceValue": 4.05, "basePriceScale": "l"}]},
    "assets": [{"type": "primary", "url": "https://s7g10.scene7.com/is/image/aldinord/6625"}],
    "hierarchicalCategories": {"lvl1": ["Despensa > Aceites y vinagres"]},
    "productSlug": "aceite-de-oliva-virgen-extra-662500",
}

VINEGAR_OFFER = {
    "objectID": "655400",
    "name": "Vinagre balsámico",
    "isAvailable": True,
    "currentPrice": {
        "priceValue": 3.99,
        "strikePrice": {"strikePriceValue": 5.75},
        "priceTagLabels": {"promoText1": "-30%"},
    },
    "assets": [],
    "mainCategoryID": "aceites-y-vinagres",
}

MILK_OFFER_NO_LABEL = {
    "objectID": "100100",
    "name": "Leche entera",
    "isAvailable": True,
    "currentPrice": {"priceValue": 0.89, "strikePrice": {"strikePriceValue": 0.99}},
}

COMING_SOON = {"objectID": "999", "name": "Turrón", "isComingSoon": True, "currentPrice": {"priceValue": 2}}


def _page(hits: list[dict] | None) -> str:
    state = None if hits is None else {"initialResults": {"idx": {"results": [{"hits": hits, "nbHits": len(hits)}]}}}
    data = {"props": {"pageProps": {"algoliaState": state}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'


PAGES = {
    "/productos/despensa.html": _page(None),
    "/productos/despensa/aceites-y-vinagres.html": _page([OIL, VINEGAR_OFFER, COMING_SOON]),
    "/productos/lacteos-y-huevos/leche.html": _page([MILK_OFFER_NO_LABEL, OIL]),
}


class AldiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.paths: list[str] = []

    def _handler(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        if request.url.path.endswith("sitemap-pages.xml"):
            return httpx.Response(200, text=SITEMAP)
        if request.url.path in PAGES:
            return httpx.Response(200, text=PAGES[request.url.path])
        return httpx.Response(404)

    def _scrape(self):
        async def scenario():
            async with httpx.AsyncClient(transport=httpx.MockTransport(self._handler)) as client:
                return await aldi.scrape(client)

        return {p.id: p for p in asyncio.run(scenario())}

    def test_only_grocery_category_pages_are_downloaded(self) -> None:
        self._scrape()
        self.assertCountEqual(self.paths[1:], list(PAGES))

    def test_products_are_normalized_and_deduplicated(self) -> None:
        products = self._scrape()

        self.assertEqual(set(products), {"662500", "655400", "100100"})
        oil = products["662500"]
        self.assertEqual(oil.supermarket, "aldi")
        self.assertEqual(oil.brand, "OLEARIA DEL OLIVAR")
        self.assertEqual(oil.description, "3 l unidad")
        self.assertEqual(oil.price, 12.15)
        self.assertEqual(oil.price_per_unit, 4.05)
        self.assertEqual(oil.unit, "l")
        self.assertIsNone(oil.previous_price)
        self.assertIsNone(oil.promotion)
        self.assertEqual(oil.image_url, "https://s7g10.scene7.com/is/image/aldinord/6625?wid=400&hei=400")
        self.assertEqual(oil.category, "Aceites y vinagres")
        self.assertEqual(oil.url, "https://www.aldi.es/producto/aceite-de-oliva-virgen-extra-662500.html")

    def test_offers_keep_strike_price_and_label(self) -> None:
        products = self._scrape()

        vinegar = products["655400"]
        self.assertEqual((vinegar.price, vinegar.previous_price, vinegar.promotion), (3.99, 5.75, "-30%"))
        self.assertIsNone(vinegar.image_url)
        self.assertEqual(vinegar.category, "aceites-y-vinagres")

        milk = products["100100"]
        self.assertEqual((milk.previous_price, milk.promotion), (0.99, "Oferta"))

    def test_multibuy_offers_keep_the_single_unit_price(self) -> None:
        for label, price, strike in (("2ª ud al -50%", 0.84, 1.69), ("4x1 €", 0.25, 0.32)):
            product = aldi._parsear_hit(
                {
                    "objectID": "1",
                    "name": "Bebida energética",
                    "currentPrice": {
                        "priceValue": price,
                        "strikePrice": {"strikePriceValue": strike},
                        "priceTagLabels": {"promoText1": label},
                    },
                }
            )
            self.assertEqual((product.price, product.previous_price, product.promotion), (strike, None, label))

    def test_page_without_next_data_returns_nothing(self) -> None:
        self.assertEqual(aldi._parsear_pagina("<html></html>"), [])
        self.assertEqual(aldi._parsear_pagina('<script id="__NEXT_DATA__">{bad json</script>'), [])


if __name__ == "__main__":
    unittest.main()
