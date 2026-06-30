import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv()  # загружаем .env до любых импортов, которые читают os.getenv

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    WebAppInfo,
    ReplyKeyboardRemove,
)

from prices import find_price, get_stats, load_all_sheets
from access import is_allowed, is_admin, load_allowed_users, list_users_text, add_user, remove_user

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError(
        "Не задан BOT_TOKEN. "
        "Локально: добавь в .env | Railway: Variables → BOT_TOKEN"
    )

WEBAPP_URL = os.getenv("WEBAPP_URL", "")

REFRESH_INTERVAL_MIN = int(os.getenv("REFRESH_INTERVAL_MIN", "30"))

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()


def main_keyboard() -> ReplyKeyboardMarkup | ReplyKeyboardRemove:
    """Клавиатура с кнопкой сканирования (если задан WEBAPP_URL)."""
    if not WEBAPP_URL:
        return ReplyKeyboardRemove()
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📷 Сканировать штрихкод", web_app=WebAppInfo(url=WEBAPP_URL))]
        ],
        resize_keyboard=True,
    )


# ── Фоновое обновление ─────────────────────────────────────────────────────────

async def auto_refresh_loop() -> None:
    while True:
        await asyncio.sleep(REFRESH_INTERVAL_MIN * 60)
        logger.info("Автообновление данных из Google Sheets...")
        load_allowed_users()
        count = load_all_sheets()
        logger.info("Автообновление: %d позиций", count)


# ── Команды ────────────────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: types.Message) -> None:
    if not is_allowed(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return

    scan_hint = (
        "\n\nНажми кнопку <b>📷 Сканировать штрихкод</b> внизу "
        "или просто введи штрихкод цифрами."
        if WEBAPP_URL else
        "\n\nПросто отправь штрихкод цифрами."
    )

    await message.answer(
        "👋 Привет! Я помогу узнать цену товара по штрихкоду." + scan_hint + "\n\n"
        "📋 <b>Команды:</b>\n"
        "/stats — статистика по отделам\n"
        "/reload — обновить данные из таблицы\n"
        "/help — помощь"
        + (
            "\n\n🔑 <b>Админ:</b>\n"
            "/users — список доступа\n"
            "/adduser ID Имя — добавить\n"
            "/removeuser ID — удалить"
            if is_admin(message.from_user.id) else ""
        ),
        reply_markup=main_keyboard(),
    )


@dp.message(Command("help"))
async def cmd_help(message: types.Message) -> None:
    if not is_allowed(message.from_user.id):
        return
    await message.answer(
        "🔍 <b>Как пользоваться:</b>\n\n"
        "• Нажми 📷 Сканировать — наведи камеру на штрихкод\n"
        "• Или введи штрихкод вручную цифрами\n\n"
        f"Данные обновляются каждые {REFRESH_INTERVAL_MIN} минут.",
        reply_markup=main_keyboard(),
    )


@dp.message(Command("stats"))
async def cmd_stats(message: types.Message) -> None:
    if not is_allowed(message.from_user.id):
        return
    await message.answer(get_stats())


@dp.message(Command("reload"))
async def cmd_reload(message: types.Message) -> None:
    if not is_allowed(message.from_user.id):
        return
    await message.answer("🔄 Обновляю данные из Google Таблицы...")
    load_allowed_users()
    count = load_all_sheets()
    if count > 0:
        await message.answer(f"✅ Загружено <b>{count}</b> позиций.\n\n{get_stats()}")
    else:
        await message.answer(
            "⚠️ Не удалось загрузить данные.\n"
            "Проверь SPREADSHEET_ID и доступ к таблице."
        )


# ── Админ: управление доступом ─────────────────────────────────────────────────

@dp.message(Command("users"))
async def cmd_users(message: types.Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Только для администратора.")
        return
    await message.answer(list_users_text())


@dp.message(Command("adduser"))
async def cmd_adduser(message: types.Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Только для администратора.")
        return

    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer(
            "Формат: <code>/adduser 123456789 Имя</code>\n"
            "ID узнать у @userinfobot"
        )
        return

    user_id = int(parts[1])
    name = parts[2].strip() if len(parts) > 2 else ""

    try:
        result = add_user(user_id, name)
        await message.answer(result)
    except Exception as e:
        logger.exception("adduser failed")
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(Command("removeuser"))
async def cmd_removeuser(message: types.Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Только для администратора.")
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer("Формат: <code>/removeuser 123456789</code>")
        return

    user_id = int(parts[1].strip())

    try:
        result = remove_user(user_id)
        await message.answer(result)
    except Exception as e:
        logger.exception("removeuser failed")
        await message.answer(f"❌ Ошибка: {e}")


# ── Данные из Mini App (сканер) ────────────────────────────────────────────────

@dp.message(F.web_app_data)
async def handle_webapp_data(message: types.Message) -> None:
    if not is_allowed(message.from_user.id):
        return

    barcode = (message.web_app_data.data or "").strip()
    logger.info("WebApp штрихкод=%s user_id=%s", barcode, message.from_user.id)

    if not barcode.isdigit() or not (4 <= len(barcode) <= 20):
        await message.answer(f"⚠️ Получен некорректный штрихкод: <code>{barcode}</code>")
        return

    await message.answer(find_price(barcode))


# ── Ввод штрихкода вручную ─────────────────────────────────────────────────────

@dp.message(F.text)
async def handle_barcode(message: types.Message) -> None:
    if not is_allowed(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return

    barcode = message.text.strip()

    if not barcode.isdigit():
        await message.answer(
            "⚠️ Штрихкод должен содержать только цифры.\n"
            "Попробуй ещё раз или введи /help"
        )
        return

    if not (4 <= len(barcode) <= 20):
        await message.answer(
            f"⚠️ Неверная длина штрихкода: <code>{barcode}</code> ({len(barcode)} симв.)"
        )
        return

    logger.info("Текст штрихкод=%s user_id=%s", barcode, message.from_user.id)
    await message.answer(find_price(barcode))


# ── Запуск ────────────────────────────────────────────────────────────────────

async def main() -> None:
    logger.info("Загружаю пользователей и данные из Google Sheets...")
    users_count = load_allowed_users()
    logger.info("Пользователей с доступом: %d", users_count)
    count = load_all_sheets()
    logger.info("Загружено %d позиций.", count)

    asyncio.create_task(auto_refresh_loop())

    logger.info("Запускаю бота... WEBAPP_URL=%s", WEBAPP_URL or "(не задан)")
    await dp.start_polling(bot, allowed_updates=["message"])


if __name__ == "__main__":
    asyncio.run(main())
