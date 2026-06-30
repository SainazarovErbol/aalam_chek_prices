import os
import json
import logging
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

# Кэш: { штрихкод: { name, price, unit, department } }
_cache: dict = {}
_last_loaded: datetime | None = None


def _load_credentials(scopes: list) -> Credentials:
    """
    Загружает credentials из переменных окружения или файла.
    Railway: GOOGLE_CREDENTIALS_JSON
    Локально: credentials.json или GOOGLE_CREDENTIALS_FILE
    """
    json_str = os.getenv("GOOGLE_CREDENTIALS_JSON", "").strip()
    if json_str:
        info = json.loads(json_str)
        return Credentials.from_service_account_info(info, scopes=scopes)

    creds_path = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json").strip()

    # Частая ошибка: JSON вставили в GOOGLE_CREDENTIALS_FILE вместо GOOGLE_CREDENTIALS_JSON
    if creds_path.startswith("{"):
        info = json.loads(creds_path)
        return Credentials.from_service_account_info(info, scopes=scopes)

    if not os.path.exists(creds_path):
        raise FileNotFoundError(
            f"Файл {creds_path!r} не найден. "
            "На Railway задай переменную GOOGLE_CREDENTIALS_JSON (не GOOGLE_CREDENTIALS_FILE)."
        )

    return Credentials.from_service_account_file(creds_path, scopes=scopes)


def get_sheets_client() -> gspread.Client:
    """Клиент Google Sheets (используется также в access.py)."""
    creds = _load_credentials(SCOPES)
    return gspread.authorize(creds)


def _get_client() -> gspread.Client:
    return get_sheets_client()


def _parse_price(raw: str) -> float | None:
    try:
        return float(str(raw).strip().replace(" ", "").replace(",", "."))
    except (ValueError, TypeError):
        return None


def load_all_sheets() -> int:
    """
    Загружает все листы таблицы в кэш.
    Каждый лист = отдел. Ищем колонки по имени (регистр не важен).
    Возвращает количество загруженных позиций.
    """
    global _cache, _last_loaded

    spreadsheet_id = os.getenv("SPREADSHEET_ID", "")
    if not spreadsheet_id:
        logger.error("Не задан SPREADSHEET_ID в .env")
        return 0

    try:
        client = _get_client()
        spreadsheet = client.open_by_key(spreadsheet_id)
    except Exception as e:
        logger.error("Не удалось подключиться к Google Sheets: %s", e)
        return 0

    new_cache: dict = {}

    for worksheet in spreadsheet.worksheets():
        department = worksheet.title

        # Лист «Доступ» — только пользователи, не товары
        if department.strip().lower() in ("доступ", "access", "users"):
            continue

        try:
            records = worksheet.get_all_records(numericise_ignore=["all"])
        except Exception as e:
            logger.warning("Ошибка при чтении листа '%s': %s", department, e)
            continue

        for record in records:
            # Нормализуем ключи
            norm = {str(k).lower().strip(): str(v).strip() for k, v in record.items()}

            barcode = (
                norm.get("штрихкод")
                or norm.get("barcode")
                or norm.get("код")
                or norm.get("ean")
                or ""
            ).strip()

            name = (
                norm.get("наименование")
                or norm.get("номенклатура")
                or norm.get("товар")
                or norm.get("name")
                or ""
            ).strip()

            price_raw = (
                norm.get("цена")
                or norm.get("розничная цена")
                or norm.get("price")
                or ""
            ).strip()

            unit = (
                norm.get("единица")
                or norm.get("ед.")
                or norm.get("unit")
                or ""
            ).strip()

            if not barcode or not name:
                continue

            new_cache[barcode] = {
                "name": name,
                "price": _parse_price(price_raw),
                "unit": unit,
                "department": department,
            }

        logger.info("Лист '%s': загружено %d позиций", department, len(
            [v for v in new_cache.values() if v["department"] == department]
        ))

    _cache = new_cache
    _last_loaded = datetime.now()
    logger.info("Итого загружено: %d позиций", len(_cache))
    return len(_cache)


def find_price(barcode: str) -> str:
    """Ищет товар по штрихкоду, возвращает строку для Telegram."""
    barcode = barcode.strip()

    if not _cache:
        return "⚠️ База данных пуста. Выполни /reload или проверь настройки."

    item = _cache.get(barcode)
    if item is None:
        return f"❌ Товар со штрихкодом <code>{barcode}</code> не найден"

    name = item["name"]
    price = item["price"]
    unit = item["unit"]
    department = item["department"]

    if price is not None:
        price_str = f"{price:,.2f}".replace(",", " ").replace(".", ",")
        price_line = f"💰 Цена: <b>{price_str} сом</b>"
        if unit:
            price_line += f" / {unit}"
    else:
        price_line = "💰 Цена: <i>не указана</i>"

    lines = [
        f"📦 <b>{name}</b>",
        f"🏪 Отдел: {department}",
        f"🔢 Штрихкод: <code>{barcode}</code>",
        price_line,
    ]

    if _last_loaded:
        lines.append(f"\n🕐 Данные от: {_last_loaded.strftime('%d.%m.%Y %H:%M')}")

    return "\n".join(lines)


def get_stats() -> str:
    """Статистика по загруженным данным с разбивкой по отделам."""
    if not _cache:
        return "📊 База данных не загружена. Выполни /reload"

    # Считаем по отделам
    by_dept: dict[str, int] = {}
    for item in _cache.values():
        dept = item["department"]
        by_dept[dept] = by_dept.get(dept, 0) + 1

    lines = [f"📊 Всего товаров: <b>{len(_cache)}</b>", ""]
    for dept, count in sorted(by_dept.items()):
        lines.append(f"  • {dept}: {count} поз.")

    if _last_loaded:
        lines.append(f"\n🕐 Обновлено: {_last_loaded.strftime('%d.%m.%Y в %H:%M')}")

    return "\n".join(lines)
