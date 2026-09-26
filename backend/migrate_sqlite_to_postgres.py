"""One-time, transactional copy of the existing NutriDulce SQLite data to PostgreSQL."""
from __future__ import annotations

import sqlite3
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.server import connect, init_db  # noqa: E402

SQLITE_PATH = Path(os.environ.get("SQLITE_PATH", ROOT / "data" / "nutridulce.sqlite3"))

# Parent rows come first so PostgreSQL foreign-key checks remain enabled throughout the copy.
TABLE_COLUMNS = {
    "users": ("id", "username", "password_hash", "created_at"),
    "payment_methods": ("id", "name"),
    "products": ("id", "name", "description", "price", "stock", "image", "active", "low_stock_threshold", "created_at", "updated_at"),
    "customers": ("id", "name", "phone", "address", "created_at"),
    "orders": ("id", "code", "customer_id", "payment_method", "delivery_address", "notes", "status", "total", "created_at", "updated_at"),
    "order_items": ("id", "order_id", "product_id", "product_name", "quantity", "unit_price"),
    "sales": ("id", "order_id", "customer_id", "payment_method", "status", "total", "created_at"),
    "sale_items": ("id", "sale_id", "product_id", "product_name", "quantity", "unit_price"),
    "stock_movements": ("id", "product_id", "product_name", "quantity_change", "reason", "order_id", "created_at"),
    "expenses": ("id", "name", "category", "amount", "date", "description", "created_at"),
}
TIMESTAMP_COLUMNS = {"created_at", "updated_at"}
IDENTITY_TABLES = tuple(TABLE_COLUMNS)


def convert_value(column: str, value):
    if value is None:
        return None
    if column in TIMESTAMP_COLUMNS:
        if isinstance(value, datetime):
            parsed = value
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    if column == "date":
        return value if isinstance(value, date) else date.fromisoformat(str(value))
    return value


def main():
    if not SQLITE_PATH.is_file():
        raise SystemExit(f"No encontré la base SQLite de origen: {SQLITE_PATH}")

    # Create only the target schema first; this mode does not seed demo products/users.
    init_db(seed_data=False)
    source = sqlite3.connect(SQLITE_PATH)
    source.row_factory = sqlite3.Row
    try:
        existing_tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = sorted(set(TABLE_COLUMNS) - existing_tables)
        if missing:
            raise SystemExit("La base SQLite no contiene las tablas esperadas: " + ", ".join(missing))

        with connect() as target:
            # Refuse a non-empty target to prevent duplicate IDs or overwriting live data.
            occupied = []
            for table in TABLE_COLUMNS:
                count = target.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]
                if count:
                    occupied.append(table)
            if occupied:
                raise SystemExit("La base PostgreSQL ya tiene datos; no se importó nada. Tablas con datos: " + ", ".join(occupied))

            totals = {}
            for table, columns in TABLE_COLUMNS.items():
                column_sql = ",".join(columns)
                source_rows = source.execute(f"SELECT {column_sql} FROM {table} ORDER BY id").fetchall()
                if not source_rows:
                    totals[table] = 0
                    continue
                placeholders = ",".join(["%s"] * len(columns))
                insert_sql = f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders})"
                rows = [tuple(convert_value(column, row[column]) for column in columns) for row in source_rows]

                with target.cursor() as cur:
                    cur.executemany(insert_sql, rows)

                totals[table] = len(rows)

            # Explicitly copied IDs must advance each identity sequence to avoid future collisions.
            for table in IDENTITY_TABLES:
                max_id = target.execute(f"SELECT MAX(id) AS max_id FROM {table}").fetchone()["max_id"]
                if max_id is None:
                    continue
                sequence = target.execute("SELECT pg_get_serial_sequence(%s, 'id') AS sequence", (f"public.{table}",)).fetchone()["sequence"]
                if sequence:
                    target.execute("SELECT setval(%s::regclass, %s, true)", (sequence, max_id))

        # Apply harmless app defaults and legacy image-key migrations after preserving source rows.
        init_db(seed_data=True)
        print("Importación completada. Filas copiadas:")
        for table, count in totals.items():
            print(f"  {table}: {count}")
    finally:
        source.close()


if __name__ == "__main__":
    main()
