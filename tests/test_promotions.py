import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

os.environ.setdefault("SUPERMARKET_API_SCRAPE_TOKEN", "test-scrape-token")

from sqlalchemy import create_engine, inspect, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.database import Base, add_missing_columns  # noqa: E402
from app.models import backup, product  # noqa: E402,F401
from app.models.product import ProductRow  # noqa: E402
from app.repositories import product_repository  # noqa: E402
from app.scrapers import bonarea, mercadona  # noqa: E402


def _mercadona_category(price: str, previous: str | None) -> dict:
    return {
        "name": "Aceite de oliva",
        "categories": [
            {
                "products": [
                    {
                        "id": "4241",
                        "display_name": "Aceite de oliva 0,4º Hacendado",
                        "price_instructions": {"unit_price": price, "previous_unit_price": previous},
                    }
                ]
            }
        ],
    }


class MercadonaPromotionTestCase(unittest.TestCase):
    def test_price_drop_keeps_previous_price(self) -> None:
        [p] = mercadona._parsear_categoria(_mercadona_category("16.75", "       17.25"))
        self.assertEqual(p.price, 16.75)
        self.assertEqual(p.previous_price, 17.25)
        self.assertEqual(p.promotion, "Bajada de precio")

    def test_price_rise_or_no_previous_is_not_a_promotion(self) -> None:
        for previous in ("15.00", None, "   "):
            [p] = mercadona._parsear_categoria(_mercadona_category("16.75", previous))
            self.assertIsNone(p.previous_price, previous)
            self.assertIsNone(p.promotion, previous)


class BonareaPromotionTestCase(unittest.TestCase):
    def _article(self, discount=0, tags=()):
        return {
            "identifier": "13*1",
            "description": "Butifarra",
            "priceToPay": 5.9,
            "discount": discount,
            "caracteristiques": [{"descripcio": t} for t in ("BONAREA", *tags)],
        }

    def test_promotion_labels(self) -> None:
        cases = [
            (self._article(discount=20), "-20%"),
            (self._article(tags=("BAIXADA_PREU",)), "Bajada de precio"),
            (self._article(tags=("PREU_IMBATIBLE",)), "Precio imbatible"),
            (self._article(tags=("BAIXADA_PREU", "SUPERPREU")), "Superprecio"),
            (self._article(), None),
        ]
        for article, expected in cases:
            [p] = bonarea._parsear_articulos([article], "Embutidos")
            self.assertEqual(p.promotion, expected)
            self.assertIsNone(p.previous_price)


class MigrationTestCase(unittest.TestCase):
    def test_adds_new_columns_to_an_existing_products_table(self) -> None:
        engine = create_engine("sqlite://", poolclass=StaticPool)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE products (pk INTEGER PRIMARY KEY, external_id VARCHAR(64), "
                    "supermarket VARCHAR(32), name VARCHAR(512), description TEXT, brand VARCHAR(255), "
                    "price FLOAT, price_per_unit FLOAT, unit VARCHAR(64), image_url TEXT, "
                    "category VARCHAR(255), ean VARCHAR(32), url TEXT, scraped_at DATETIME)"
                )
            )
            connection.execute(text("INSERT INTO products (external_id, supermarket, name) VALUES ('1', 'bonarea', 'Pan')"))

        add_missing_columns(engine)
        add_missing_columns(engine)  # idempotente

        columns = {c["name"] for c in inspect(engine).get_columns("products")}
        self.assertTrue({"previous_price", "promotion"} <= columns)
        indexes = {i["name"] for i in inspect(engine).get_indexes("products")}
        self.assertIn("ix_products_promotion", indexes)
        with engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT name FROM products")).scalar(), "Pan")


class PromotionSearchTestCase(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        now = datetime.now(timezone.utc)
        rows = [
            ("1", "mercadona", "Aceite", 16.75, 17.25, "Bajada de precio"),  # -2,9 %
            ("2", "mercadona", "Atún", 3.0, 4.0, "Bajada de precio"),  # -25 %
            ("3", "bonarea", "Butifarra", 5.9, None, "Bajada de precio"),
            ("4", "bonarea", "Pan", 1.1, None, None),
        ]
        with factory() as session, session.begin():
            session.add_all(
                ProductRow(
                    external_id=pid, supermarket=sm, name=name, price=price,
                    previous_price=previous, promotion=promo, scraped_at=now,
                )
                for pid, sm, name, price, previous, promo in rows
            )
        patcher = patch.object(product_repository, "SessionLocal", factory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_promotion_filter_and_discount_sort(self) -> None:
        total, items = product_repository.search_products(None, None, None, 1, 50, "discount", True)
        self.assertEqual(total, 3)
        self.assertEqual([p.id for p in items], ["2", "1", "3"])
        self.assertEqual(items[0].previous_price, 4.0)

    def test_promotion_filter_by_supermarket(self) -> None:
        total, items = product_repository.search_products("bonarea", None, None, 1, 50, "discount", True)
        self.assertEqual([p.id for p in items], ["3"])


if __name__ == "__main__":
    unittest.main()
