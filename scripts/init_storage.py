from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "storage" / "invoice_agent.db"
SCHEMA_PATH = ROOT / "storage" / "schema.sql"


def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    con = sqlite3.connect(DB_PATH)
    try:
        con.executescript(schema_sql)
        con.commit()
    finally:
        con.close()
    print(f"Initialized SQLite storage at: {DB_PATH}")


if __name__ == "__main__":
    main()

