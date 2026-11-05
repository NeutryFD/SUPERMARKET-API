import asyncio
import html
import json
import os
import unittest

os.environ.setdefault("SUPERMARKET_API_SCRAPE_TOKEN", "test-scrape-token")

import httpx  # noqa: E402

from app.scrapers import lidl  # noqa: E402

FOOD_PATH = "Mundos de necesidad/Comida y cerca de la comida"


def _product(product_id, title, category="Food", path="Quesos, productos lácteos y huevos/Yogur", **region):
    return {
        "productId": product_id,
        "fullTitle": title,
        "category": category,
        "brand": {"name": "MILBONA"},
        "image": f"https://www.lidl.es/assets/{product_id}.png",
        "canonicalUrl": f"/p/producto/p{product_id}",
        "keyfacts": {"description": "<ul><li>Categoría I.</li></ul>", "wonCategoryPrimary": f"{FOOD_PATH}/{path}"},
        "regionsPrices": {"1": region},
    }


def _price(price, old=None, packaging="1 kg", end="2026-11-08T22:59:59Z"):
    data = {"price": price, "packaging": {"text": packaging}, "endDate": end}
    if old:
        data["oldPrice"] = old
    return data


YOGURT = _product(1, "MILBONA Griego de fresa", currentPrice=_price(1.49, 1.59, "6x125 g"))
MANDARIN = _product(
    2,
    "Mandarina nacional 1 kg malla",
    category="F+V",
    path="Frutas y hortalizas/Fruta",
    currentLidlPlusPrice={"price": _price(1.59, 2.19), "lidlPlusText": "Con Lidl Plus"},
)
SALMON = _product(3, "Lomos de salmón", path="Pescado y marisco/Pescado", currentPrice=_price(5.89, packaging="2 uds"))
NEXT_WEEK = _product(4, "Turrón de Jijona", futurePrices=[{"price": _price(2.99)}])
PLANT = _product(5, "Flor de Pascua", category="P+F", currentPrice=_price(4.99, packaging="1 ud"))


def _page(products: list[dict], links: list[str] = ()) -> str:
    anchors = "".join(f'<a href="{link}">x</a>' for link in links)
    grids = "".join(f'<div data-grid-data="{html.escape(json.dumps(p))}"></div>' for p in products)
    return f"<html>{anchors}{grids}</html>"


PAGES = {
    "/c/store/ofertas-semanales/a10089449": _page(
        [YOGURT, MANDARIN, NEXT_WEEK, PLANT],
        [
            "/c/store/ofertas-semanales/a10089449",
            "/c/store/super-finde/a10089450",
            "/c/store/ofertas-proxima-semana/a10088432",
            "/c/cocina/a10098908",
        ],
    ),
    "/c/store/super-finde/a10089450": _page([SALMON, YOGURT]),
}


class LidlTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.paths: list[str] = []
        self.pages = dict(PAGES)

    def _handler(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        if request.url.path in self.pages:
            return httpx.Response(200, text=self.pages[request.url.path])
        return httpx.Response(404)

    def _scrape(self):
        async def scenario():
            async with httpx.AsyncClient(transport=httpx.MockTransport(self._handler)) as client:
                return await lidl.scrape(client)

        return {p.id: p for p in asyncio.run(scenario())}

    def test_only_this_week_store_offer_pages_are_downloaded(self) -> None:
        self._scrape()
        self.assertCountEqual(self.paths, list(PAGES))

    def test_current_food_offers_are_normalized_and_deduplicated(self) -> None:
        products = self._scrape()

        self.assertEqual(set(products), {"1", "2", "3"})
        yogurt = products["1"]
        self.assertEqual(yogurt.supermarket, "lidl")
        self.assertEqual(yogurt.brand, "MILBONA")
        self.assertEqual((yogurt.price, yogurt.previous_price), (1.49, 1.59))
        self.assertEqual((yogurt.price_per_unit, yogurt.unit), (1.99, "kg"))
        self.assertEqual(yogurt.promotion, "-6% · hasta el 8/11")
        self.assertEqual(yogurt.category, "Yogur")
        self.assertEqual(yogurt.section, "lacteos-huevos")
        self.assertEqual(yogurt.description, "Categoría I.")
        self.assertEqual(yogurt.image_url, "https://www.lidl.es/assets/1.png")
        self.assertEqual(yogurt.url, "https://www.lidl.es/p/producto/p1")

    def test_lidl_plus_price_is_labelled(self) -> None:
        mandarin = self._scrape()["2"]
        self.assertEqual((mandarin.price, mandarin.previous_price), (1.59, 2.19))
        self.assertEqual(mandarin.promotion, "Con Lidl Plus -27% · hasta el 8/11")
        self.assertEqual(mandarin.section, "fruta-verdura")

    def test_offer_without_previous_price_or_weight(self) -> None:
        salmon = self._scrape()["3"]
        self.assertIsNone(salmon.previous_price)
        self.assertEqual((salmon.price_per_unit, salmon.unit), (None, None))
        self.assertEqual(salmon.promotion, "Oferta · hasta el 8/11")

    def test_no_offers_fails_instead_of_emptying_the_catalog(self) -> None:
        self.pages = {"/c/store/ofertas-semanales/a10089449": "<html></html>"}
        with self.assertRaises(RuntimeError):
            self._scrape()

    def test_bad_grid_data_is_ignored(self) -> None:
        self.assertEqual(lidl._parsear_pagina('<div data-grid-data="{bad json"></div>'), [])


if __name__ == "__main__":
    unittest.main()
