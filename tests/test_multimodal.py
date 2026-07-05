"""Multi-modal ingestion tests: the sample invoice renders to a valid PNG.

LLM-free — exercises only the Pillow rendering, so CI stays offline.
"""

from PIL import Image

from kompass.ingest.multimodal import make_sample_invoice


def test_make_sample_invoice_writes_valid_png(tmp_path):
    path = make_sample_invoice(tmp_path / "invoice.png")
    assert path.exists()
    with Image.open(path) as img:
        assert img.format == "PNG"
        assert img.mode == "RGB"
        assert img.width > 0 and img.height > 0
