import asyncio
import os
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

os.environ.setdefault("SUPERMARKET_API_SCRAPE_TOKEN", "test-scrape-token")

import httpx  # noqa: E402

from app.scrapers import ametller  # noqa: E402

CATEGORIES = {
    "id": "root",
    "categories": [
        {"id": "PROMOCIONS", "name": "Promociones"},
        {
            "id": "6",
            "name": "Lácteos y huevos",
            "categories": [{"id": "60", "name": "Yogures", "categories": [{"id": "601", "name": "Griegos"}]}],
        },
        {"id": "1", "name": "Fruta", "categories": [{"id": "17", "name": "Frutas tropicales"}]},
        {"id": "3", "name": "Carne y Charcutería", "categories": [{"id": "30", "name": "Pollo"}]},
    ],
}


def _hit(product_id, name, price, category, promotions=None, unit="KG", per_unit=None):
    hit = {
        "productId": product_id,
        "productName": name,
        "price": price,
        "pricePerUnit": per_unit,
        "image": {"link": f"https://www.ametllerorigen.com/images/{product_id}.jpg"},
        "c_instaleapHit": {"unit": unit, "attributes": {"category": category}, "barcodes": [f"84{product_id}"]},
    }
    if promotions:
        hit["productPromotions"] = promotions
    return hit


def _callout(text):
    return f'<p style="background-color: #DB5A3F; color: white;">{text}</p>'


YOGURT = _hit("601", "Yogur griego Ametller Origen 125g - 4uds.", 2.49, "601", per_unit=4.98)
PITAHAYA = _hit("328", "Pitahaya roja unidad", 3.49, "17", [{"calloutMsg": _callout("2x6€"), "promotionId": "1"}])
FLAN = _hit("700", "Flan de huevo 110g - 4uds.", 2.50, "601", [{"calloutMsg": _callout("-20%"), "promotionalPrice": 2.0}], per_unit=5.0)
CHICKEN = _hit(
    "300",
    "Alas de pollo adobadas - 8u.",
    4.0,
    "30",
    [{"calloutMsg": _callout("PRECIO CLUB"), "promotionalPrice": 3.0}],
    unit="ST",
)
LOT = _hit("900", "Lote Brunch", 10.0, "30", [{"promotionalPrice": 9.0, "promotionId": "lotes_pricebook"}], unit="")
NO_PRICE = {"productId": "1", "productName": "Sin precio"}

PAGES = [[YOGURT, PITAHAYA, FLAN], [CHICKEN, LOT, YOGURT, NO_PRICE]]


class AmetllerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.searches: list[dict] = []
        self.authorization: list[str | None] = []

    def _handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/oauth2/authorize"):
            self.assertEqual(request.url.params["hint"], "guest")
            return httpx.Response(303, headers={"location": f"{ametller.REDIRECT_URI}?usid=u1&code=abc"})
        if path.endswith("/oauth2/token"):
            form = parse_qs(request.content.decode())
            self.assertEqual(form["code"], ["abc"])
            self.assertEqual(form["grant_type"], ["authorization_code_pkce"])
            return httpx.Response(200, json={"access_token": "guest-token"})
        self.authorization.append(request.headers.get("authorization"))
        if path.endswith("/categories/root"):
            return httpx.Response(200, json=CATEGORIES)
        if path.endswith("/product-search"):
            params = dict(request.url.params)
            self.searches.append(params)
            page = int(params["offset"]) // ametller.PAGE_SIZE
            return httpx.Response(200, json={"total": 7, "hits": PAGES[page] if page < len(PAGES) else []})
        return httpx.Response(404)

    def _scrape(self):
        async def scenario():
            async with httpx.AsyncClient(transport=httpx.MockTransport(self._handler)) as client:
                return await ametller.scrape(client)

        return {p.id: p for p in asyncio.run(scenario())}

    def test_guest_token_is_used_for_every_api_call(self) -> None:
        self._scrape()
        self.assertTrue(self.authorization)
        self.assertEqual(set(self.authorization), {"Bearer guest-token"})

    def test_whole_catalog_is_paged(self) -> None:
        with patch.object(ametller, "PAGE_SIZE", 3):
            products = self._scrape()

        self.assertEqual([s["offset"] for s in self.searches], ["0", "3", "6"])
        self.assertTrue(all(s["refine"] == "cgid=root" for s in self.searches))
        self.assertEqual(set(products), {"601", "328", "700", "300", "900"})

    def test_products_are_normalized(self) -> None:
        with patch.object(ametller, "PAGE_SIZE", 3):
            yogurt = self._scrape()["601"]

        self.assertEqual(yogurt.supermarket, "ametller")
        self.assertEqual((yogurt.price, yogurt.price_per_unit, yogurt.unit), (2.49, 4.98, "kg"))
        self.assertIsNone(yogurt.previous_price)
        self.assertIsNone(yogurt.promotion)
        self.assertEqual(yogurt.category, "Griegos")
        self.assertEqual(yogurt.section, "lacteos-huevos")
        self.assertEqual(yogurt.ean, "84601")
        self.assertEqual(yogurt.image_url, "https://www.ametllerorigen.com/images/601.jpg")
        self.assertEqual(yogurt.url, "https://www.ametllerorigen.com/es/p/601.html")

    def test_promotions(self) -> None:
        with patch.object(ametller, "PAGE_SIZE", 3):
            products = self._scrape()

        # Solo anunciada: el precio no cambia
        pitahaya = products["328"]
        self.assertEqual((pitahaya.price, pitahaya.previous_price, pitahaya.promotion), (3.49, None, "2x6€"))
        # Rebaja directa: precio rebajado y el precio por unidad recalculado
        flan = products["700"]
        self.assertEqual((flan.price, flan.previous_price, flan.promotion), (2.0, 2.5, "-20%"))
        self.assertEqual(flan.price_per_unit, 4.0)
        # Precio club: se completa con el descuento
        chicken = products["300"]
        self.assertEqual((chicken.price, chicken.previous_price, chicken.promotion), (3.0, 4.0, "PRECIO CLUB -25%"))
        self.assertEqual(chicken.unit, "ud")
        # Lote sin texto: solo el descuento
        lot = products["900"]
        self.assertEqual((lot.price, lot.previous_price, lot.promotion, lot.unit), (9.0, 10.0, "-10%", None))

    def test_section_uses_the_most_specific_category_first(self) -> None:
        with patch.object(ametller, "PAGE_SIZE", 3):
            products = self._scrape()

        # "Carne y Charcutería" sería charcutería; "Pollo" decide que es carne
        self.assertEqual(products["300"].section, "carne")
        self.assertEqual(ametller._seccion(["Veggie-productos", "Heura y tofu"], "Leche de avena"), "lacteos-huevos")
        self.assertEqual(ametller._seccion([], "Pitahaya"), "otros")

    def test_promotional_price_not_lower_is_ignored(self) -> None:
        price, previous, label = ametller._promocion(
            3.49, [{"calloutMsg": _callout("PRECIO CLUB"), "promotionalPrice": 3.49}]
        )
        self.assertEqual((price, previous, label), (3.49, None, "PRECIO CLUB"))

    def test_empty_catalog_fails_instead_of_wiping_stored_products(self) -> None:
        global PAGES
        saved, PAGES = PAGES, []
        try:
            with self.assertRaises(RuntimeError):
                self._scrape()
        finally:
            PAGES = saved

    def test_rejected_guest_login_fails(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"message": "invalid client"})

        async def scenario():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await ametller.scrape(client)

        with self.assertRaises(RuntimeError):
            asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
