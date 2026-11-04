import asyncio
import os
import unittest
from urllib.parse import parse_qs

os.environ.setdefault("SUPERMARKET_API_SCRAPE_TOKEN", "test-scrape-token")

import httpx  # noqa: E402

from app.scrapers import bonarea  # noqa: E402

TREE = {
    "articles": [],
    "nivells": [
        {
            "descripcio": "Alimentación",
            "children": [
                {
                    "descripcio": "Carnes y huevos",
                    "children": [
                        {
                            "descripcio": "Aves",
                            "children": [
                                {"descripcio": "Pavo", "identifier": "13*300*010*010*020"},
                            ],
                        },
                        {"descripcio": "Cerdo", "identifier": "13*300*010*060"},
                    ],
                }
            ],
        },
        {
            "descripcio": "Bricolaje",
            "children": [
                {"descripcio": "Adhesivos", "identifier": "13*070*010"},
            ],
        },
    ],
}

PAVO = {
    "articles": [
        {
            "identifier": "13*5179",
            "description": "Pechuga fileteada de pavo 5 und.",
            "priceToPay": 3.8,
            "unitPrice": "10,15 €/kg",
            "measurementUnit": "aprox. 374 g",
            "image": ["13_5179_1.png"],
            "urlFriendly": "https://www.bonarea-online.com/online/producto/pechuga-fileteada-de-pavo-5-und/13_5179",
            "isValid": True,
        },
        {
            "identifier": "13*9999",
            "description": "Artículo no válido",
            "priceToPay": 1.0,
            "isValid": False,
        },
    ]
}

CERDO = {
    "articles": [
        # Repetido en otra categoría: no debe duplicarse
        PAVO["articles"][0],
        {
            "identifier": "13*4479",
            "description": "Panceta de cerdo extrafina",
            "priceToPay": 4.5,
            "unitPrice": "1.234,50 €/kg",
            "image": [],
        },
    ]
}


class BonareaTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.references: list[str] = []

    def _handler(self, request: httpx.Request) -> httpx.Response:
        reference = parse_qs(request.content.decode())["reference"][0]
        self.references.append(reference)
        if reference == bonarea.ROOT_REFERENCE:
            return httpx.Response(200, json=TREE)
        if reference == "13_300_010_010_020":
            return httpx.Response(200, json=PAVO)
        if reference == "13_300_010_060":
            return httpx.Response(200, json=CERDO)
        return httpx.Response(500)

    def _scrape(self):
        async def scenario():
            transport = httpx.MockTransport(self._handler)
            async with httpx.AsyncClient(transport=transport) as client:
                return await bonarea.scrape(client)

        return asyncio.run(scenario())

    def test_scrape_downloads_only_grocery_leaf_categories(self) -> None:
        self._scrape()

        self.assertEqual(self.references[0], bonarea.ROOT_REFERENCE)
        self.assertCountEqual(
            self.references[1:], ["13_300_010_010_020", "13_300_010_060"]
        )

    def test_scrape_normalizes_and_deduplicates_products(self) -> None:
        products = {p.id: p for p in self._scrape()}

        self.assertEqual(set(products), {"13_5179", "13_4479"})

        pavo = products["13_5179"]
        self.assertEqual(pavo.supermarket, "bonarea")
        self.assertEqual(pavo.name, "Pechuga fileteada de pavo 5 und.")
        self.assertEqual(pavo.description, "aprox. 374 g")
        self.assertEqual(pavo.price, 3.8)
        self.assertEqual(pavo.price_per_unit, 10.15)
        self.assertEqual(pavo.unit, "kg")
        self.assertEqual(
            pavo.image_url, "https://images.bonarea.com/13_5179_1.png?width=400&height=400"
        )
        self.assertEqual(pavo.category, "Pavo")
        self.assertTrue(pavo.url.endswith("/13_5179"))

        panceta = products["13_4479"]
        self.assertEqual(panceta.price_per_unit, 1234.5)
        self.assertIsNone(panceta.image_url)

    def test_parse_unit_price(self) -> None:
        cases = {
            "10,15 €/kg": (10.15, "kg"),
            "0,99 €/l": (0.99, "l"),
            "3,80 €/u.": (3.8, "u"),
            "": (None, None),
            None: (None, None),
            "sin precio": (None, None),
        }
        for text, expected in cases.items():
            self.assertEqual(bonarea._parsear_precio_unidad(text), expected, text)


if __name__ == "__main__":
    unittest.main()
