import asyncio
import os
import unittest

os.environ.setdefault("SUPERMARKET_API_SCRAPE_TOKEN", "test-scrape-token")

import httpx  # noqa: E402

from app.scrapers import mercadona  # noqa: E402

CATEGORY_TREE = {"results": [{"id": 1, "categories": [{"id": 420}]}]}

CATEGORY_420 = {
    "id": 420,
    "name": "Aceite de oliva",
    "categories": [
        {
            "products": [
                {
                    "id": "4241",
                    "display_name": "Aceite de oliva 0,4º Hacendado",
                    "packaging": "Garrafa",
                    "thumbnail": "https://prod-mercadona.imgix.net/images/4241.jpg",
                    "share_url": "https://tienda.mercadona.es/product/4241/",
                    "price_instructions": {
                        "unit_price": "19.75",
                        "reference_price": "3.950",
                        "reference_format": "L",
                        "unit_size": 5,
                        "size_format": "l",
                    },
                }
            ]
        }
    ],
}


def _run(coroutine):
    return asyncio.run(coroutine)


class MercadonaPostcodeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.requests: list[httpx.Request] = []

    def _client(self, handler) -> httpx.AsyncClient:
        def recording_handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return handler(request)

        return httpx.AsyncClient(transport=httpx.MockTransport(recording_handler))

    def test_resolve_warehouse_reads_header(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, headers={"x-customer-wh": "bcn1"}, json={"warehouse_changed": False}
            )

        async def scenario():
            async with self._client(handler) as client:
                return await mercadona.resolve_warehouse(client, "08001")

        self.assertEqual(_run(scenario()), "bcn1")
        request = self.requests[0]
        self.assertEqual(request.method, "POST")
        self.assertTrue(str(request.url).endswith("/postal-codes/actions/change-pc/"))
        self.assertEqual(request.content, b'{"new_postal_code":"08001"}')

    def test_resolve_warehouse_raises_when_postcode_not_served(self) -> None:
        async def scenario():
            async with self._client(lambda _: httpx.Response(404)) as client:
                await mercadona.resolve_warehouse(client, "99999")

        with self.assertRaises(mercadona.PostcodeNotServedError):
            _run(scenario())

    def test_resolve_warehouse_raises_without_header(self) -> None:
        async def scenario():
            async with self._client(lambda _: httpx.Response(200, json={})) as client:
                await mercadona.resolve_warehouse(client, "08001")

        with self.assertRaises(mercadona.PostcodeNotServedError):
            _run(scenario())

    def test_scrape_sends_warehouse_on_every_catalog_request(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(200, headers={"x-customer-wh": "mad3"})
            if request.url.path.endswith("/categories/"):
                return httpx.Response(200, json=CATEGORY_TREE)
            return httpx.Response(200, json=CATEGORY_420)

        async def scenario():
            async with self._client(handler) as client:
                return await mercadona.scrape(client, "28001")

        products = _run(scenario())

        catalog_requests = [r for r in self.requests if r.method == "GET"]
        self.assertEqual(len(catalog_requests), 2)
        for request in catalog_requests:
            self.assertEqual(request.url.params["wh"], "mad3")
            self.assertEqual(request.url.params["lang"], "es")

        self.assertEqual(len(products), 1)
        product = products[0]
        self.assertEqual(product.id, "4241")
        self.assertEqual(product.price, 19.75)
        self.assertEqual(product.price_per_unit, 3.95)
        self.assertEqual(product.unit, "L")
        self.assertEqual(product.description, "Garrafa 5 l")
        self.assertEqual(product.image_url, "https://prod-mercadona.imgix.net/images/4241.jpg")


if __name__ == "__main__":
    unittest.main()
