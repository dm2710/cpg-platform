"""
Integration tests — end-to-end API flows using TestClient + real DB.
"""

import io
from datetime import date, timedelta

import pytest

from app.tests.conftest import make_transaction_record, make_sku_record, seed_category, seed_region


# ── Health ────────────────────────────────────────────────

class TestHealth:
    def test_health_ok(self, client):
        r = client.get("/api/v1/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_health_db_ok(self, client):
        r = client.get("/api/v1/health/db")
        assert r.status_code == 200
        assert r.json()["database"] == "connected"


# ── Ingestion: JSON push ──────────────────────────────────

class TestJsonPush:
    def test_push_single_record(self, client):
        r = client.post("/api/v1/ingestion/push", json={
            "source_name": "test_push",
            "records": [make_transaction_record()],
        })
        assert r.status_code == 200
        data = r.json()
        assert data["pipeline_result"]["accepted"] == 1
        assert data["pipeline_result"]["rejected"] == 0

    def test_push_multiple_records(self, client):
        records = [make_transaction_record(revenue=i * 100.0) for i in range(1, 6)]
        r = client.post("/api/v1/ingestion/push", json={
            "source_name": "test_push",
            "records": records,
        })
        assert r.status_code == 200
        assert r.json()["pipeline_result"]["accepted"] == 5

    def test_push_deduplicates_on_retry(self, client):
        record = make_transaction_record()
        payload = {"source_name": "test_push", "records": [record]}

        r1 = client.post("/api/v1/ingestion/push", json=payload)
        r2 = client.post("/api/v1/ingestion/push", json=payload)  # retry

        assert r1.json()["pipeline_result"]["accepted"] == 1
        assert r2.json()["pipeline_result"]["duplicates_skipped"] == 1
        assert r2.json()["pipeline_result"]["accepted"] == 0

    def test_push_rejects_missing_date(self, client):
        r = client.post("/api/v1/ingestion/push", json={
            "source_name": "test_push",
            "records": [{"revenue": 100.0, "category_name": "Electronics"}],
        })
        assert r.status_code == 200
        assert r.json()["pipeline_result"]["rejected"] == 1

    def test_push_resolves_schema_drift(self, client):
        """Record uses non-canonical field names → pipeline resolves them."""
        r = client.post("/api/v1/ingestion/push", json={
            "source_name": "pos_legacy",
            "records": [{
                "sale_date":    str(date.today()),
                "net_amount":   250.0,
                "dept":         "Electronics",
                "store_region": "North America",
                "qty":          3,
            }],
        })
        assert r.status_code == 200
        assert r.json()["pipeline_result"]["accepted"] == 1

    def test_push_with_currency_conversion(self, client):
        r = client.post("/api/v1/ingestion/push", json={
            "source_name": "shopify",
            "records": [make_transaction_record(revenue=100.0, currency="EUR")],
        })
        assert r.status_code == 200
        # Accepted despite non-USD currency
        assert r.json()["pipeline_result"]["accepted"] == 1

    def test_push_empty_records_rejected(self, client):
        r = client.post("/api/v1/ingestion/push", json={
            "source_name": "test",
            "records": [],
        })
        assert r.status_code == 422  # Pydantic min_length=1


# ── Ingestion: CSV upload ─────────────────────────────────

class TestCsvUpload:
    def _make_csv(self, rows: list[dict]) -> bytes:
        import csv, io
        buf = io.StringIO()
        if rows:
            writer = csv.DictWriter(buf, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        return buf.getvalue().encode()

    def test_upload_standard_csv(self, client):
        csv_bytes = self._make_csv([
            {"transaction_date": str(date.today()), "revenue": 200.0,
             "category_name": "Apparel", "region_name": "Europe", "quantity": 5},
        ])
        r = client.post(
            "/api/v1/ingestion/upload-csv?source_name=csv_upload",
            files={"file": ("test.csv", io.BytesIO(csv_bytes), "text/csv")},
        )
        assert r.status_code == 200
        assert r.json()["pipeline_result"]["accepted"] == 1

    def test_upload_drifted_headers(self, client):
        """CSV uses 'sale_date', 'net_amount' — pipeline resolves aliases."""
        csv_bytes = self._make_csv([
            {"sale_date": str(date.today()), "net_amount": 300.0,
             "dept": "Sports", "store_region": "Asia"},
        ])
        r = client.post(
            "/api/v1/ingestion/upload-csv?source_name=pos_legacy",
            files={"file": ("pos.csv", io.BytesIO(csv_bytes), "text/csv")},
        )
        assert r.status_code == 200
        assert r.json()["pipeline_result"]["accepted"] == 1

    def test_upload_non_csv_rejected(self, client):
        r = client.post(
            "/api/v1/ingestion/upload-csv?source_name=test",
            files={"file": ("data.xlsx", b"binary", "application/octet-stream")},
        )
        assert r.status_code == 400


# ── Staging inspection ────────────────────────────────────

class TestStaging:
    def test_list_staging_after_push(self, client):
        client.post("/api/v1/ingestion/push", json={
            "source_name": "test_push",
            "records": [make_transaction_record()],
        })
        r = client.get("/api/v1/ingestion/staging?source_name=test_push")
        assert r.status_code == 200
        assert r.json()["total"] >= 1

    def test_get_single_staging_record(self, client):
        client.post("/api/v1/ingestion/push", json={
            "source_name": "test_push",
            "records": [make_transaction_record()],
        })
        list_r = client.get("/api/v1/ingestion/staging")
        staging_id = list_r.json()["data"][0]["staging_id"]

        r = client.get(f"/api/v1/ingestion/staging/{staging_id}")
        assert r.status_code == 200
        assert r.json()["staging_id"] == staging_id

    def test_staging_404_for_missing_id(self, client):
        r = client.get("/api/v1/ingestion/staging/999999")
        assert r.status_code == 404


# ── Analytics ─────────────────────────────────────────────

class TestAnalytics:
    def _load_records(self, client, n: int = 5):
        records = [
            make_transaction_record(
                transaction_date=str(date.today() - timedelta(days=i)),
                revenue=100.0 * (i + 1),
                category_name="Electronics" if i % 2 == 0 else "Apparel",
                region_name="North America",
            )
            for i in range(n)
        ]
        client.post("/api/v1/ingestion/push", json={"source_name": "test_push", "records": records})

    def test_summary_after_ingestion(self, client):
        self._load_records(client)
        r = client.get("/api/v1/analytics/summary")
        assert r.status_code == 200
        data = r.json()
        assert float(data["total_revenue"]) > 0
        assert data["category_count"] >= 1

    def test_revenue_time_series(self, client):
        self._load_records(client)
        r = client.get("/api/v1/analytics/revenue?granularity=day")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_revenue_with_date_filter(self, client):
        self._load_records(client)
        today = date.today()
        r = client.get(
            f"/api/v1/analytics/revenue?start_date={today}&end_date={today}"
        )
        assert r.status_code == 200

    def test_breakdown_by_category(self, client):
        self._load_records(client)
        r = client.get("/api/v1/analytics/breakdown?dimension=category")
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 1
        assert "label" in data[0]
        assert "revenue" in data[0]
        assert "pct" in data[0]

    def test_breakdown_by_region(self, client):
        self._load_records(client)
        r = client.get("/api/v1/analytics/breakdown?dimension=region")
        assert r.status_code == 200

    def test_list_categories(self, client):
        self._load_records(client)
        r = client.get("/api/v1/analytics/categories")
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_list_regions(self, client):
        self._load_records(client)
        r = client.get("/api/v1/analytics/regions")
        assert r.status_code == 200


# ── Reference data ────────────────────────────────────────

class TestReference:
    def test_upsert_sku(self, client):
        r = client.post("/api/v1/reference/catalog/sku", json=make_sku_record())
        assert r.status_code == 200
        assert "upserted" in r.json()["message"]

    def test_list_skus(self, client):
        client.post("/api/v1/reference/catalog/sku", json=make_sku_record())
        r = client.get("/api/v1/reference/catalog/skus")
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_sku_scd2_version_created(self, client):
        """Changing list_price on an existing SKU creates a new SCD2 version."""
        sku = make_sku_record()
        client.post("/api/v1/reference/catalog/sku", json=sku)

        sku_v2 = {**sku, "list_price": 149.99, "change_reason": "price_increase"}
        client.post("/api/v1/reference/catalog/sku", json=sku_v2)

        r = client.get(f"/api/v1/reference/catalog/sku/{sku['sku_id']}/history")
        assert r.status_code == 200
        assert r.json()["total"] >= 2

    def test_upsert_store(self, client, db):
        seed_region(db, "North America")
        r = client.post("/api/v1/reference/stores", json={
            "store_id":   "STR-001",
            "store_name": "New York Flagship",
            "region_name": "North America",
            "store_type": "flagship",
            "city":       "New York",
        })
        assert r.status_code == 200

    def test_upsert_campaign(self, client):
        r = client.post("/api/v1/reference/signals/campaigns", json={
            "campaign_id":   "CAMP-001",
            "campaign_name": "Q4 Holiday Push",
            "start_date":    str(date.today()),
            "end_date":      str(date.today() + timedelta(days=30)),
            "channel":       "social",
            "budget_usd":    50000.0,
        })
        assert r.status_code == 200

    def test_upsert_promo(self, client):
        r = client.post("/api/v1/reference/signals/promos", json={
            "promo_id":    "PROMO-001",
            "promo_name":  "Black Friday 2024",
            "start_date":  str(date.today()),
            "end_date":    str(date.today() + timedelta(days=4)),
            "promo_type":  "pct_off",
            "discount_pct": 25.0,
        })
        assert r.status_code == 200


# ── Data quality ──────────────────────────────────────────

class TestDataQuality:
    def test_dq_summary_after_push(self, client):
        # Push a record with a drifted field to generate issues
        client.post("/api/v1/ingestion/push", json={
            "source_name": "pos_legacy",
            "records": [{"sale_date": str(date.today()), "net_amount": 100.0, "dept": "Elec"}],
        })
        r = client.get("/api/v1/dq/summary")
        assert r.status_code == 200

    def test_dq_issues_list(self, client):
        r = client.get("/api/v1/dq/issues")
        assert r.status_code == 200
        assert "data" in r.json()

    def test_dq_sources_health(self, client):
        client.post("/api/v1/ingestion/push", json={
            "source_name": "test_push",
            "records": [make_transaction_record()],
        })
        r = client.get("/api/v1/dq/sources")
        assert r.status_code == 200

    def test_late_arrivals_list(self, client):
        r = client.get("/api/v1/dq/late-arrivals")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_recompute_endpoint(self, client):
        today = date.today()
        r = client.post(f"/api/v1/dq/recompute?from_date={today}")
        assert r.status_code == 200
