from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException

from app.services.erpnext import absolute_media_url, request_tenant_erpnext


def fetch_item_doc(tenant, item_code: str) -> dict[str, Any] | None:
    response = request_tenant_erpnext(tenant, "GET", f"/api/resource/Item/{quote(item_code, safe='')}")
    if response.status_code != 200:
        return None
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, dict) else None


def resolve_item_doc(tenant, item_ref: str) -> dict[str, Any] | None:
    item_ref = str(item_ref or "").strip()
    if not item_ref:
        return None

    item_doc = fetch_item_doc(tenant, item_ref)
    if item_doc:
        return item_doc

    search_variants = [
        [["disabled", "=", 0], ["item_name", "=", item_ref]],
        [["disabled", "=", 0], ["item_name", "like", f"%{item_ref}%"]],
    ]
    for filters in search_variants:
        response = request_tenant_erpnext(
            tenant,
            "GET",
            "/api/resource/Item",
            params={
                "fields": json.dumps(["item_code", "item_name", "item_group", "description", "stock_uom"]),
                "filters": json.dumps(filters),
                "limit_page_length": 1,
            },
        )
        if response.status_code != 200:
            continue
        data = response.json().get("data", [])
        if not data:
            continue
        resolved_item_code = data[0].get("item_code")
        if resolved_item_code:
            item_doc = fetch_item_doc(tenant, resolved_item_code)
            if item_doc:
                return item_doc
    return None


def fetch_item_translation(tenant, source_text: str | None, lang: str | None) -> str | None:
    if not source_text or not lang or lang == "en":
        return None
    response = request_tenant_erpnext(
        tenant,
        "GET",
        "/api/resource/Translation",
        params={
            "fields": json.dumps(["source_text", "translated_text", "language"]),
            "filters": json.dumps([["source_text", "=", source_text], ["language", "=", lang]]),
            "limit_page_length": 1,
        },
    )
    if response.status_code != 200:
        return None
    data = response.json().get("data", [])
    if not data:
        return None
    translated = str(data[0].get("translated_text") or "").strip()
    return translated or None


def fetch_item_price(tenant, item_code: str | None) -> tuple[float | None, str | None]:
    if not item_code:
        return None, None
    response = request_tenant_erpnext(
        tenant,
        "GET",
        "/api/resource/Item Price",
        params={
            "fields": json.dumps(["item_code", "price_list_rate", "currency", "price_list", "selling"]),
            "filters": json.dumps([["item_code", "=", item_code], ["selling", "=", 1]]),
            "order_by": "modified desc",
            "limit_page_length": 1,
        },
    )
    if response.status_code != 200:
        return None, None
    data = response.json().get("data", [])
    if not data:
        return None, None
    row = data[0]
    return row.get("price_list_rate"), row.get("currency")


def extract_item_uoms(item_doc: dict[str, Any], fallback_stock_uom: str | None = None) -> tuple[str | None, str | None, list[dict[str, Any]]]:
    stock_uom = item_doc.get("stock_uom") or fallback_stock_uom
    sales_uom = item_doc.get("sales_uom")
    available_uoms: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add_uom(raw_uom: Any, raw_conversion: Any) -> None:
        if not isinstance(raw_uom, str):
            return
        uom = raw_uom.strip()
        if not uom:
            return
        key = uom.lower()
        if key in seen:
            return
        conversion_factor: float | None = None
        if isinstance(raw_conversion, (int, float)):
            conversion_factor = float(raw_conversion)
            if conversion_factor <= 0:
                conversion_factor = 1.0
        elif stock_uom and uom.lower() == str(stock_uom).lower():
            conversion_factor = 1.0
        available_uoms.append(
            {
                "uom": uom,
                "conversion_factor": conversion_factor,
                "is_stock_uom": bool(stock_uom and uom.lower() == str(stock_uom).lower()),
            }
        )
        seen.add(key)

    _add_uom(stock_uom, 1.0)
    for row in item_doc.get("uoms", []):
        if isinstance(row, dict):
            _add_uom(row.get("uom"), row.get("conversion_factor"))
    _add_uom(sales_uom, 1.0 if sales_uom and stock_uom and str(sales_uom).lower() == str(stock_uom).lower() else None)

    return stock_uom, sales_uom, available_uoms


def humanize_uom(uom: str | None, lang: str = "ru") -> str | None:
    if not uom:
        return None
    normalized = uom.strip().lower()
    labels_ru = {
        "nos": "С€С‚СѓРєРё",
        "unit": "С€С‚СѓРєРё",
        "pcs": "С€С‚СѓРєРё",
        "pc": "С€С‚СѓРєРё",
        "piece": "С€С‚СѓРєРё",
        "box": "РєРѕСЂРѕР±РєРё",
        "boxes": "РєРѕСЂРѕР±РєРё",
        "pack": "СѓРїР°РєРѕРІРєРё",
        "packet": "СѓРїР°РєРѕРІРєРё",
        "kg": "РєРёР»РѕРіСЂР°РјРјС‹",
        "g": "РіСЂР°РјРјС‹",
        "l": "Р»РёС‚СЂС‹",
        "m": "РјРµС‚СЂС‹",
    }
    labels_en = {
        "nos": "pieces",
        "unit": "pieces",
        "pcs": "pieces",
        "pc": "pieces",
        "piece": "pieces",
        "box": "boxes",
        "boxes": "boxes",
        "pack": "packs",
        "packet": "packs",
        "kg": "kilograms",
        "g": "grams",
        "l": "liters",
        "m": "meters",
    }
    labels = labels_ru if lang == "ru" else labels_en
    return labels.get(normalized, uom)


def customer_uom_summary(stock_uom: str | None, non_stock_uoms: list[dict[str, Any]], lang: str = "ru") -> str | None:
    stock_label = humanize_uom(stock_uom, lang)
    if not non_stock_uoms:
        if not stock_label:
            return None
        if lang == "ru":
            return f"РўРѕРІР°СЂ РїСЂРѕРґР°РµС‚СЃСЏ РІ РµРґРёРЅРёС†Р°С…: {stock_label}."
        return f"This product is sold in: {stock_label}."

    non_stock_labels = [str(uom.get('display_name') or uom.get('uom')) for uom in non_stock_uoms if uom.get("display_name") or uom.get("uom")]
    if not non_stock_labels:
        if not stock_label:
            return None
        if lang == "ru":
            return f"РўРѕРІР°СЂ РїСЂРѕРґР°РµС‚СЃСЏ РІ РµРґРёРЅРёС†Р°С…: {stock_label}."
        return f"This product is sold in: {stock_label}."

    all_labels: list[str] = []
    if stock_label:
        all_labels.append(stock_label)
    all_labels.extend([label for label in non_stock_labels if label not in all_labels])
    if lang == "ru":
        return f"РўРѕРІР°СЂ РїСЂРѕРґР°РµС‚СЃСЏ РІ РµРґРёРЅРёС†Р°С…: {', '.join(all_labels)}."
    return f"This product is sold in: {', '.join(all_labels)}."


def list_items(
    tenant,
    *,
    item_group: str | None = None,
    item_name: str | None = None,
    lang: str | None = None,
    limit: int = 200,
    enrich: bool = True,
) -> list[dict[str, Any]]:
    resolved_limit = max(1, min(200, int(limit or 200)))
    detailed_fields = ["item_code", "item_name", "item_group", "description", "standard_rate", "currency", "stock_uom", "image", "website_image"]
    basic_fields = ["item_code", "item_name", "item_group", "description", "stock_uom", "image", "website_image"]
    selected_fields = detailed_fields if enrich else basic_fields

    def _fetch(filters: list[list[object]]) -> list[dict[str, Any]]:
        response = request_tenant_erpnext(
            tenant,
            "GET",
            "/api/resource/Item",
            params={
                "fields": json.dumps(selected_fields),
                "filters": json.dumps(filters),
                "limit_page_length": resolved_limit,
            },
        )
        if enrich and response.status_code == 417:
            response = request_tenant_erpnext(
                tenant,
                "GET",
                "/api/resource/Item",
                params={
                    "fields": json.dumps(basic_fields),
                    "filters": json.dumps(filters),
                    "limit_page_length": resolved_limit,
                },
            )
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail=f"ERPNext returned {response.status_code}")
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        return data if isinstance(data, list) else []

    filters = [["disabled", "=", 0]]
    if item_group:
        filters.append(["item_group", "=", item_group])
    if item_name:
        filters.append(["item_name", "like", f"%{item_name}%"])

    items = _fetch(filters)
    if not items and item_name:
        items = _fetch([["disabled", "=", 0], ["item_name", "like", f"%{item_name}%"]])
    if not items and item_group and not item_name:
        items = _fetch([["disabled", "=", 0], ["item_name", "like", f"%{item_group}%"]])

    if not enrich:
        return items

    for item in items:
        item_doc = fetch_item_doc(tenant, item.get("item_code", ""))
        translated_name = fetch_item_translation(tenant, item.get("item_name"), lang)
        image_url = absolute_media_url(
            tenant.erpnext_url,
            (item_doc or {}).get("image") or (item_doc or {}).get("website_image") or item.get("image"),
        )
        price_rate, price_currency = fetch_item_price(tenant, item.get("item_code"))
        if item.get("standard_rate") in (None, "") and price_rate not in (None, ""):
            item["standard_rate"] = price_rate
        else:
            item.setdefault("standard_rate", None)
        if item.get("currency") in (None, "") and price_currency not in (None, ""):
            item["currency"] = price_currency
        else:
            item.setdefault("currency", None)
        stock_uom, sales_uom, available_uoms = extract_item_uoms(item_doc or {}, item.get("stock_uom"))
        item["translated_item_name"] = translated_name
        item["display_item_name"] = translated_name or item.get("item_name")
        item["image_url"] = image_url
        item["stock_uom"] = stock_uom
        item["stock_uom_label"] = humanize_uom(stock_uom, "ru")
        item["sales_uom"] = sales_uom
        item["sales_uom_label"] = humanize_uom(sales_uom, "ru")
        for uom in available_uoms:
            uom["display_name"] = humanize_uom(uom.get("uom"), "ru")
        item["available_uoms"] = available_uoms
        item["non_stock_uoms"] = [uom for uom in available_uoms if not uom.get("is_stock_uom")]
        item["customer_uom_summary"] = customer_uom_summary(item["stock_uom"], item["non_stock_uoms"], "ru")
    return items


def get_item_detail(tenant, item_ref: str, *, lang: str | None = None) -> dict[str, Any] | None:
    item_doc = resolve_item_doc(tenant, item_ref)
    if not item_doc:
        return None
    stock_uom, sales_uom, available_uoms = extract_item_uoms(item_doc, item_doc.get("stock_uom"))
    translated_name = fetch_item_translation(tenant, item_doc.get("item_name"), lang)
    image_url = absolute_media_url(tenant.erpnext_url, item_doc.get("image") or item_doc.get("website_image"))
    price_rate, price_currency = fetch_item_price(tenant, item_doc.get("item_code"))
    return {
        "item_code": item_doc.get("item_code"),
        "item_name": item_doc.get("item_name"),
        "translated_item_name": translated_name,
        "display_item_name": translated_name or item_doc.get("item_name"),
        "item_group": item_doc.get("item_group"),
        "description": item_doc.get("description"),
        "standard_rate": price_rate,
        "currency": price_currency,
        "image_url": image_url,
        "stock_uom": stock_uom,
        "sales_uom": sales_uom,
        "available_uoms": available_uoms,
    }
