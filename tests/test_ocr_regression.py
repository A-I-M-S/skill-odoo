from pathlib import Path

from openclaw_bot_cli.extraction import extract_text


def test_fairprice_receipt_total_is_visible():
    text, kind = extract_text(Path(__file__).parent / "fixtures" / "fairprice_receipt.jpg")
    assert kind == "ocr_image"
    assert "FairPrice" in text
    assert "33.60" in text
    assert "VISA" in text
