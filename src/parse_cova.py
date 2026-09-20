"""Parse COVA POS exports (Sales by Invoice/Product, inventory/stock-on-hand)
into normalized DataFrames the rest of the pipeline can use.

COVA lets you customize export columns, so headers vary by report/config.
config/column_map.yaml lists the header names we look for; add yours if a
real export doesn't match.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_column_map() -> dict:
    path = REPO_ROOT / "config" / "column_map.yaml"
    if not path.exists():
        path = REPO_ROOT / "config" / "column_map.example.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


def _read_table(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xls"):
        return pd.read_excel(path)
    return pd.read_csv(path)


OPTIONAL_FIELDS = {"brand", "date"}


def _resolve_columns(df: pd.DataFrame, field_map: dict) -> dict[str, str | None]:
    """Find which actual column in df matches each logical field's candidate
    names. Fields in OPTIONAL_FIELDS may be absent (resolve to None); any
    other missing field is an error."""
    resolved = {}
    missing = []
    for field, candidates in field_map.items():
        match = next((c for c in candidates if c in df.columns), None)
        if match:
            resolved[field] = match
        elif field in OPTIONAL_FIELDS:
            resolved[field] = None
        else:
            missing.append(field)
    if missing:
        raise ValueError(
            f"Could not find columns for: {missing}. "
            f"Available columns: {list(df.columns)}. "
            "Add the real header name to config/column_map.yaml."
        )
    return resolved


def _apply_column_map(df: pd.DataFrame, cols: dict[str, str | None]) -> pd.DataFrame:
    present = {v: k for k, v in cols.items() if v is not None}
    out = df.rename(columns=present)[list(present.values())]
    for field, source in cols.items():
        if source is None:
            out[field] = None
    return out[list(cols.keys())]


def parse_sales_export(path: str | Path) -> pd.DataFrame:
    """Returns columns: product, sku, category, brand, quantity_sold, date"""
    df = _read_table(path)
    mapping = load_column_map()["sales_export"]
    cols = _resolve_columns(df, mapping)
    out = _apply_column_map(df, cols)
    out["quantity_sold"] = pd.to_numeric(out["quantity_sold"], errors="coerce").fillna(0)
    if out["date"].notna().any():
        out["date"] = pd.to_datetime(out["date"], errors="coerce")
    return out


def parse_inventory_export(path: str | Path) -> pd.DataFrame:
    """Returns columns: product, sku, category, brand, quantity_on_hand"""
    df = _read_table(path)
    mapping = load_column_map()["inventory_export"]
    cols = _resolve_columns(df, mapping)
    out = _apply_column_map(df, cols)
    out["quantity_on_hand"] = pd.to_numeric(out["quantity_on_hand"], errors="coerce").fillna(0)
    return out


def summarize_sales(sales_df: pd.DataFrame) -> pd.DataFrame:
    """Collapse a sales export (possibly one row per invoice line) into
    total quantity_sold and days_covered per product."""
    days_covered = 1
    if "date" in sales_df.columns and sales_df["date"].notna().any():
        days_covered = max((sales_df["date"].max() - sales_df["date"].min()).days + 1, 1)

    grouped = (
        sales_df.groupby(["product", "sku", "category", "brand"], dropna=False)["quantity_sold"]
        .sum()
        .reset_index()
    )
    grouped["days_covered"] = days_covered
    grouped["daily_sales_velocity"] = grouped["quantity_sold"] / days_covered
    return grouped
