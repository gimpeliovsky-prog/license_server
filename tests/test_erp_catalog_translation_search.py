from types import SimpleNamespace

from app.services import erp_catalog


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_fetch_translation_source_texts_prefers_matching_translation(monkeypatch):
    calls = []

    def fake_request(_tenant, _method, path, params=None):
        calls.append((path, params))
        assert path == "/api/resource/Translation"
        return _FakeResponse(
            200,
            {
                "data": [
                    {"source_text": "Laptop", "translated_text": "מחשב נייד", "language": "he"},
                    {"source_text": "Desktop", "translated_text": "מחשב שולחני", "language": "he"},
                ]
            },
        )

    monkeypatch.setattr(erp_catalog, "request_tenant_erpnext", fake_request)

    source_texts = erp_catalog._fetch_translation_source_texts(
        SimpleNamespace(),
        query="מחשב נייד",
        lang="he",
        limit=5,
    )

    assert source_texts == ["Laptop"]
    assert calls


def test_fetch_items_by_source_texts_returns_unique_item_codes(monkeypatch):
    def fake_request(_tenant, _method, path, params=None):
        assert path == "/api/resource/Item"
        return _FakeResponse(
            200,
            {
                "data": [
                    {"item_code": "SKU002", "item_name": "Laptop"},
                    {"item_code": "SKU002", "item_name": "Laptop"},
                ]
            },
        )

    monkeypatch.setattr(erp_catalog, "request_tenant_erpnext", fake_request)

    items = erp_catalog._fetch_items_by_source_texts(
        SimpleNamespace(),
        source_texts=["Laptop"],
        limit=5,
    )

    assert items == [{"item_code": "SKU002", "item_name": "Laptop"}]
