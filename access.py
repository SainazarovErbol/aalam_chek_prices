import os
import logging

import gspread

from prices import get_sheets_client

logger = logging.getLogger(__name__)

ACCESS_TAB = os.getenv("ACCESS_SHEET_NAME", "Доступ")
ACCESS_TAB_ALIASES = {"доступ", "access", "users"}

_allowed_users: set[int] = set()
_user_names: dict[int, str] = {}


def is_access_tab(title: str) -> bool:
    return title.strip().lower() in ACCESS_TAB_ALIASES


def _parse_admin_ids() -> set[int]:
    raw = os.getenv("ADMIN_USER_IDS", "")
    if not raw.strip():
        return set()
    return {int(x.strip()) for x in raw.split(",") if x.strip()}


def _env_allowed_ids() -> set[int]:
    raw = os.getenv("ALLOWED_USER_IDS", "")
    if not raw.strip():
        return set()
    return {int(x.strip()) for x in raw.split(",") if x.strip()}


def is_admin(user_id: int) -> bool:
    admins = _parse_admin_ids()
    return user_id in admins if admins else False


def is_allowed(user_id: int) -> bool:
    if not _allowed_users:
        env_ids = _env_allowed_ids()
        return user_id in env_ids if env_ids else True
    return user_id in _allowed_users


def _get_spreadsheet():
    spreadsheet_id = os.getenv("SPREADSHEET_ID", "")
    if not spreadsheet_id:
        raise RuntimeError("Не задан SPREADSHEET_ID")
    client = get_sheets_client()
    return client.open_by_key(spreadsheet_id)


def _get_access_worksheet(create: bool = False):
    spreadsheet = _get_spreadsheet()
    try:
        return spreadsheet.worksheet(ACCESS_TAB)
    except gspread.WorksheetNotFound:
        if not create:
            return None
        ws = spreadsheet.add_worksheet(title=ACCESS_TAB, rows=200, cols=2)
        ws.update("A1:B1", [["Telegram ID", "Имя"]])
        logger.info("Создан лист '%s'", ACCESS_TAB)
        return ws


def load_allowed_users() -> int:
    """Загружает список пользователей с листа «Доступ»."""
    global _allowed_users, _user_names

    try:
        ws = _get_access_worksheet(create=False)
    except Exception as e:
        logger.error("Ошибка доступа к таблице пользователей: %s", e)
        _allowed_users = _env_allowed_ids()
        _user_names = {}
        return len(_allowed_users)

    if ws is None:
        _allowed_users = _env_allowed_ids()
        _user_names = {}
        logger.info("Лист '%s' не найден, используем ALLOWED_USER_IDS из env", ACCESS_TAB)
        return len(_allowed_users)

    records = ws.get_all_records(numericise_ignore=["all"])
    users: set[int] = set()
    names: dict[int, str] = {}

    for row in records:
        norm = {str(k).lower().strip(): str(v).strip() for k, v in row.items() if k}
        uid_raw = (
            norm.get("telegram id")
            or norm.get("telegram_id")
            or norm.get("id")
            or norm.get("userid")
            or norm.get("user_id")
            or ""
        ).strip()
        if not uid_raw.isdigit():
            continue
        uid = int(uid_raw)
        users.add(uid)
        name = norm.get("имя") or norm.get("name") or norm.get("фio") or norm.get("фио") or ""
        if name:
            names[uid] = name

    _allowed_users = users
    _user_names = names
    logger.info("Загружено %d пользователей с листа '%s'", len(users), ACCESS_TAB)
    return len(users)


def list_users_text() -> str:
    if not _allowed_users:
        env_ids = _env_allowed_ids()
        if env_ids:
            lines = ["👥 <b>Пользователи (из env):</b>", ""]
            for uid in sorted(env_ids):
                lines.append(f"  • <code>{uid}</code>")
            return "\n".join(lines)
        return "👥 Список пуст — доступ открыт для всех."

    lines = [f"👥 <b>Доступ разрешён ({len(_allowed_users)}):</b>", ""]
    for uid in sorted(_allowed_users):
        name = _user_names.get(uid, "")
        if name:
            lines.append(f"  • <code>{uid}</code> — {name}")
        else:
            lines.append(f"  • <code>{uid}</code>")
    return "\n".join(lines)


def add_user(user_id: int, name: str = "") -> str:
    if user_id in _allowed_users:
        return f"ℹ️ Пользователь <code>{user_id}</code> уже в списке."

    ws = _get_access_worksheet(create=True)
    ws.append_row([str(user_id), name], value_input_option="USER_ENTERED")
    _allowed_users.add(user_id)
    if name:
        _user_names[user_id] = name
    return f"✅ Добавлен: <code>{user_id}</code>" + (f" ({name})" if name else "")


def remove_user(user_id: int) -> str:
    if user_id not in _allowed_users:
        return f"❌ Пользователь <code>{user_id}</code> не найден в списке."

    ws = _get_access_worksheet(create=False)
    if ws is None:
        return "❌ Лист «Доступ» не найден."

    cell = ws.find(str(user_id), in_column=1)
    if cell is None:
        _allowed_users.discard(user_id)
        _user_names.pop(user_id, None)
        return f"✅ Удалён из кэша: <code>{user_id}</code>"

    ws.delete_rows(cell.row)
    _allowed_users.discard(user_id)
    _user_names.pop(user_id, None)
    return f"✅ Удалён: <code>{user_id}</code>"
