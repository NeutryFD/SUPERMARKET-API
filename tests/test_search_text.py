import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

os.environ.setdefault("SUPERMARKET_API_SCRAPE_TOKEN", "test-scrape-token")

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.database import Base  # noqa: E402
from app.core.text import normalize, search_text  # noqa: E402
from app.models import backup, product  # noqa: E402,F401
from app.models.product import ProductRow  # noqa: E402
from app.repositories import product_repository  # noqa: E402


class NormalizeTestCase(unittest.TestCase):
    def test_lowercase_without_accents(self) -> None:
        self.assertEqual(normalize("Café Ñora CRÈME"), "cafe nora creme")
        self.assertEqual(search_text("Leche", None, "Asturiana", "Leche y bebidas", "8410"), "leche asturiana leche y bebidas 8410")


class SearchColumnsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.engine = engine
        self.factory = sessionmaker(bind=engine, expire_on_commit=False)
        patcher = patch.object(product_repository, "SessionLocal", self.factory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _search(self, q):
        return [p.name for p in product_repository.search_products(None, q, None, 1, 50)[1]]

    def test_columns_are_filled_when_saving_and_searched(self) -> None:
        product_repository.save_catalog(
            "mercadona",
            [
                product.Product(id="1", supermarket="mercadona", name="Café molido", brand="Hacendado", category="Café"),
                product.Product(id="2", supermarket="mercadona", name="Leche entera", category="Leche y bebidas vegetales"),
            ],
        )
        with self.factory() as session:
            row = session.query(ProductRow).filter_by(external_id="1").one()
        self.assertEqual(row.name_search, "cafe molido")
        self.assertEqual(row.search_text, "cafe molido hacendado cafe")

        self.assertEqual(self._search("CAFÉ"), ["Café molido"])
        self.assertEqual(self._search("hacendado cafe"), ["Café molido"])
        self.assertEqual(self._search("bebidas leche"), ["Leche entera"])
        self.assertEqual(self._search("zumo"), [])

    def test_backfill_fills_rows_of_older_versions(self) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO products (external_id, supermarket, name, category, scraped_at) "
                    "VALUES ('9', 'aldi', 'Turrón de Jijona', 'Turrones', :now)"
                ),
                {"now": datetime.now(timezone.utc)},
            )
        self.assertEqual(self._search("turron"), [])

        self.assertEqual(product_repository.backfill_search_columns(), 1)
        self.assertEqual(self._search("turron"), ["Turrón de Jijona"])
        self.assertEqual(product_repository.backfill_search_columns(), 0)


if __name__ == "__main__":
    unittest.main()
