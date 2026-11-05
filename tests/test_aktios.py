import os
import unittest

os.environ.setdefault("SUPERMARKET_API_SCRAPE_TOKEN", "test-scrape-token")

from app.scrapers import aktios  # noqa: E402


def _product(prices: dict[str, float], offers: list[dict] | None = None) -> dict:
    return {
        "code": "1",
        "productData": {"name": "Lentejas a la riojana", "brand": {"name": "Consum"}},
        "priceData": {
            "prices": [{"id": k, "value": {"centAmount": v, "centUnitAmount": v * 2}} for k, v in prices.items()],
            "unitPriceUnitType": "1 Kg",
        },
        "offers": offers or [],
        "media": [{"url": "https://cdn-consum.aktiosdigitalservices.com/1.jpg", "order": 0}],
    }


class AktiosPromotionTestCase(unittest.TestCase):
    def test_offer_price_is_a_discount_with_previous_price(self) -> None:
        p = aktios._parsear_producto(
            _product({"PRICE": 2.35, "OFFER_PRICE": 2.0}, [{"shortDescription": "Ahora más barato"}]), "consum"
        )
        self.assertEqual((p.price, p.previous_price, p.promotion), (2.0, 2.35, "-15%"))
        self.assertEqual(p.price_per_unit, 4.0)

    def test_pack_offer_keeps_regular_price_and_uses_its_text(self) -> None:
        p = aktios._parsear_producto(
            _product({"PRICE": 0.99}, [{"shortDescription": "Oferta Pack <b>8 Ud</b>  X 0,75€/Ud"}]), "masymas"
        )
        self.assertEqual((p.price, p.previous_price, p.promotion), (0.99, None, "Oferta Pack 8 Ud X 0,75€/Ud"))

    def test_no_offer_is_not_a_promotion(self) -> None:
        p = aktios._parsear_producto(_product({"PRICE": 2.35}), "consum")
        self.assertEqual((p.price, p.previous_price, p.promotion), (2.35, None, None))

    def test_offer_price_not_lower_is_ignored(self) -> None:
        p = aktios._parsear_producto(_product({"PRICE": 2.0, "OFFER_PRICE": 2.0}), "consum")
        self.assertEqual((p.previous_price, p.promotion), (None, None))


class AktiosSectionTestCase(unittest.TestCase):
    def test_uses_the_menu_path_and_skips_offer_categories(self) -> None:
        product = _product({"PRICE": 2.35})
        product["categories"] = [
            {"id": 900, "name": "Ofertas en despensa"},
            {"id": 1933, "name": "Té"},
        ]
        rutas = {
            900: ["Ahora más barato", "Ofertas en despensa"],
            1933: ["Despensa", "Desayuno, dulces y café", "Infusiones", "Té"],
        }

        p = aktios._parsear_producto(product, "consum", rutas)

        self.assertEqual((p.category, p.section), ("Té", "desayuno-dulces"))

    def test_without_menu_falls_back_to_the_first_category(self) -> None:
        product = _product({"PRICE": 1.0})
        product["categories"] = [{"id": 1, "name": "Yogures"}]
        p = aktios._parsear_producto(product, "masymas")
        self.assertEqual((p.category, p.section), ("Yogures", "lacteos-huevos"))


if __name__ == "__main__":
    unittest.main()
