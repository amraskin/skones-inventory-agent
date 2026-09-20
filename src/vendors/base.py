"""Common interface all vendor site integrations implement."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class VendorProduct:
    name: str
    price: float | None
    in_stock: bool | None
    unit: str | None = None
    url: str | None = None
    raw_match_name: str | None = None  # the name as it appears on the vendor site
    is_substitute: bool = False
    substitute_for: str | None = None  # original product name, when is_substitute is True


class VendorClient:
    """Subclass per vendor (e.g. NabisClient). Each one knows how to log in
    to that vendor's site and look up current price/availability for the
    products reorder.py says you need."""

    name: str = "base"

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def login(self) -> None:
        raise NotImplementedError

    def search_product(self, product_name: str) -> VendorProduct | None:
        raise NotImplementedError

    def find_substitute(
        self, category: str | None, brand: str | None, exclude_name: str
    ) -> VendorProduct | None:
        """Find an in-stock alternative in the same category+brand line,
        for when search_product's exact match is out of stock or missing."""
        raise NotImplementedError

    def close(self) -> None:
        pass
