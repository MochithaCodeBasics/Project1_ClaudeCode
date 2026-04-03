from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "storage" / "invoice_agent.db"
SCHEMA_PATH = ROOT / "storage" / "schema.sql"

mcp = FastMCP("invoice-sqlite-store")

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "INGESTED": {"FLAGGED", "READY_FOR_APPROVAL"},
    "FLAGGED": {"APPROVED", "REJECTED"},
    "READY_FOR_APPROVAL": {"APPROVED", "REJECTED"},
    "APPROVED": {"POSTED", "POST_FAILED"},
    "POST_FAILED": {"APPROVED", "POSTED"},
    "REJECTED": set(),
    "POSTED": set(),
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _con() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _ensure_db() -> None:
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    con = _con()
    try:
        con.executescript(schema_sql)
        con.commit()
    finally:
        con.close()


@mcp.tool()
def init_storage() -> str:
    """Initialize SQLite storage schema."""
    _ensure_db()
    return f"Initialized storage at {DB_PATH}"


@mcp.tool()
def seed_master_data() -> str:
    """Seed vendor master and purchase orders from seed_data.json."""
    _ensure_db()
    seed_path = ROOT / "seed_data.json"
    data = json.loads(seed_path.read_text(encoding="utf-8"))
    now = _now()

    con = _con()
    try:
        for v in data.get("vendor_master", []):
            con.execute(
                """
                INSERT OR IGNORE INTO vendors (external_id, name, aliases_json, email, bank_account, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    v["id"],
                    v["name"],
                    json.dumps(v.get("aliases", [])),
                    v.get("email"),
                    v.get("bank_account"),
                    v.get("status", "active"),
                    now,
                ),
            )

        for po in data.get("purchase_orders", []):
            con.execute(
                """
                INSERT OR IGNORE INTO purchase_orders (po_number, vendor_external_id, status, total_amount, line_items_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    po["po_number"],
                    po["vendor_id"],
                    po.get("status", "open"),
                    float(po.get("total_amount", 0)),
                    json.dumps(po.get("line_items", [])),
                    now,
                ),
            )
        con.commit()
    finally:
        con.close()

    return "Seeded vendors and purchase orders."


@mcp.tool()
def upsert_invoice(
    *,
    source_ref: str | None,
    filename: str | None,
    vendor_name: str,
    invoice_number: str | None,
    po_number: str | None,
    currency: str = "USD",
    subtotal: float = 0.0,
    tax: float = 0.0,
    total: float = 0.0,
    bank_account: str | None = None,
    status: str = "INGESTED",
    flags: list[str] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Insert a new invoice row."""
    _ensure_db()
    now = _now()
    con = _con()
    try:
        cur = con.execute(
            """
            INSERT INTO invoices (
              source, source_ref, filename, vendor_name, invoice_number, po_number, currency,
              subtotal, tax, total, bank_account, status, flags_json, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "gmail",
                source_ref,
                filename,
                vendor_name,
                invoice_number,
                po_number,
                currency.upper(),
                float(subtotal),
                float(tax),
                float(total),
                bank_account,
                status,
                json.dumps(flags or []),
                notes,
                now,
                now,
            ),
        )
        invoice_id = int(cur.lastrowid)
        con.commit()
    finally:
        con.close()
    return {"invoice_id": invoice_id, "status": status}


@mcp.tool()
def list_invoices(status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """List invoices, optionally filtered by status."""
    _ensure_db()
    lim = max(1, min(limit, 500))
    con = _con()
    try:
        if status:
            rows = con.execute(
                """
                SELECT * FROM invoices WHERE status = ? ORDER BY id DESC LIMIT ?
                """,
                (status, lim),
            ).fetchall()
        else:
            rows = con.execute(
                """
                SELECT * FROM invoices ORDER BY id DESC LIMIT ?
                """,
                (lim,),
            ).fetchall()
    finally:
        con.close()
    out: list[dict[str, Any]] = []
    for r in rows:
        item = dict(r)
        item["flags"] = json.loads(item.pop("flags_json") or "[]")
        out.append(item)
    return out


@mcp.tool()
def transition_invoice_status(invoice_id: int, target_status: str, actor: str = "finance_admin", note: str = "") -> dict[str, Any]:
    """Move invoice to a new status using guarded transitions."""
    _ensure_db()
    con = _con()
    try:
        row = con.execute("SELECT id, status FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if not row:
            raise ValueError(f"Invoice {invoice_id} not found")
        current = str(row["status"])
        if target_status not in ALLOWED_TRANSITIONS.get(current, set()):
            raise ValueError(f"Invalid transition: {current} -> {target_status}")

        now = _now()
        con.execute(
            "UPDATE invoices SET status = ?, updated_at = ? WHERE id = ?",
            (target_status, now, invoice_id),
        )
        con.execute(
            """
            INSERT INTO audit_logs (invoice_id, action, actor, details_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (invoice_id, f"STATUS_CHANGED_TO_{target_status}", actor, json.dumps({"note": note}), now),
        )
        con.commit()
    finally:
        con.close()
    return {"invoice_id": invoice_id, "from": current, "to": target_status}


@mcp.tool()
def add_audit_log(invoice_id: int | None, action: str, actor: str, details: dict[str, Any] | None = None) -> str:
    """Write an audit log entry."""
    _ensure_db()
    con = _con()
    try:
        con.execute(
            """
            INSERT INTO audit_logs (invoice_id, action, actor, details_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (invoice_id, action, actor, json.dumps(details or {}), _now()),
        )
        con.commit()
    finally:
        con.close()
    return "audit_log_added"


@mcp.tool()
def get_report_summary(year: int, month: int) -> dict[str, Any]:
    """Get period totals from MCP storage."""
    _ensure_db()
    start = f"{year:04d}-{month:02d}-01"
    end_month = 1 if month == 12 else month + 1
    end_year = year + 1 if month == 12 else year
    end = f"{end_year:04d}-{end_month:02d}-01"

    con = _con()
    try:
        rows = con.execute(
            """
            SELECT status, COUNT(*) AS cnt, COALESCE(SUM(total), 0) AS total
            FROM invoices
            WHERE created_at >= ? AND created_at < ?
            GROUP BY status
            """,
            (start, end),
        ).fetchall()
    finally:
        con.close()

    by_status = {str(r["status"]): {"count": int(r["cnt"]), "total": float(r["total"])} for r in rows}
    total_payables = sum(v["total"] for v in by_status.values())
    approved_total = by_status.get("APPROVED", {}).get("total", 0.0) + by_status.get("POSTED", {}).get("total", 0.0)
    rejected_total = by_status.get("REJECTED", {}).get("total", 0.0)
    pending_total = total_payables - approved_total - rejected_total

    return {
        "period": f"{year:04d}-{month:02d}",
        "invoice_count": sum(v["count"] for v in by_status.values()),
        "total_payables": round(total_payables, 2),
        "approved_total": round(approved_total, 2),
        "rejected_total": round(rejected_total, 2),
        "pending_total": round(pending_total, 2),
        "status_breakdown": by_status,
    }


if __name__ == "__main__":
    mcp.run()

