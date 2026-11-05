import os
import unittest
from unittest.mock import patch

os.environ.setdefault("SUPERMARKET_API_SCRAPE_TOKEN", "test-scrape-token")

from fastapi.testclient import TestClient  # noqa: E402
from pydantic import SecretStr  # noqa: E402

from app.api.routes import scrape as scrape_routes  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas.product import ScrapeJob  # noqa: E402


class ScrapeRoutesTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()

    def test_scrape_requires_a_valid_token(self) -> None:
        with patch.object(
            scrape_routes.settings, "scrape_token", SecretStr("test-scrape-token")
        ):
            missing_token = self.client.post("/scrape/mercadona")
            invalid_token = self.client.post(
                "/scrape/mercadona", headers={"X-Scrape-Token": "invalid"}
            )

        self.assertEqual(missing_token.status_code, 401)
        self.assertEqual(invalid_token.status_code, 401)

    @patch("app.api.routes.scrape.scrape_service.start_job")
    def test_scrape_starts_job_with_valid_token(self, start_job) -> None:
        start_job.return_value = ScrapeJob(supermarket="mercadona", status="running")

        with patch.object(
            scrape_routes.settings, "scrape_token", SecretStr("test-scrape-token")
        ):
            response = self.client.post(
                "/scrape/mercadona",
                headers={"X-Scrape-Token": "test-scrape-token"},
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "running")
        start_job.assert_called_once_with("mercadona", None)

    @patch("app.api.routes.scrape.scrape_service.start_job")
    def test_scrape_forwards_valid_postcode(self, start_job) -> None:
        start_job.return_value = ScrapeJob(
            supermarket="mercadona", status="running", postcode="28001"
        )

        with patch.object(
            scrape_routes.settings, "scrape_token", SecretStr("test-scrape-token")
        ):
            response = self.client.post(
                "/scrape/mercadona",
                params={"postcode": "28001"},
                headers={"X-Scrape-Token": "test-scrape-token"},
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["postcode"], "28001")
        start_job.assert_called_once_with("mercadona", "28001")

    @patch("app.api.routes.scrape.scrape_service.start_job")
    def test_scrape_rejects_invalid_postcode(self, start_job) -> None:
        with patch.object(
            scrape_routes.settings, "scrape_token", SecretStr("test-scrape-token")
        ):
            for postcode in ("1234", "123456", "abcde", "00001", "53001"):
                response = self.client.post(
                    "/scrape/mercadona",
                    params={"postcode": postcode},
                    headers={"X-Scrape-Token": "test-scrape-token"},
                )
                self.assertEqual(response.status_code, 422, postcode)

        start_job.assert_not_called()

    @patch("app.api.routes.scrape.scrape_service.start_job")
    def test_lidl_is_enabled_by_default(self, start_job) -> None:
        start_job.return_value = ScrapeJob(supermarket="lidl", status="running")
        with patch.object(
            scrape_routes.settings, "scrape_token", SecretStr("test-scrape-token")
        ):
            response = self.client.post(
                "/scrape/lidl",
                headers={"X-Scrape-Token": "test-scrape-token"},
            )

        self.assertEqual(response.status_code, 202)
        start_job.assert_called_once_with("lidl", None)

    def test_unknown_supermarket_is_not_found(self) -> None:
        with patch.object(
            scrape_routes.settings, "scrape_token", SecretStr("test-scrape-token")
        ):
            response = self.client.post(
                "/scrape/carrefour",
                headers={"X-Scrape-Token": "test-scrape-token"},
            )

        self.assertEqual(response.status_code, 404)
