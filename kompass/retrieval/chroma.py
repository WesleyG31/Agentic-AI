"""Local-only Chroma configuration with product telemetry disabled by construction."""

from __future__ import annotations

from chromadb.config import Settings
from chromadb.telemetry.product import ProductTelemetryClient, ProductTelemetryEvent
from overrides import override


class NoopProductTelemetry(ProductTelemetryClient):
    """Accept Chroma lifecycle events without network, identity files, or vendor calls."""

    @override
    def capture(self, event: ProductTelemetryEvent) -> None:
        return None


def local_chroma_settings() -> Settings:
    return Settings(
        anonymized_telemetry=False,
        chroma_product_telemetry_impl=(
            "kompass.retrieval.chroma.NoopProductTelemetry"
        ),
    )
