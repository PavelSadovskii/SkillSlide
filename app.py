from __future__ import annotations

import cgi
import csv
import os
import re
import sqlite3
import subprocess
from shutil import which
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "receipts.db"
UPLOAD_DIR = BASE_DIR / "data" / "uploads"
STATIC_DIR = BASE_DIR / "static"

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
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.executescript(SCHEMA)


def parse_money(value: str) -> float:
    return float(value.replace(",", "."))


def parse_optional_number(value: str | None) -> float | None:
    if value is None or value == "":
        return None
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
        quantity_match = re.search(r"(?P<quantity>\d+[.,]?\d*)\s*[xх*]\s*\d+[.,]\d{2}", line.lower())
        quantity = None
        if quantity_match:
            quantity = float(quantity_match.group("quantity").replace(",", "."))
        name = MONEY_PATTERN.sub("", line)
        name = re.sub(r"\s{2,}", " ", name).strip(" -")
        if not name:
            continue
        receipt.items.append(ReceiptItem(name=name, quantity=quantity, price=price, line_total=line_total))

    return receipt


def extract_text_from_upload(filename: str, data: bytes) -> tuple[str, str | None]:
    extension = Path(filename).suffix.lower()
    if extension in {".txt", ".csv"}:
        return data.decode("utf-8", errors="ignore"), None
    if extension in {".png", ".jpg", ".jpeg", ".bmp", ".tiff"}:
        if not which("tesseract"):
            return "", "OCR недоступен: установите tesseract-ocr и добавьте его в PATH."
        try:
            completed = subprocess.run(
                ["tesseract", "-", "stdout", "-l", "rus+eng"],
                input=data,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            return completed.stdout.decode("utf-8", errors="ignore"), None
        except subprocess.CalledProcessError:
            return "", "OCR не удалось выполнить для загруженного изображения."
    return "", "Формат файла не поддерживается для распознавания."


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


def redirect(location: str) -> tuple[int, dict[str, str], bytes]:
    return HTTPStatus.SEE_OTHER, {"Location": location}, b""


def html_response(body: str) -> tuple[int, dict[str, str], bytes]:
    return HTTPStatus.OK, {"Content-Type": "text/html; charset=utf-8"}, body.encode("utf-8")


def not_found() -> tuple[int, dict[str, str], bytes]:
    return HTTPStatus.NOT_FOUND, {"Content-Type": "text/plain; charset=utf-8"}, b"Not Found"


def render_index(receipts: list[dict]) -> str:
    items_html = ""
    if receipts:
        list_items = []
        for receipt in receipts:
            list_items.append(
                """
                <li>
                  <a href="/receipt/{receipt_id}">
                    <div class="receipt-title">{store}</div>
                    <div class="receipt-meta">
                      <span>Дата: {date}</span>
                      <span>Сумма: {total}</span>
                    </div>
                  </a>
                </li>
                """.format(
                    receipt_id=receipt["id"],
                    store=receipt["store_name"],
                    date=receipt.get("purchase_date") or "—",
                    total=receipt.get("total") or "—",
                )
            )
        items_html = "<ul class=\"receipt-list\">{}</ul>".format("".join(list_items))
    else:
        items_html = "<p class=\"empty\">Пока нет загруженных чеков.</p>"

    return """
    <!DOCTYPE html>
    <html lang="ru">
    <head>
      <meta charset="UTF-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1" />
      <title>SkillSlide Receipts</title>
      <link rel="stylesheet" href="/static/styles.css" />
    </head>
    <body>
      <main class="container">
        <header class="header">
          <div>
            <h1>Распознавание чеков</h1>
            <p>Загрузите чек или текст, чтобы получить список товаров, сумму, магазин и дату покупки.</p>
          </div>
        </header>

        <section class="card">
          <form class="upload-form" action="/upload" method="post" enctype="multipart/form-data">
            <label class="file-input">
              <input type="file" name="receipt" accept="image/*,.txt,.csv" required />
              <span>Выбрать файл чека</span>
            </label>
            <button class="primary" type="submit">Распознать чек</button>
          </form>
        </section>

        <section class="card">
          <h2>Последние чеки</h2>
          {items_html}
        </section>
      </main>
    </body>
    </html>
    """.format(items_html=items_html)


def render_receipt(receipt: dict) -> str:
    items_html = ""
    if receipt["items"]:
        rows = []
        for item in receipt["items"]:
            rows.append(
                """
                <div class="item-entry">
                  <form class="item-row" action="/receipt/{receipt_id}/items/{item_id}/update" method="post">
                    <input type="text" name="name" value="{name}" />
                    <input type="text" name="quantity" value="{quantity}" placeholder="Кол-во" />
                    <input type="text" name="price" value="{price}" placeholder="Цена" />
                    <input type="text" name="line_total" value="{line_total}" placeholder="Сумма" />
                    <button class="secondary" type="submit">Обновить</button>
                  </form>
                  <form class="item-delete" action="/receipt/{receipt_id}/items/{item_id}/delete" method="post">
                    <button class="ghost" type="submit">Удалить</button>
                  </form>
                </div>
                """.format(
                    receipt_id=receipt["id"],
                    item_id=item["id"],
                    name=item["name"],
                    quantity=item.get("quantity") or "",
                    price=item.get("price") or "",
                    line_total=item.get("line_total") or "",
                )
            )
        items_html = "<div class=\"items\">{}</div>".format("".join(rows))
    else:
        items_html = "<p class=\"empty\">Товары не распознаны, можно добавить их вручную в базе данных.</p>"

    return """
    <!DOCTYPE html>
    <html lang="ru">
    <head>
      <meta charset="UTF-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1" />
      <title>Чек {receipt_id}</title>
      <link rel="stylesheet" href="/static/styles.css" />
    </head>
    <body>
      <main class="container">
        <header class="header">
          <div>
            <h1>{store_name}</h1>
            <p>Детали распознанного чека и возможность корректировки данных.</p>
          </div>
          <div class="header-actions">
            <a class="secondary" href="/receipt/{receipt_id}/export">Экспорт CSV</a>
            <form action="/receipt/{receipt_id}/delete" method="post">
              <button class="danger" type="submit">Удалить чек</button>
            </form>
            <a class="secondary" href="/">Назад к списку</a>
          </div>
        </header>

        <section class="card">
          <h2>Данные чека</h2>
          <form class="receipt-form" action="/receipt/{receipt_id}/update" method="post">
            <label>
              Магазин
              <input type="text" name="store_name" value="{store_name}" />
            </label>
            <label>
              Дата покупки
              <input type="text" name="purchase_date" value="{purchase_date}" placeholder="ДД.ММ.ГГГГ" />
            </label>
            <label>
              Итоговая сумма
              <input type="text" name="total" value="{total}" placeholder="0.00" />
            </label>
            <button class="primary" type="submit">Сохранить</button>
          </form>
        </section>

        <section class="card">
          <h2>Товары</h2>
          {items_html}
          <form class="item-add" action="/receipt/{receipt_id}/items/add" method="post">
            <input type="text" name="name" placeholder="Название товара" />
            <input type="text" name="quantity" placeholder="Кол-во" />
            <input type="text" name="price" placeholder="Цена" />
            <input type="text" name="line_total" placeholder="Сумма" />
            <button class="primary" type="submit">Добавить товар</button>
          </form>
        </section>

        <section class="card">
          <h2>Сырой текст</h2>
          <pre class="raw-text">{raw_text}</pre>
        </section>
      </main>
    </body>
    </html>
    """.format(
        receipt_id=receipt["id"],
        store_name=receipt["store_name"],
        purchase_date=receipt.get("purchase_date") or "",
        total=receipt.get("total") or "",
        raw_text=receipt.get("raw_text") or "",
        items_html=items_html,
    )


def parse_post_form(handler: BaseHTTPRequestHandler) -> dict[str, str | bytes]:
    content_type = handler.headers.get("Content-Type", "")
    if content_type.startswith("multipart/form-data"):
        form = cgi.FieldStorage(
            fp=handler.rfile,
            headers=handler.headers,
            environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": content_type},
        )
        data: dict[str, str | bytes] = {}
        for key in form.keys():
            field = form[key]
            if isinstance(field, list):
                field = field[0]
            if field.filename:
                data[key] = field.file.read()
                data[f"{key}__filename"] = field.filename
            else:
                data[key] = field.value
        return data

    length = int(handler.headers.get("Content-Length", "0"))
    body = handler.rfile.read(length)
    form = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    return {key: values[0] for key, values in form.items()}


class ReceiptHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            body = render_index(fetch_receipts())
            self.respond(*html_response(body))
            return

        if path.startswith("/static/"):
            file_path = STATIC_DIR / path.replace("/static/", "")
            if file_path.exists() and file_path.is_file():
                content = file_path.read_bytes()
                self.respond(
                    HTTPStatus.OK,
                    {"Content-Type": "text/css; charset=utf-8"},
                    content,
                )
                return
            self.respond(*not_found())
            return

        if path.startswith("/receipt/"):
            parts = path.strip("/").split("/")
            if len(parts) >= 2 and parts[1].isdigit():
                receipt_id = int(parts[1])
                if len(parts) == 3 and parts[2] == "export":
                    receipt = fetch_receipt(receipt_id)
                    if not receipt:
                        self.respond(*redirect("/"))
                        return
                    buffer = StringIO()
                    writer = csv.writer(buffer)
                    writer.writerow([
                        "store_name",
                        "purchase_date",
                        "total",
                        "item_name",
                        "quantity",
                        "price",
                        "line_total",
                    ])
                    for item in receipt["items"]:
                        writer.writerow(
                            [
                                receipt["store_name"],
                                receipt.get("purchase_date") or "",
                                receipt.get("total") or "",
                                item.get("name") or "",
                                item.get("quantity") or "",
                                item.get("price") or "",
                                item.get("line_total") or "",
                            ]
                        )
                    content = buffer.getvalue().encode("utf-8")
                    filename = f"receipt_{receipt_id}.csv"
                    self.respond(
                        HTTPStatus.OK,
                        {
                            "Content-Type": "text/csv; charset=utf-8",
                            "Content-Disposition": f"attachment; filename={quote(filename)}",
                        },
                        content,
                    )
                    return

                receipt = fetch_receipt(receipt_id)
                if not receipt:
                    self.respond(*redirect("/"))
                    return
                body = render_receipt(receipt)
                self.respond(*html_response(body))
                return

        self.respond(*not_found())

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        form = parse_post_form(self)

        if path == "/upload":
            file_data = form.get("receipt")
            filename = form.get("receipt__filename")
            if isinstance(file_data, bytes) and isinstance(filename, str):
                safe_name = "_".join(filename.split())
                stored_name = f"{datetime.utcnow().timestamp()}_{safe_name}"
                file_path = UPLOAD_DIR / stored_name
                file_path.write_bytes(file_data)
                raw_text, notice = extract_text_from_upload(filename, file_data)
                if raw_text:
                    parsed_receipt = parse_receipt_text(raw_text)
                    if notice:
                        parsed_receipt.raw_text = f"{parsed_receipt.raw_text}\n\n{notice}"
                else:
                    parsed_receipt = ParsedReceipt(
                        "OCR недоступен",
                        None,
                        None,
                        notice or "Не удалось распознать текст из файла.",
                    )
                receipt_id = save_receipt(parsed_receipt)
                self.respond(*redirect(f"/receipt/{receipt_id}"))
                return
            self.respond(*redirect("/"))
            return

        if path.startswith("/receipt/"):
            parts = path.strip("/").split("/")
            if len(parts) >= 2 and parts[1].isdigit():
                receipt_id = int(parts[1])

                if len(parts) == 3 and parts[2] == "update":
                    store_name = (form.get("store_name") or "Неизвестный магазин").strip()
                    purchase_date = (form.get("purchase_date") or "").strip() or None
                    total_value = parse_optional_number(form.get("total") or None)
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
                    self.respond(*redirect(f"/receipt/{receipt_id}"))
                    return

                if len(parts) == 3 and parts[2] == "delete":
                    with sqlite3.connect(DB_PATH) as conn:
                        conn.execute("DELETE FROM items WHERE receipt_id = ?", (receipt_id,))
                        conn.execute("DELETE FROM receipts WHERE id = ?", (receipt_id,))
                        conn.commit()
                    self.respond(*redirect("/"))
                    return

                if len(parts) == 4 and parts[2] == "items" and parts[3] == "add":
                    name = (form.get("name") or "Новый товар").strip()
                    quantity = parse_optional_number(form.get("quantity") or None)
                    price = parse_optional_number(form.get("price") or None)
                    line_total = parse_optional_number(form.get("line_total") or None)
                    with sqlite3.connect(DB_PATH) as conn:
                        conn.execute(
                            """
                            INSERT INTO items (receipt_id, name, quantity, price, line_total)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (receipt_id, name, quantity, price, line_total),
                        )
                        conn.commit()
                    self.respond(*redirect(f"/receipt/{receipt_id}"))
                    return

                if len(parts) == 5 and parts[2] == "items" and parts[3].isdigit():
                    item_id = int(parts[3])
                    action = parts[4]

                    if action == "update":
                        name = (form.get("name") or "").strip()
                        quantity = parse_optional_number(form.get("quantity") or None)
                        price = parse_optional_number(form.get("price") or None)
                        line_total = parse_optional_number(form.get("line_total") or None)
                        with sqlite3.connect(DB_PATH) as conn:
                            conn.execute(
                                """
                                UPDATE items
                                SET name = ?, quantity = ?, price = ?, line_total = ?
                                WHERE id = ? AND receipt_id = ?
                                """,
                                (name, quantity, price, line_total, item_id, receipt_id),
                            )
                            conn.commit()
                        self.respond(*redirect(f"/receipt/{receipt_id}"))
                        return

                    if action == "delete":
                        with sqlite3.connect(DB_PATH) as conn:
                            conn.execute(
                                "DELETE FROM items WHERE id = ? AND receipt_id = ?",
                                (item_id, receipt_id),
                            )
                            conn.commit()
                        self.respond(*redirect(f"/receipt/{receipt_id}"))
                        return

        self.respond(*not_found())

    def log_message(self, format: str, *args: object) -> None:
        return

    def respond(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)


def run_server() -> None:
    init_db()
    port = int(os.environ.get("PORT", "5000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), ReceiptHandler)
    print(f"Server running on http://0.0.0.0:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run_server()
