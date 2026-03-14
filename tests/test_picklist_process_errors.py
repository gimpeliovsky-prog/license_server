from app.services.picklist_process import PickListProcessError, classify_erp_failure


def test_classify_erp_failure_detects_missing_required_fields():
    reason_code, retryable, erp_refused = classify_erp_failure(
        "Mandatory fields required in Delivery Note",
        422,
    )

    assert reason_code == "missing_required_fields"
    assert retryable is False
    assert erp_refused is True


def test_classify_erp_failure_detects_erp_unavailable():
    reason_code, retryable, erp_refused = classify_erp_failure(
        "Internal Server Error",
        500,
    )

    assert reason_code == "erp_unavailable"
    assert retryable is True
    assert erp_refused is False


def test_picklist_process_error_to_detail_exposes_machine_readable_fields():
    detail = PickListProcessError(
        "ERP refused document creation",
        status_code=409,
        reason_code="erp_refused",
        retryable=False,
        erp_refused=True,
    ).to_detail()

    assert detail == {
        "message": "ERP refused document creation",
        "reason_code": "erp_refused",
        "retryable": False,
        "erp_refused": True,
    }
