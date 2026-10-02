from app.formatters import safe_order_text


def test_order_text_does_not_expose_unknown_private_fields() -> None:
    text = safe_order_text(
        {
            "code": "ORD-1",
            "status": "in_processing",
            "paid_amount": "100000",
            "balance_amount": "50000",
            "cost_amount": "1",
            "internal_note": "private",
        }
    )
    assert "ORD-1" in text
    assert "private" not in text
    assert "cost_amount" not in text
