"""Nabis (https://ny.nabis.com) vendor integration.

Login strategy: reuse a saved browser session (cookies) instead of scripting
raw username/password every run. B2B platforms like Nabis commonly have bot
protection and/or 2FA that a headless login script will trip; logging in
once yourself (via scripts/nabis_login.py, headed, on a machine with normal
internet access) and persisting the session is far more reliable, and only
needs to be repeated when the session expires.

Selectors for the catalog/search page are read from
config/nabis_selectors.yaml - fill that in from the real site (see the
.example.yaml for instructions) before this will actually find products.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from playwright.sync_api import sync_playwright

from .base import VendorClient, VendorProduct

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SESSION_STATE_PATH = REPO_ROOT / "data" / "session_state" / "nabis.json"


def load_selectors() -> dict:
    path = REPO_ROOT / "config" / "nabis_selectors.yaml"
    if not path.exists():
        path = REPO_ROOT / "config" / "nabis_selectors.example.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


class NabisClient(VendorClient):
    name = "nabis"

    def __init__(self, headless: bool = True):
        if not SESSION_STATE_PATH.exists():
            raise RuntimeError(
                f"No saved Nabis session at {SESSION_STATE_PATH}. "
                "Run `python scripts/nabis_login.py` first (from a machine "
                "with real internet access) to log in and save a session."
            )
        self.selectors = load_selectors()
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=headless)
        self._context = self._browser.new_context(storage_state=str(SESSION_STATE_PATH))
        self.page = self._context.new_page()

    def login(self) -> None:
        """No-op: session is already loaded from storage_state. Verifies it's
        still valid and raises if it has expired."""
        cfg = self.selectors["login"]
        self.page.goto(cfg["url"])
        try:
            self.page.wait_for_selector(cfg["logged_in_indicator"], timeout=8000)
        except Exception as e:
            raise RuntimeError(
                "Saved Nabis session looks expired (didn't see the "
                "logged-in indicator after navigating). Re-run "
                "scripts/nabis_login.py to refresh it."
            ) from e

    def _extract_card(self, card, cfg) -> tuple[str | None, float | None, bool | None]:
        name_el = card.query_selector(cfg["product_name"])
        price_el = card.query_selector(cfg["product_price"])
        stock_el = card.query_selector(cfg["product_stock"])

        raw_name = name_el.inner_text().strip() if name_el else None
        price_text = price_el.inner_text().strip() if price_el else None
        stock_text = stock_el.inner_text().strip() if stock_el else None

        price = None
        if price_text:
            digits = "".join(c for c in price_text if c.isdigit() or c == ".")
            price = float(digits) if digits else None

        in_stock = None
        if stock_text:
            in_stock = "out of stock" not in stock_text.lower()

        return raw_name, price, in_stock

    def _search(self, query: str) -> list:
        cfg = self.selectors["catalog"]
        url = cfg["search_url_template"].format(query=query)
        self.page.goto(url)
        self.page.wait_for_timeout(1500)  # TODO: replace with a real wait-for-selector once verified
        return self.page.query_selector_all(cfg["product_card"]), url

    def search_product(self, product_name: str) -> VendorProduct | None:
        cfg = self.selectors["catalog"]
        cards, url = self._search(product_name)
        if not cards:
            return None

        # Take the first result as the best match. TODO once selectors are
        # verified: consider fuzzy-matching product_name against candidates
        # instead of trusting the site's own search ranking.
        raw_name, price, in_stock = self._extract_card(cards[0], cfg)

        return VendorProduct(
            name=product_name,
            price=price,
            in_stock=in_stock,
            url=url,
            raw_match_name=raw_name,
        )

    def find_substitute(
        self, category: str | None, brand: str | None, exclude_name: str
    ) -> VendorProduct | None:
        """Search by category+brand instead of exact product name, and
        return the first in-stock result that isn't the excluded product."""
        cfg = self.selectors["catalog"]
        query = " ".join(part for part in [brand, category] if part)
        if not query:
            return None
        cards, url = self._search(query)

        for card in cards:
            raw_name, price, in_stock = self._extract_card(card, cfg)
            if not raw_name or raw_name == exclude_name:
                continue
            if in_stock is False:
                continue
            return VendorProduct(
                name=raw_name,
                price=price,
                in_stock=in_stock,
                url=url,
                raw_match_name=raw_name,
                is_substitute=True,
                substitute_for=exclude_name,
            )
        return None

    def close(self) -> None:
        self._context.close()
        self._browser.close()
        self._playwright.stop()
