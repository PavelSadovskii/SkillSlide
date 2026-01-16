from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import Flask, redirect, render_template, request, url_for
from PIL import Image
import pytesseract

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "receipts.db"
UPLOAD_DIR = BASE_DIR / "data" / "uploads"

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = str(UPLOAD_DIR)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024


SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_name TEXT NOT NULL,
    purchase_date TEXT,
    total REAL,
    raw_text TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    quantity REAL,
    price REAL,
    line_total REAL,
    FOREIGN KEY(receipt_id) REFERENCES receipts(id)
);
"""


DATE_PATTERNS = [
    re.compile(r"(?P<date>\d{2}[./]\d{2}[./]\d{4})"),
    re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})"),
]

TOTAL_PATTERN = re.compile(r"(итого|сумма|total)")
MONEY_PATTERN = re.compile(r"\d+[.,]\d{2}")


class ReceiptItem:
    def __init__(self, name: str, quantity: float | None, price: float | None, line_total: float | None):
        self.name = name
        self.quantity = quantity
        self.price = price
        self.line_total = line_total


class ParsedReceipt:
    def __init__(self, store_name: str, purchase_date: str | None, total: float | None, raw_text: str):
        self.store_name = store_name
        self.purchase_date = purchase_date
        self.total = total
        self.raw_text = raw_text
        self.items: list[ReceiptItem] = []


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(SCHEMA)


def parse_money(value: str) -> float:
    return float(value.replace(",", "."))


def parse_receipt_text(text: str) -> ParsedReceipt:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    store_name = lines[0] if lines else "Неизвестный магазин"
    purchase_date: str | None = None
    total: float | None = None

    for line in lines:
        if not purchase_date:
            for pattern in DATE_PATTERNS:
                match = pattern.search(line)
                if match:
                    purchase_date = match.group("date")
                    break
        if total is None and TOTAL_PATTERN.search(line.lower()):
            money = MONEY_PATTERN.findall(line)
            if money:
                total = parse_money(money[-1])

    receipt = ParsedReceipt(store_name, purchase_date, total, text)

    for line in lines:
        if TOTAL_PATTERN.search(line.lower()):
            continue
        if any(pattern.search(line) for pattern in DATE_PATTERNS):
            continue
        money_values = MONEY_PATTERN.findall(line)
        if not money_values:
            continue

        line_total = parse_money(money_values[-1])
        price = parse_money(money_values[-2]) if len(money_values) > 1 else line_total
        name = MONEY_PATTERN.sub("", line)
        name = re.sub(r"\s{2,}", " ", name).strip(" -")
        if not name:
            continue
        receipt.items.append(ReceiptItem(name=name, quantity=None, price=price, line_total=line_total))

    return receipt


def ocr_image(file_path: Path) -> str:
    image = Image.open(file_path).convert("RGB")
    return pytesseract.image_to_string(image, lang="rus+eng")


def save_receipt(parsed: ParsedReceipt) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO receipts (store_name, purchase_date, total, raw_text, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                parsed.store_name,
                parsed.purchase_date,
                parsed.total,
                parsed.raw_text,
                datetime.utcnow().isoformat(timespec="seconds"),
            ),
        )
        receipt_id = cursor.lastrowid
        for item in parsed.items:
            cursor.execute(
                """
                INSERT INTO items (receipt_id, name, quantity, price, line_total)
                VALUES (?, ?, ?, ?, ?)
                """,
                (receipt_id, item.name, item.quantity, item.price, item.line_total),
            )
        conn.commit()
    return int(receipt_id)


def fetch_receipts() -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, store_name, purchase_date, total, created_at
            FROM receipts
            ORDER BY created_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def fetch_receipt(receipt_id: int) -> dict | None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        receipt = conn.execute(
            """
            SELECT id, store_name, purchase_date, total, raw_text, created_at
            FROM receipts
            WHERE id = ?
            """,
            (receipt_id,),
        ).fetchone()
        if not receipt:
            return None
        items = conn.execute(
            """
            SELECT id, name, quantity, price, line_total
            FROM items
            WHERE receipt_id = ?
            ORDER BY id
            """,
            (receipt_id,),
        ).fetchall()
    data = dict(receipt)
    data["items"] = [dict(item) for item in items]
    return data


@app.route("/")
def index() -> str:
    receipts = fetch_receipts()
    return render_template("index.html", receipts=receipts)


@app.route("/upload", methods=["POST"])
def upload() -> str:
    file = request.files.get("receipt")
    if not file or not file.filename:
        return redirect(url_for("index"))

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{datetime.utcnow().timestamp()}_{file.filename}"
    file_path = UPLOAD_DIR / filename
    file.save(file_path)

    text = ocr_image(file_path)
    parsed = parse_receipt_text(text)
    receipt_id = save_receipt(parsed)
    return redirect(url_for("receipt_detail", receipt_id=receipt_id))


@app.route("/receipt/<int:receipt_id>")
def receipt_detail(receipt_id: int) -> str:
    receipt = fetch_receipt(receipt_id)
    if not receipt:
        return redirect(url_for("index"))
    return render_template("receipt.html", receipt=receipt)


@app.route("/receipt/<int:receipt_id>/update", methods=["POST"])
def update_receipt(receipt_id: int) -> str:
    store_name = request.form.get("store_name") or "Неизвестный магазин"
    purchase_date = request.form.get("purchase_date") or None
    total = request.form.get("total") or None
    total_value = float(total.replace(",", ".")) if total else None

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            UPDATE receipts
            SET store_name = ?, purchase_date = ?, total = ?
            WHERE id = ?
            """,
            (store_name, purchase_date, total_value, receipt_id),
        )
        conn.commit()

    return redirect(url_for("receipt_detail", receipt_id=receipt_id))


@app.route("/receipt/<int:receipt_id>/items/<int:item_id>/update", methods=["POST"])
def update_item(receipt_id: int, item_id: int) -> str:
    name = request.form.get("name") or ""
    quantity = request.form.get("quantity") or None
    price = request.form.get("price") or None
    line_total = request.form.get("line_total") or None

    def parse_optional(value: str | None) -> float | None:
        if value is None or value == "":
            return None
        return float(value.replace(",", "."))

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            UPDATE items
            SET name = ?, quantity = ?, price = ?, line_total = ?
            WHERE id = ? AND receipt_id = ?
            """,
            (
                name,
                parse_optional(quantity),
                parse_optional(price),
                parse_optional(line_total),
                item_id,
                receipt_id,
            ),
        )
        conn.commit()

    return redirect(url_for("receipt_detail", receipt_id=receipt_id))


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True)
