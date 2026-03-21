from app.services.picklist_process import (
    PickListProcessError,
    build_delivery_note_payload_from_pick_list,
    build_pick_list_preview,
    create_delivery_note_from_pick_list,
    build_delivery_note_items_from_pick_list,
    normalize_delivery_note_payload_quantities,
    sanitize_for_insert,
)


def test_build_pick_list_preview_reports_shortage_for_unallocated_qty():
    sales_order = {
        "name": "SO-0001",
        "items": [
            {
                "name": "SOI-1",
                "item_code": "ITEM-001",
                "item_name": "Fresh Fish",
                "qty": 25,
                "delivered_qty": 0,
                "picked_qty": 0,
                "conversion_factor": 1,
                "delivered_by_supplier": 0,
            }
        ],
    }
    draft = {
        "locations": [
            {
                "sales_order_item": "SOI-1",
                "item_code": "ITEM-001",
                "qty": 18,
            }
        ]
    }

    preview = build_pick_list_preview(sales_order, draft, "SO-0001")

    assert preview.sales_order_name == "SO-0001"
    assert preview.allocated_line_count == 1
    assert len(preview.shortages) == 1
    shortage = preview.shortages[0]
    assert shortage.item_code == "ITEM-001"
    assert shortage.item_name == "Fresh Fish"
    assert shortage.requested_qty == 25
    assert shortage.allocated_qty == 18
    assert shortage.shortage_qty == 7


def test_build_pick_list_preview_consumes_item_level_allocations_once():
    sales_order = {
        "name": "SO-0002",
        "items": [
            {
                "name": "SOI-1",
                "item_code": "ITEM-002",
                "item_name": "Salmon",
                "qty": 10,
                "delivered_qty": 0,
                "picked_qty": 0,
                "conversion_factor": 1,
                "delivered_by_supplier": 0,
            },
            {
                "name": "SOI-2",
                "item_code": "ITEM-002",
                "item_name": "Salmon",
                "qty": 8,
                "delivered_qty": 0,
                "picked_qty": 0,
                "conversion_factor": 1,
                "delivered_by_supplier": 0,
            },
        ],
    }
    draft = {
        "locations": [
            {
                "item_code": "ITEM-002",
                "qty": 12,
            }
        ]
    }

    preview = build_pick_list_preview(sales_order, draft, "SO-0002")

    assert preview.allocated_line_count == 1
    assert len(preview.shortages) == 1
    shortage = preview.shortages[0]
    assert shortage.item_code == "ITEM-002"
    assert shortage.requested_qty == 8
    assert shortage.allocated_qty == 2
    assert shortage.shortage_qty == 6


def test_sanitize_for_insert_removes_erp_metadata_fields():
    payload = {
        "name": "PICK-0001",
        "docstatus": 0,
        "customer": "Customer A",
        "locations": [
            {
                "name": "child-row",
                "item_code": "ITEM-001",
                "qty": 5,
            }
        ],
    }

    sanitized = sanitize_for_insert(payload)

    assert "name" not in sanitized
    assert "docstatus" not in sanitized
    assert sanitized["customer"] == "Customer A"
    assert sanitized["locations"][0]["item_code"] == "ITEM-001"
    assert "name" not in sanitized["locations"][0]


def test_normalize_delivery_note_payload_requires_android_commercial_qty_for_box_weight():
    pick_list = {
        "locations": [
            {
                "name": "PICK-LINE-1",
                "item_code": "ITEM-BOX",
                "uom": "Box",
                "stock_uom": "kg",
                "conversion_factor": 12,
                "picked_qty": 65,
            }
        ]
    }
    draft = {
        "items": [
            {
                "pick_list_item": "PICK-LINE-1",
                "item_code": "ITEM-BOX",
                "qty": 5.4166667,
                "uom": "Box",
                "stock_uom": "kg",
                "conversion_factor": 12,
                "stock_qty": 65,
            }
        ]
    }

    payload = sanitize_for_insert(draft)
    try:
        normalize_delivery_note_payload_quantities(pick_list, draft, payload)
        assert False, "expected PickListProcessError"
    except PickListProcessError as exc:
        assert exc.reason_code == "invalid_completion_payload"


def test_normalize_delivery_note_payload_prefers_android_commercial_qty():
    pick_list = {
        "locations": [
            {
                "name": "PICK-LINE-3",
                "item_code": "ITEM-BOX",
                "uom": "Box",
                "stock_uom": "kg",
                "conversion_factor": 12,
                "picked_qty": 65,
            }
        ]
    }
    draft = {
        "items": [
            {
                "pick_list_item": "PICK-LINE-3",
                "item_code": "ITEM-BOX",
                "qty": 5.4166667,
                "uom": "Box",
                "stock_uom": "kg",
                "conversion_factor": 12,
                "stock_qty": 65,
            }
        ]
    }

    payload = sanitize_for_insert(draft)
    normalize_delivery_note_payload_quantities(
        pick_list,
        draft,
        payload,
        completion_lines=[{"pick_list_item": "PICK-LINE-3", "commercial_qty": 6.0}],
    )

    item = payload["items"][0]
    assert item["qty"] == 6.0
    assert item["stock_qty"] == 65
    assert round(item["conversion_factor"], 6) == round(65 / 6, 6)


def test_normalize_delivery_note_payload_prefers_android_commercial_qty_for_hebrew_weight_uom():
    pick_list = {
        "locations": [
            {
                "name": "PICK-LINE-HE-1",
                "item_code": "ITEM-BOX",
                "uom": "Box",
                "stock_uom": "ק\"ג",
                "conversion_factor": 12,
                "picked_qty": 65,
            }
        ]
    }
    draft = {
        "items": [
            {
                "pick_list_item": "PICK-LINE-HE-1",
                "item_code": "ITEM-BOX",
                "qty": 5.4166667,
                "uom": "Box",
                "stock_uom": "ק\"ג",
                "conversion_factor": 12,
                "stock_qty": 65,
            }
        ]
    }

    payload = sanitize_for_insert(draft)
    normalize_delivery_note_payload_quantities(
        pick_list,
        draft,
        payload,
        completion_lines=[{"pick_list_item": "PICK-LINE-HE-1", "commercial_qty": 5.0}],
    )

    item = payload["items"][0]
    assert item["qty"] == 5.0
    assert item["stock_qty"] == 65
    assert round(item["conversion_factor"], 6) == round(65 / 5, 6)


def test_normalize_delivery_note_payload_keeps_direct_weight_orders():
    pick_list = {
        "locations": [
            {
                "name": "PICK-LINE-2",
                "item_code": "ITEM-KG",
                "uom": "kg",
                "stock_uom": "kg",
                "conversion_factor": 1,
                "picked_qty": 7.12,
            }
        ]
    }
    draft = {
        "items": [
            {
                "pick_list_item": "PICK-LINE-2",
                "item_code": "ITEM-KG",
                "qty": 7.12,
                "uom": "kg",
                "stock_uom": "kg",
                "conversion_factor": 1,
                "stock_qty": 7.12,
            }
        ]
    }

    payload = sanitize_for_insert(draft)
    normalize_delivery_note_payload_quantities(pick_list, draft, payload)

    item = payload["items"][0]
    assert item["qty"] == 7.12
    assert item["stock_qty"] == 7.12
    assert item["conversion_factor"] == 1


def test_build_delivery_note_items_from_pick_list_uses_integer_android_box_qty():
    pick_list = {
        "locations": [
            {
                "name": "PICK-LINE-1",
                "item_code": "ITEM-BOX",
                "item_name": "Fish Box",
                "uom": "Box",
                "stock_uom": "kg",
                "conversion_factor": 12,
                "picked_qty": 65,
                "warehouse": "WH-1",
                "sales_order": "SO-1",
                "sales_order_item": "SOI-1",
            }
        ],
    }

    items = build_delivery_note_items_from_pick_list(
        pick_list,
        completion_lines=[{"pick_list_item": "PICK-LINE-1", "commercial_qty": 5.0}],
    )

    assert len(items) == 1
    item = items[0]
    assert item["qty"] == 5.0
    assert item["uom"] == "Box"
    assert item["stock_uom"] == "kg"
    assert round(item["conversion_factor"], 6) == round(65 / 5, 6)
    assert item["against_sales_order"] == "SO-1"
    assert item["so_detail"] == "SOI-1"


def test_build_delivery_note_payload_from_pick_list_uses_sales_order_defaults():
    class TenantStub:
        erpnext_url = "https://erp.example.com"
        api_key = "key"
        api_secret = "secret"

    pick_list_doc = {
        "customer": "CUST-1",
        "customer_name": "Customer One",
        "company": "COMP-1",
        "locations": [
            {
                "name": "PICK-LINE-4",
                "item_code": "ITEM-BOX",
                "item_name": "Fish Box",
                "uom": "Box",
                "stock_uom": "ק\"ג",
                "conversion_factor": 12,
                "picked_qty": 65,
                "warehouse": "WH-1",
                "sales_order": "SO-1",
                "sales_order_item": "SOI-1",
            }
        ],
    }

    from unittest.mock import patch

    with patch("app.services.picklist_process.fetch_sales_order_details", return_value={
        "customer": "CUST-1",
        "customer_name": "Customer One",
        "company": "COMP-1",
        "currency": "ILS",
        "selling_price_list": "Standard Selling",
        "set_warehouse": "WH-1",
    }):
        payload = build_delivery_note_payload_from_pick_list(
            TenantStub(),
            pick_list_doc,
            completion_lines=[{"pick_list_item": "PICK-LINE-4", "commercial_qty": 5.0}],
        )

    assert payload["customer"] == "CUST-1"
    assert payload["currency"] == "ILS"
    assert payload["selling_price_list"] == "Standard Selling"
    item = payload["items"][0]
    assert item["qty"] == 5.0
    assert item["uom"] == "Box"
    assert item["stock_uom"] == "ק\"ג"
    assert round(item["conversion_factor"], 6) == round(65 / 5, 6)


def test_create_delivery_note_from_pick_list_posts_single_direct_document():
    class TenantStub:
        erpnext_url = "https://erp.example.com"
        api_key = "key"
        api_secret = "secret"

    class ResponseStub:
        status_code = 200

        @staticmethod
        def json():
            return {"data": {"name": "DN-0001"}}

    pick_list_doc = {
        "customer": "CUST-1",
        "customer_name": "Customer One",
        "company": "COMP-1",
        "locations": [
            {
                "name": "PICK-LINE-1",
                "item_code": "ITEM-BOX",
                "item_name": "Fish Box",
                "uom": "Box",
                "stock_uom": "kg",
                "conversion_factor": 12,
                "picked_qty": 65,
                "warehouse": "WH-1",
                "sales_order": "SO-1",
                "sales_order_item": "SOI-1",
            }
        ],
    }

    from unittest.mock import patch

    with patch("app.services.picklist_process.ensure_document_submitted", return_value=pick_list_doc), \
        patch("app.services.picklist_process.fetch_sales_order_details", return_value={
            "customer": "CUST-1",
            "customer_name": "Customer One",
            "company": "COMP-1",
            "currency": "ILS",
            "selling_price_list": "Standard Selling",
            "set_warehouse": "WH-1",
        }), \
        patch("app.services.picklist_process.request_erpnext", return_value=ResponseStub()) as request_mock:
        name = create_delivery_note_from_pick_list(
            TenantStub(),
            "PICK-0001",
            completion_lines=[{"pick_list_item": "PICK-LINE-1", "commercial_qty": 5.0}],
        )

    assert name == "DN-0001"
    _, kwargs = request_mock.call_args
    assert kwargs["method"] == "POST"
    assert kwargs["path"] == "/api/resource/Delivery Note"
    assert kwargs["json_body"]["customer"] == "CUST-1"
    assert kwargs["json_body"]["items"][0]["qty"] == 5.0
