import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

os.environ.setdefault("SUPERMARKET_API_SCRAPE_TOKEN", "test-scrape-token")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.database import Base  # noqa: E402
from app.main import app  # noqa: E402
from app.models import backup, product  # noqa: E402,F401
from app.models.product import ProductRow  # noqa: E402
from app.repositories import product_repository  # noqa: E402
from app.sections import SECTIONS, classify  # noqa: E402

# (ruta en la tienda, nombre del producto, sección esperada), sacados de los catálogos reales
CASES = [
    (["Panadería y pastelería", "Pan de molde y otras especialidades", "Pan de hamburguesa y perritos"], "Pan hamburguesa", "panaderia"),
    (["Alimentación", "Carnes y huevos", "Huevos"], "Huevos L", "lacteos-huevos"),
    (["Frescos", "Lácteos", "Yogures", "Yogur natural y azucarado"], "Yogur", "lacteos-huevos"),
    (["Despensa", "Legumbres y cereales"], "Garbanzos", "despensa"),
    (["Alimentación", "Repostería", "Harina"], "Harina de trigo", "despensa"),
    (["Infantil", "Pañales", "Pañales para adulto"], "Pañal", "higiene-belleza"),
    (["Gourmet", "Postres gourmet"], "Tiramisú", "desayuno-dulces"),
    ([None], "Uva Blanca Bandeja", "fruta-verdura"),
    (["Huevos, leche y mantequilla", "Leche y bebidas vegetales"], "Leche entera", "lacteos-huevos"),
    (["Azúcar, caramelos y chocolate", "Chocolate", "Chocolate con leche"], "Chocolate", "desayuno-dulces"),
    (["Congelados", "Pescado"], "Merluza", "congelados"),
    (["Fruta y verdura", "Verdura"], "Aguacate", "fruta-verdura"),
    (["Carne", "Cerdo"], "Lomo", "carne"),
    (["Frescos", "Charcutería", "Cocidos de pollo"], "Pechuga", "charcuteria-quesos"),
    (["Despensa", "Conservas", "Conservas de pescado"], "Atún", "despensa"),
    (["Bebidas", "Zumos"], "Zumo naranja", "bebidas"),
    (["Agua y refrescos", "Agua"], "Agua", "bebidas"),
    (["Bodega", "Vino tinto"], "Rioja", "bodega"),
    (["Mascotas", "Gato"], "Pienso", "mascotas"),
    (["Limpieza y hogar", "Detergente y suavizante ropa"], "Detergente", "limpieza-hogar"),
    (["Cuidado del cabello", "Champú"], "Champú", "higiene-belleza"),
    (["Pizzas y platos preparados", "Platos preparados calientes"], "Lasaña", "preparados"),
    ([], "Producto sin pistas", "otros"),
]


class ClassifyTestCase(unittest.TestCase):
    def test_real_store_paths(self) -> None:
        for path, name, expected in CASES:
            self.assertEqual(classify(path, name), expected, (path, name))

    def test_name_is_only_used_when_the_path_does_not_match(self) -> None:
        self.assertEqual(classify(["Fruta y verdura"], "Chocolate"), "fruta-verdura")
        self.assertEqual(classify(["Novedades"], "Chocolate con almendras"), "desayuno-dulces")

    def test_every_rule_points_to_a_known_section(self) -> None:
        from app.sections import _RULES

        slugs = {slug for slug, _ in SECTIONS}
        self.assertTrue({slug for slug, _ in _RULES} <= slugs)


class SectionsApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        now = datetime.now(timezone.utc)
        rows = [
            ("1", "mercadona", "Leche entera", "Leche y bebidas vegetales", "lacteos-huevos", None),
            ("2", "bonarea", "Leche semidesnatada", "Leche", "lacteos-huevos", "Bajada de precio"),
            ("3", "bonarea", "Yogur natural", "Yogures", "lacteos-huevos", None),
            ("4", "aldi", "Aceite de oliva", "Aceites y vinagres", "despensa", "-30%"),
            ("5", "aldi", "Pan rústico", None, None, None),
        ]
        with factory() as session, session.begin():
            session.add_all(
                ProductRow(
                    external_id=pid, supermarket=sm, name=name, category=category,
                    section=section, promotion=promo, scraped_at=now,
                )
                for pid, sm, name, category, section, promo in rows
            )
        patcher = patch.object(product_repository, "SessionLocal", factory)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def _counts(self, **params):
        response = self.client.get("/sections", params=params)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_lists_every_section_in_order_with_counts(self) -> None:
        sections = self._counts()
        self.assertEqual([s["slug"] for s in sections], [slug for slug, _ in SECTIONS])
        counts = {s["slug"]: s["count"] for s in sections}
        self.assertEqual(counts["lacteos-huevos"], 3)
        self.assertEqual(counts["despensa"], 1)
        self.assertTrue(all(s["categories"] == [] for s in sections))

    def test_counts_follow_filters_and_list_categories_of_the_section(self) -> None:
        sections = {s["slug"]: s for s in self._counts(q="leche", section="lacteos-huevos")}
        lacteos = sections["lacteos-huevos"]
        self.assertEqual(lacteos["count"], 2)
        self.assertEqual(
            lacteos["categories"],
            [{"name": "Leche", "count": 1}, {"name": "Leche y bebidas vegetales", "count": 1}],
        )
        self.assertEqual(sections["despensa"]["count"], 0)

        promos = {s["slug"]: s["count"] for s in self._counts(promotion="true")}
        self.assertEqual((promos["lacteos-huevos"], promos["despensa"]), (1, 1))

    def test_products_filter_by_section_and_exact_category(self) -> None:
        response = self.client.get("/products", params={"section": "lacteos-huevos", "subcategory": "Leche"})
        self.assertEqual([p["id"] for p in response.json()["items"]], ["2"])
        self.assertEqual(response.json()["items"][0]["section"], "lacteos-huevos")

    def test_products_and_sections_can_be_limited_to_some_supermarkets(self) -> None:
        response = self.client.get("/products", params={"supermarkets": "mercadona,aldi"})
        self.assertEqual(sorted(p["supermarket"] for p in response.json()["items"]), ["aldi", "aldi", "mercadona"])

        counts = {s["slug"]: s["count"] for s in self._counts(supermarkets="bonarea")}
        self.assertEqual((counts["lacteos-huevos"], counts["despensa"]), (2, 0))

    def test_an_empty_supermarket_list_matches_nothing(self) -> None:
        self.assertEqual(self.client.get("/products", params={"supermarkets": ""}).json()["total"], 0)
        self.assertTrue(all(s["count"] == 0 for s in self._counts(supermarkets="")))

    def test_unknown_supermarket_in_the_list_is_rejected(self) -> None:
        self.assertEqual(self.client.get("/products", params={"supermarkets": "mercadona,nope"}).status_code, 404)
        self.assertEqual(self.client.get("/sections", params={"supermarkets": "nope"}).status_code, 404)

    def test_unknown_section_is_rejected(self) -> None:
        self.assertEqual(self.client.get("/sections", params={"section": "nope"}).status_code, 404)
        self.assertEqual(self.client.get("/products", params={"section": "nope"}).status_code, 404)

    def test_backfill_classifies_rows_without_section(self) -> None:
        self.assertEqual(product_repository.backfill_sections(), 1)
        response = self.client.get("/products", params={"section": "panaderia"})
        self.assertEqual([p["id"] for p in response.json()["items"]], ["5"])
        self.assertEqual(product_repository.backfill_sections(), 0)


if __name__ == "__main__":
    unittest.main()
