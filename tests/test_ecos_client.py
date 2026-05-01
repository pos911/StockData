import unittest
from unittest.mock import Mock, patch

from src.collectors.ecos_client import EcosClient
from src.collectors.ecos_rates import BaseEcosSeriesCollector


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class EcosClientTests(unittest.TestCase):
    def setUp(self):
        self.client = EcosClient(api_key="TESTKEY")

    def test_build_url(self):
        url = self.client.build_statistic_url(
            stat_code="817Y002",
            item_code="010210000",
            cycle="D",
            start_date="20240101",
            end_date="20240131",
            start=1,
            end=100,
        )
        self.assertEqual(
            url,
            "https://ecos.bok.or.kr/api/StatisticSearch/TESTKEY/json/kr/1/100/817Y002/D/20240101/20240131/010210000/?/?/?",
        )

    def test_parse_time(self):
        self.assertEqual(str(EcosClient.parse_time("20240424", "D")), "2024-04-24")
        self.assertEqual(str(EcosClient.parse_time("202404", "M")), "2024-04-01")
        self.assertEqual(str(EcosClient.parse_time("2024Q3", "Q")), "2024-07-01")
        self.assertEqual(str(EcosClient.parse_time("2024", "A")), "2024-01-01")

    def test_parse_value(self):
        self.assertEqual(EcosClient.parse_value("3.728"), 3.728)
        self.assertIsNone(EcosClient.parse_value("."))
        self.assertIsNone(EcosClient.parse_value(""))

    @patch("src.collectors.ecos_client.requests.Session.get")
    def test_fetch_statistic_empty_response(self, mock_get):
        mock_get.return_value = FakeResponse(
            {"RESULT": {"CODE": "INFO-200", "MESSAGE": "해당하는 데이터가 없습니다."}}
        )
        rows = self.client.fetch_statistic(
            stat_code="817Y002",
            item_code="010210000",
            cycle="D",
            start_date="20240101",
            end_date="20240131",
        )
        self.assertEqual(rows, [])

    @patch("src.collectors.ecos_client.requests.Session.get")
    def test_fetch_statistic_converts_rows(self, mock_get):
        mock_get.side_effect = [
            FakeResponse(
                {
                    "StatisticSearch": {
                        "list_total_count": 1,
                        "row": [
                            {
                                "STAT_CODE": "817Y002",
                                "ITEM_CODE1": "010210000",
                                "ITEM_NAME1": "국고채(10년)",
                                "UNIT_NAME": "연%",
                                "TIME": "20240102",
                                "DATA_VALUE": "3.123",
                            }
                        ],
                    }
                }
            ),
            FakeResponse(
                {
                    "StatisticSearch": {
                        "list_total_count": 1,
                        "row": [
                            {
                                "STAT_CODE": "817Y002",
                                "ITEM_CODE1": "010210000",
                                "ITEM_NAME1": "국고채(10년)",
                                "UNIT_NAME": "연%",
                                "TIME": "20240102",
                                "DATA_VALUE": "3.123",
                            }
                        ],
                    }
                }
            ),
        ]
        rows = self.client.fetch_statistic(
            stat_code="817Y002",
            item_code="010210000",
            cycle="D",
            start_date="20240101",
            end_date="20240131",
            series_id="KR_GOVT_10Y",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["series_id"], "KR_GOVT_10Y")
        self.assertEqual(rows[0]["date"], "2024-01-02")
        self.assertEqual(rows[0]["value"], 3.123)

    def test_duplicate_deduplication(self):
        rows = [
            {"series_id": "KR_GOVT_10Y", "date": "2024-01-02", "value": 3.1},
            {"series_id": "KR_GOVT_10Y", "date": "2024-01-02", "value": 3.2},
        ]
        deduped = BaseEcosSeriesCollector._deduplicate_by_date(rows)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["value"], 3.2)

    def test_collect_definitions_uses_lookback_when_incremental_empty(self):
        class FakeClient:
            def __init__(self):
                self.calls = []

            def fetch_statistic(self, **kwargs):
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    return []
                return [
                    {
                        "series_id": "KR_GOVT_10Y",
                        "date": "2026-04-24",
                        "time": "20260424",
                        "value": 3.817,
                        "item_name": "국고채 10년",
                        "unit": "연%",
                        "collected_at": "2026-04-24T18:30:00+09:00",
                    }
                ]

        class FakeQuery:
            data = [{"date": "2026-04-24"}]

            def select(self, *_args, **_kwargs):
                return self

            def eq(self, *_args, **_kwargs):
                return self

            def order(self, *_args, **_kwargs):
                return self

            def limit(self, *_args, **_kwargs):
                return self

            def execute(self):
                return self

        class FakeLoader:
            class Client:
                def table(self, _name):
                    return FakeQuery()

            client = Client()

        definition = {
            "series_id": "KR_GOVT_10Y",
            "source": "ECOS",
            "stat_code": "060Y001",
            "item_code": "010210000",
            "cycle": "DD",
            "name_ko": "국고채 10년",
            "category": "rates",
            "unit": "연%",
            "frequency": "daily",
        }
        fake_client = FakeClient()
        collector = BaseEcosSeriesCollector(client=fake_client, loader=FakeLoader())
        result = collector.collect_definitions([definition], lookback_days=14)

        self.assertEqual(len(fake_client.calls), 2)
        self.assertEqual(fake_client.calls[0]["start_date"], "20260425")
        self.assertEqual(len(result["normalized_records"]), 1)
        self.assertEqual(result["normalized_records"][0]["series_id"], "KR_GOVT_10Y")


if __name__ == "__main__":
    unittest.main()
