"""Multi-modal invoice ingestion: turn an invoice image into structured, validated data.

Support and ops work runs on documents — invoices, receipts, screenshots. This module takes
an invoice PNG, sends it to the vision-capable balanced-tier model, and returns a typed
`InvoiceExtract` the agent can act on: validated fields instead of brittle free-text parsing.
It mirrors the "intelligent document processing" interview case.

`make_sample_invoice` renders a deterministic demo invoice with Pillow, so the extractor (and
the __main__ demo) have something realistic to read without committing a binary fixture.
"""

from __future__ import annotations

import base64
from pathlib import Path

from langchain_core.messages import HumanMessage
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

from kompass.models.router import pick


class InvoiceExtract(BaseModel):
    """Structured fields extracted from an invoice image."""

    number: str = Field(description="invoice number exactly as printed, e.g. INV-2026-0042")
    date: str = Field(description="invoice date in ISO format YYYY-MM-DD")
    vendor: str = Field(description="the issuing company name")
    total_eur: float = Field(description="grand total in euros as a number, no currency symbol")
    line_items: list[str] = Field(
        description='one line per item, formatted "desc x qty @ unit", '
        'e.g. "ErgoDesk Monitor Arm x1 @ 79.99"'
    )


def make_sample_invoice(
    path: str | Path, number: str = "INV-2026-0042", total: str = "189.99"
) -> Path:
    """Render a simple but realistic invoice PNG for the demo and the test.

    Deterministic (same bytes every run): draws the ACME GmbH header, the invoice number and
    date, a two-row line-item table, and the euro total, using Pillow's default font so no
    external font file is needed.
    """
    path = Path(path)
    title = ImageFont.load_default(size=32)
    head = ImageFont.load_default(size=22)
    body = ImageFont.load_default(size=20)

    img = Image.new("RGB", (720, 470), "white")
    draw = ImageDraw.Draw(img)

    draw.text((40, 30), "ACME GmbH", fill="black", font=title)
    draw.text((40, 92), f"Invoice {number}", fill="black", font=head)
    draw.text((40, 124), "Date: 2026-07-01", fill="black", font=body)

    draw.line((40, 170, 680, 170), fill="black", width=2)
    draw.text((48, 180), "Description", fill="black", font=head)
    draw.text((470, 180), "Qty", fill="black", font=head)
    draw.text((570, 180), "Unit EUR", fill="black", font=head)
    draw.line((40, 212, 680, 212), fill="black", width=1)

    draw.text((48, 224), "ErgoDesk Monitor Arm", fill="black", font=body)
    draw.text((470, 224), "1", fill="black", font=body)
    draw.text((570, 224), "79.99", fill="black", font=body)

    draw.text((48, 258), "AcousticPro USB Microphone", fill="black", font=body)
    draw.text((470, 258), "1", fill="black", font=body)
    draw.text((570, 258), "110.00", fill="black", font=body)

    draw.line((40, 300, 680, 300), fill="black", width=1)
    draw.text((40, 330), f"TOTAL EUR {total}", fill="black", font=title)

    img.save(path)
    return path


def extract_invoice(image_path: str | Path) -> InvoiceExtract:
    """Extract structured invoice fields from a PNG with the vision-capable balanced model.

    The image is base64-encoded into a data URI and sent as an image_url content block next to
    the text instruction; structured output validates the reply into an InvoiceExtract.
    """
    b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
    message = HumanMessage(
        content=[
            {
                "type": "text",
                "text": "Read this invoice image and extract its fields: number, date, "
                "vendor, total in euros, and one entry per line item.",
            },
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ]
    )
    return pick("balanced").with_structured_output(InvoiceExtract).invoke([message])


if __name__ == "__main__":
    import tempfile

    sample = make_sample_invoice(Path(tempfile.gettempdir()) / "kompass_sample_invoice.png")
    print(f"Sample invoice rendered to: {sample}")
    print(extract_invoice(sample).model_dump_json(indent=2))
