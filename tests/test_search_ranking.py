import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

os.environ.setdefault("SUPERMARKET_API_SCRAPE_TOKEN", "test-scrape-token")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.database import Base  # noqa: E402
from app.models import backup, product  # noqa: E402,F401
from app.models.product import ProductRow  # noqa: E402
from app.repositories import product_repository  # noqa: E402

PRODUCTS = [
    ("1", "mercadona", "Chocolate con leche Hacendado almendras enteras", 2.5, 16.667, "Chocolate"),
    ("2", "mercadona", "Leche entera Hacendado", 0.95, 0.95, "Leche"),
    ("3", "bonarea", "Leche entera", 1.77, 1.18, "Leche"),
    ("4", "bonarea", "Leche entera ATO paq. de 6 brics", 7.68, 1.28, "Leche"),
    ("5", "mercadona", "Bebida de avena", 1.4, None, "Leche entera y bebidas"),
    ("6", "bonarea", "Yogur natural", None, None, "Yogures"),
]


class SearchRankingTestCase(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        now = datetime.now(timezone.utc)
        with session_factory() as session, session.begin():
            session.add_all(
                ProductRow(
                    external_id=pid,
                    supermarket=sm,
                    name=name,
                    price=price,
                    price_per_unit=ppu,
                    category=category,
                    scraped_at=now,
                )
                for pid, sm, name, price, ppu, category in PRODUCTS
            )
        patcher = patch.object(product_repository, "SessionLocal", session_factory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _ids(self, q=None, sort="relevance", supermarket=None):
        _, items = product_repository.search_products(supermarket, q, None, 1, 50, sort)
        return [p.id for p in items]

    def test_relevance_puts_exact_name_matches_first(self) -> None:
        ids = self._ids("leche entera")

        # "Leche entera" starts with the phrase and is the shortest
        self.assertEqual(ids[0], "3")
        self.assertEqual(set(ids[:3]), {"2", "3", "4"})
        # Chocolate (words scattered) and avena (match only in category) go last
        self.assertEqual(set(ids[3:]), {"1", "5"})
        self.assertEqual(ids[-1], "5")

    def test_relevance_ignores_accents_and_case(self) -> None:
        self.assertEqual(self._ids("LÉCHE ENTERA")[0], "3")

    def test_sort_by_price_puts_missing_prices_last(self) -> None:
        ids = self._ids(sort="price")
        self.assertEqual(ids[0], "2")
        self.assertEqual(ids[-1], "6")

    def test_sort_by_price_per_unit(self) -> None:
        self.assertEqual(self._ids("leche entera", sort="price_per_unit")[:3], ["2", "3", "4"])

    def test_filter_by_supermarket_keeps_ranking(self) -> None:
        self.assertEqual(self._ids("leche entera", supermarket="bonarea"), ["3", "4"])


if __name__ == "__main__":
    unittest.main()
