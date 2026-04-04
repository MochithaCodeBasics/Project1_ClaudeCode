from __future__ import annotations

import base64
import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "storage" / "invoice_agent.db"

QBO_ENV = os.getenv("QBO_ENV", "production").strip().lower()
QBO_CLIENT_ID = os.getenv("QBO_CLIENT_ID", "").strip()
QBO_CLIENT_SECRET = os.getenv("QBO_CLIENT_SECRET", "").strip()
QBO_REALM_ID = os.getenv("QBO_REALM_ID", "").strip()
QBO_REFRESH_TOKEN = os.getenv("QBO_REFRESH_TOKEN", "").strip()
QBO_ACCESS_TOKEN = os.getenv("QBO_ACCESS_TOKEN", "").strip()
QBO_TOKEN_FILE = os.getenv("QBO_TOKEN_FILE", str(ROOT / ".qbo_tokens.json")).strip()

if QBO_ENV == "sandbox":
    QBO_API_BASE = "https://sandbox-quickbooks.api.intuit.com"
else:
    QBO_API_BASE = "https://quickbooks.api.intuit.com"

QBO_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"

mcp = FastMCP("invoice-qbo-direct")


class QboApiError(Exception):
    def __init__(self, status_code: int, detail: Any):
        super().__init__(f"QBO API error: {status_code}")
        self.status_code = status_code
        self.detail = detail


class QboClient:
    def __init__(self) -> None:
        self.client_id = QBO_CLIENT_ID
        self.client_secret = QBO_CLIENT_SECRET
        self.realm_id = QBO_REALM_ID
        self.access_token = QBO_ACCESS_TOKEN or None
        self.refresh_token = QBO_REFRESH_TOKEN or None
        self._load_tokens_file()

    def _load_tokens_file(self) -> None:
        token_path = Path(QBO_TOKEN_FILE)
        if not token_path.exists():
            return
        try:
            payload = json.loads(token_path.read_text(encoding="utf-8"))
        except Exception:
            return
        access = str(payload.get("access_token", "")).strip()
        refresh = str(payload.get("refresh_token", "")).strip()
        realm = str(payload.get("realm_id", "")).strip()
        if access:
            self.access_token = access
        if refresh:
            self.refresh_token = refresh
        if realm and not self.realm_id:
            self.realm_id = realm

    def _save_tokens_file(self) -> None:
        token_path = Path(QBO_TOKEN_FILE)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "realm_id": self.realm_id,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        token_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _check_config(self) -> None:
        missing: list[str] = []
        if not self.client_id:
            missing.append("QBO_CLIENT_ID")
        if not self.client_secret:
            missing.append("QBO_CLIENT_SECRET")
        if not self.realm_id:
            missing.append("QBO_REALM_ID")
        if missing:
            raise ValueError(f"Missing required env vars: {', '.join(missing)}")

    def refresh_access_token(self) -> dict[str, Any]:
        self._check_config()
        if not self.refresh_token:
            raise ValueError("Missing QBO refresh token. Set QBO_REFRESH_TOKEN or token file.")

        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode("utf-8")).decode("utf-8")
        headers = {
            "Authorization": f"Basic {basic}",
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {"grant_type": "refresh_token", "refresh_token": self.refresh_token}

        with httpx.Client(timeout=45.0) as http:
            resp = http.post(QBO_TOKEN_URL, headers=headers, data=data)

        payload: Any = resp.json() if "application/json" in (resp.headers.get("content-type") or "") else {"raw": resp.text}
        if resp.status_code >= 400:
            raise QboApiError(resp.status_code, payload)

        self.access_token = str(payload.get("access_token") or "").strip() or None
        new_refresh = str(payload.get("refresh_token") or "").strip() or None
        if new_refresh:
            self.refresh_token = new_refresh
        self._save_tokens_file()
        return {
            "token_type": payload.get("token_type"),
            "expires_in": payload.get("expires_in"),
            "x_refresh_token_expires_in": payload.get("x_refresh_token_expires_in"),
            "realm_id": self.realm_id,
            "token_file": str(Path(QBO_TOKEN_FILE).resolve()),
        }

    def _auth_headers(self) -> dict[str, str]:
        if not self.access_token:
            self.refresh_access_token()
        if not self.access_token:
            raise ValueError("No access token available after refresh.")
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }

    def request(self, method: str, path: str, body: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> Any:
        self._check_config()
        url = f"{QBO_API_BASE}{path}"
        headers = self._auth_headers()
        if body is not None:
            headers["Content-Type"] = "application/json"

        with httpx.Client(timeout=45.0) as http:
            resp = http.request(method=method, url=url, headers=headers, params=params, json=body)

        payload: Any = resp.json() if "application/json" in (resp.headers.get("content-type") or "") else {"raw": resp.text}
        if resp.status_code == 401:
            self.refresh_access_token()
            headers = self._auth_headers()
            if body is not None:
                headers["Content-Type"] = "application/json"
            with httpx.Client(timeout=45.0) as http_retry:
                resp = http_retry.request(method=method, url=url, headers=headers, params=params, json=body)
            payload = resp.json() if "application/json" in (resp.headers.get("content-type") or "") else {"raw": resp.text}
        if resp.status_code >= 400:
            raise QboApiError(resp.status_code, payload)
        return payload

    def query(self, sql: str) -> Any:
        self._check_config()
        path = f"/v3/company/{self.realm_id}/query"
        params = {"query": sql, "minorversion": "75"}
        headers = self._auth_headers()
        with httpx.Client(timeout=45.0) as http:
            resp = http.get(f"{QBO_API_BASE}{path}", headers=headers, params=params)
        payload: Any = resp.json() if "application/json" in (resp.headers.get("content-type") or "") else {"raw": resp.text}
        if resp.status_code == 401:
            self.refresh_access_token()
            headers = self._auth_headers()
            with httpx.Client(timeout=45.0) as http_retry:
                resp = http_retry.get(f"{QBO_API_BASE}{path}", headers=headers, params=params)
            payload = resp.json() if "application/json" in (resp.headers.get("content-type") or "") else {"raw": resp.text}
        if resp.status_code >= 400:
            raise QboApiError(resp.status_code, payload)
        return payload


qbo = QboClient()


def _ok(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data}


def _fail(err: Exception) -> dict[str, Any]:
    if isinstance(err, QboApiError):
        return {"ok": False, "status_code": err.status_code, "detail": err.detail}
    return {"ok": False, "error": str(err)}


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _now() -> str:
    return datetime.now(UTC).isoformat()


@mcp.tool()
def qbo_auth_status() -> dict[str, Any]:
    """Show whether required QBO credentials appear to be configured."""
    return _ok(
        {
            "qbo_env": QBO_ENV,
            "api_base": QBO_API_BASE,
            "has_client_id": bool(qbo.client_id),
            "has_client_secret": bool(qbo.client_secret),
            "has_realm_id": bool(qbo.realm_id),
            "has_access_token": bool(qbo.access_token),
            "has_refresh_token": bool(qbo.refresh_token),
            "token_file": str(Path(QBO_TOKEN_FILE).resolve()),
        }
    )


@mcp.tool()
def qbo_refresh_token() -> dict[str, Any]:
    """Refresh QBO access token using refresh token and persist to token file."""
    try:
        return _ok(qbo.refresh_access_token())
    except Exception as err:
        return _fail(err)


@mcp.tool()
def qbo_find_vendor_by_name(display_name: str) -> dict[str, Any]:
    """Find QBO vendors by display name."""
    try:
        safe_name = display_name.replace("'", "''")
        sql = f"SELECT Id, DisplayName, Active FROM Vendor WHERE DisplayName = '{safe_name}'"
        payload = qbo.query(sql)
        vendors = (payload.get("QueryResponse") or {}).get("Vendor", [])
        return _ok(vendors)
    except Exception as err:
        return _fail(err)


@mcp.tool()
def qbo_list_recent_bills(limit: int = 20) -> dict[str, Any]:
    """List recent QBO bills."""
    try:
        lim = max(1, min(limit, 100))
        sql = f"SELECT * FROM Bill STARTPOSITION 1 MAXRESULTS {lim}"
        payload = qbo.query(sql)
        bills = (payload.get("QueryResponse") or {}).get("Bill", [])
        return _ok(bills)
    except Exception as err:
        return _fail(err)


@mcp.tool()
def qbo_create_bill(
    vendor_id: str,
    expense_account_id: str,
    amount: float,
    description: str = "Invoice expense",
    doc_number: str | None = None,
    txn_date: str | None = None,
    due_date: str | None = None,
    private_note: str | None = None,
) -> dict[str, Any]:
    """Create a QBO Bill directly using AccountBasedExpenseLineDetail."""
    try:
        path = f"/v3/company/{qbo.realm_id}/bill"
        today = datetime.now(UTC).date()
        txn = txn_date or today.isoformat()
        due = due_date or (today + timedelta(days=30)).isoformat()
        body: dict[str, Any] = {
            "VendorRef": {"value": str(vendor_id)},
            "TxnDate": txn,
            "DueDate": due,
            "Line": [
                {
                    "Amount": round(float(amount), 2),
                    "DetailType": "AccountBasedExpenseLineDetail",
                    "Description": description,
                    "AccountBasedExpenseLineDetail": {"AccountRef": {"value": str(expense_account_id)}},
                }
            ],
        }
        if doc_number:
            body["DocNumber"] = doc_number
        if private_note:
            body["PrivateNote"] = private_note

        payload = qbo.request("POST", path, body=body, params={"minorversion": 75})
        bill = payload.get("Bill", payload)
        return _ok(bill)
    except Exception as err:
        return _fail(err)


@mcp.tool()
def qbo_post_invoice_from_sqlite(
    invoice_id: int,
    vendor_id: str,
    expense_account_id: str,
    mark_posted_locally: bool = True,
) -> dict[str, Any]:
    """Create a QBO bill from local SQLite invoice row and optionally mark it POSTED."""
    try:
        con = _db()
        try:
            row = con.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
            if not row:
                raise ValueError(f"Invoice {invoice_id} not found in SQLite store")

            invoice_number = str(row["invoice_number"] or f"INV-{invoice_id}")
            amount = float(row["total"] or 0.0)
            if amount <= 0:
                raise ValueError("Invoice total must be > 0 to post to QBO")

            vendor_name = str(row["vendor_name"] or "Unknown Vendor")
            path = f"/v3/company/{qbo.realm_id}/bill"
            body = {
                "VendorRef": {"value": str(vendor_id)},
                "TxnDate": datetime.now(UTC).date().isoformat(),
                "DueDate": (datetime.now(UTC).date() + timedelta(days=30)).isoformat(),
                "DocNumber": invoice_number,
                "PrivateNote": f"Posted from Project_1 SQLite invoice_id={invoice_id}",
                "Line": [
                    {
                        "Amount": round(amount, 2),
                        "DetailType": "AccountBasedExpenseLineDetail",
                        "Description": f"{vendor_name} / {invoice_number}",
                        "AccountBasedExpenseLineDetail": {"AccountRef": {"value": str(expense_account_id)}},
                    }
                ],
            }
            payload = qbo.request("POST", path, body=body, params={"minorversion": 75})
            bill = payload.get("Bill", payload)
            qbo_bill_id = str(bill.get("Id") or "")

            if mark_posted_locally:
                now = _now()
                con.execute(
                    """
                    UPDATE invoices
                    SET status = ?, notes = COALESCE(notes, '') || ?, updated_at = ?
                    WHERE id = ?
                    """,
                    ("POSTED", f"\nQBO bill id: {qbo_bill_id}", now, invoice_id),
                )
                con.execute(
                    """
                    INSERT INTO audit_logs (invoice_id, action, actor, details_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        invoice_id,
                        "POSTED_TO_QBO_DIRECT",
                        "finance_admin",
                        json.dumps({"qbo_bill_id": qbo_bill_id, "vendor_id": vendor_id}),
                        now,
                    ),
                )
                con.commit()
        finally:
            con.close()

        return _ok({"invoice_id": invoice_id, "qbo_bill_id": qbo_bill_id, "bill": bill})
    except Exception as err:
        return _fail(err)


if __name__ == "__main__":
    mcp.run()
