from app.services.picklist_process import (
    PickListProcessError,
    build_pick_list_preview,
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
