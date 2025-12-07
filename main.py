import asyncio
import base64
from io import BytesIO
import json
import os
import random
from datetime import datetime, timedelta, timezone
import re
import math
import logging
import threading  # Для thread-safety

import requests
from flask import Flask, request, jsonify
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.error import TelegramError

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Persistent event loop for webhook processing
_loop = None

def get_event_loop():
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop

# ============================
#   НАСТРОЙКИ (через .env)
# ============================

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
if not TG_BOT_TOKEN:
    raise ValueError("Установите TG_BOT_TOKEN в .env!")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_CHAT_ID = 203473623  # ИЗ ответа пользователя

WELCOME_PHOTO_URL = "https://ecosteni.ru/wp-content/uploads/2025/11/qncccaze.jpg"
PRESENTATION_URL = "https://ecosteni.ru/wp-content/uploads/2025/11/ecosteny_prezentacziya.pdf"
TG_GROUP = "@ecosteni"

PHONE_NUMBER = "+79780223222"  # Fixed phone number without spaces

GREETING_PHRASES = [
    "Привет, {name}! Я ассистент компании ECO Стены. Помогу с подбором материалов и расчётом панелей. 😊",
    "Рад знакомству, {name}! Я здесь, чтобы помочь вам с продукцией ECO Стены и ответить на вопросы.",
    "Здравствуйте, {name}! Если планируете ремонт или обновление интерьера — давайте подберём материалы вместе.",
    "{name}, привет! Я подскажу по WPC панелям, профилям, каталогу и примерному расчёту под ваши размеры.",
    "Добро пожаловать, {name}! Рассказывайте, какой у вас объект — подберём оптимальное решение из наших материалов.",
]

# Файл для хранения статистики (на Render - ephemeral, но для простоты)
STATS_FILE = "/tmp/eco_stats.json"
USER_DATA_FILE = "/tmp/user_data.json"  # New file for user preferences and states

def load_stats():
    default_stats = {
        "users": set(),
        "calc_count": 0,
        "today": datetime.now(timezone.utc).date().isoformat(),
        "users_today": set(),
        "calc_today": 0
    }
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, 'r') as f:
                loaded = json.load(f)
                # Convert lists back to sets
                loaded['users'] = set(loaded.get('users', []))
                loaded['users_today'] = set(loaded.get('users_today', []))
                return loaded
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"Corrupted stats file, starting fresh: {e}")
            # Optionally remove corrupted file with try-except
            try:
                os.remove(STATS_FILE)
            except OSError as oe:
                logger.warning(f"Could not remove stats file: {oe}")
    return default_stats

def save_stats(stats):
    # Convert sets to lists for JSON
    serializable = {
        "users": list(stats['users']),
        "calc_count": stats['calc_count'],
        "today": stats['today'],
        "users_today": list(stats['users_today']),
        "calc_today": stats['calc_today']
    }
    try:
        with open(STATS_FILE, 'w') as f:
            json.dump(serializable, f)
    except Exception as e:
        logger.error(f"Failed to save stats: {e}")

def load_user_data():
    if os.path.exists(USER_DATA_FILE):
        try:
            with open(USER_DATA_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"Corrupted user data file, starting fresh: {e}")
            try:
                os.remove(USER_DATA_FILE)
            except OSError as oe:
                logger.warning(f"Could not remove user data file: {oe}")
    return {}

def save_user_data(user_data):
    try:
        with open(USER_DATA_FILE, 'w') as f:
            json.dump(user_data, f)
    except Exception as e:
        logger.error(f"Failed to save user data: {e}")

# Function to get/set user unit preference
def get_user_unit(user_id):
    user_data = load_user_data()
    return user_data.get(str(user_id), {}).get('unit', None)

def set_user_unit(user_id, unit):
    user_data = load_user_data()
    if str(user_id) not in user_data:
        user_data[str(user_id)] = {}
    user_data[str(user_id)]['unit'] = unit
    save_user_data(user_data)

# Function to get/set user state (for input flows)
def get_user_state(user_id):
    user_data = load_user_data()
    return user_data.get(str(user_id), {}).get('state', None)

def get_user_state_data(user_id):
    user_data = load_user_data()
    return user_data.get(str(user_id), {}).get('state_data', {})

def set_user_state(user_id, state, data=None):
    user_data = load_user_data()
    if str(user_id) not in user_data:
        user_data[str(user_id)] = {}
    user_data[str(user_id)]['state'] = state
    if data:
        user_data[str(user_id)]['state_data'] = data
    save_user_data(user_data)

def clear_user_state(user_id):
    user_data = load_user_data()
    if str(user_id) in user_data:
        if 'state' in user_data[str(user_id)]:
            del user_data[str(user_id)]['state']
        if 'state_data' in user_data[str(user_id)]:
            del user_data[str(user_id)]['state_data']
        if not user_data[str(user_id)]:  # If empty, remove user
            del user_data[str(user_id)]
    save_user_data(user_data)

# ============================
#   КАТАЛОГ МАТЕРИАЛОВ
# ============================

WALL_PRODUCTS = {
    "WPC Бамбук угольный": {
        5: {
            "width_mm": 1220,
            "panels": {
                2440: {"area_m2": 2.928, "price_rub": 10500},
                2600: {"area_m2": 3.12, "price_rub": 11100},
                2800: {"area_m2": 3.36, "price_rub": 12000},
                3000: {"area_m2": 3.6, "price_rub": 12900},
                3200: {"area_m2": 3.84, "price_rub": 13700},
            },
        },
        8: {
            "width_mm": 1220,
            "panels": {
                2440: {"area_m2": 2.928, "price_rub": 12200},
                2600: {"area_m2": 3.12, "price_rub": 13000},
                2800: {"area_m2": 3.36, "price_rub": 14000},
                3000: {"area_m2": 3.6, "price_rub": 15000},
                3200: {"area_m2": 3.84, "price_rub": 16000},
            },
        },
    },
    "WPC Бамбук": {
        5: {
            "width_mm": 1220,
            "panels": {
                2440: {"area_m2": 2.928, "price_rub": 12200},
                2600: {"area_m2": 3.12, "price_rub": 13000},
                2800: {"area_m2": 3.36, "price_rub": 14000},
                3000: {"area_m2": 3.6, "price_rub": 15000},
                3200: {"area_m2": 3.84, "price_rub": 16000},
            },
        },
        8: {
            "width_mm": 1220,
            "panels": {
                2440: {"area_m2": 2.928, "price_rub": 13900},
                2600: {"area_m2": 3.12, "price_rub": 14900},
                2800: {"area_m2": 3.36, "price_rub": 16000},
                3000: {"area_m2": 3.6, "price_rub": 17100},
                3200: {"area_m2": 3.84, "price_rub": 18300},
            },
        },
    },
    "WPC повышенной плотности": {
        8: {
            "width_mm": 1220,
            "panels": {
                2440: {"area_m2": 2.928, "price_rub": 15500},
                2600: {"area_m2": 3.12, "price_rub": 16500},
                2800: {"area_m2": 3.36, "price_rub": 17800},
                3000: {"area_m2": 3.6, "price_rub": 19100},
                3200: {"area_m2": 3.84, "price_rub": 20300},
            },
        },
    },
    "WPC Бамбук с защитным слоем": {
        8: {
            "width_mm": 1220,
            "panels": {
                2440: {"area_m2": 2.928, "price_rub": 16400},
                2600: {"area_m2": 3.12, "price_rub": 17500},
                2800: {"area_m2": 3.36, "price_rub": 18800},
                3000: {"area_m2": 3.6, "price_rub": 20100},
                3200: {"area_m2": 3.84, "price_rub": 21500},
            },
        },
    },
    "WPC повышенной плотности с защитным слоем": {
        8: {
            "width_mm": 1220,
            "panels": {
                2440: {"area_m2": 2.928, "price_rub": 18000},
                2600: {"area_m2": 3.12, "price_rub": 19100},
                2800: {"area_m2": 3.36, "price_rub": 20600},
                3000: {"area_m2": 3.6, "price_rub": 22100},
                3200: {"area_m2": 3.84, "price_rub": 23500},
            },
        },
    },
    "SPC Панель": {  # Без толщины
        0: {  # Dummy thickness
            "width_mm": 1220,
            "panels": {
                2440: {"area_m2": 2.928, "price_rub": 9500},
                2600: {"area_m2": 3.12, "price_rub": 10100},
            },
        },
    },
}

PRODUCT_CODES = {
    "wpc_charcoal": "WPC Бамбук угольный",
    "wpc_bamboo": "WPC Бамбук",
    "wpc_hd": "WPC повышенной плотности",
    "wpc_bamboo_coat": "WPC Бамбук с защитным слоем",
    "wpc_hd_coat": "WPC повышенной плотности с защитным слоем",
    "spc_panel": "SPC Панель",
}

PROFILES = {
    5: {
        "Стыковочный": 1350,
        "Стыковочный широкий": 1500,
        "Стыковочный с подсветкой": 1700,
        "Финишный": 1350,
        "Внешний угол": 1450,
        "Внутренний угол": 1450,
    },
    8: {
        "Стыковочный": 1450,
        "Стыковочный широкий": 1600,
        "Стыковочный с подсветкой": 1800,
        "Финишный": 1450,
        "Внешний угол": 1550,
        "Внутренний угол": 1550,
    },
}

SLAT_PRICES = {
    "wpc": 1200,  # руб./м.п.
    "wood": 1500,
}

PANELS_3D = {
    "var1": {"code": "3d_600x1200", "width_mm": 600, "height_mm": 1200, "area_m2": 0.72, "price_rub": 3000},
    "var2": {"code": "3d_1200x3000", "width_mm": 1200, "height_mm": 3000, "area_m2": 3.6, "price_rub": 8000},
}

SYSTEM_PROMPT = """
Ты — онлайн-консультант компании ECO Стены.

У тебя есть каталог стеновых WPC панелей с размерами, площадью покрытия и ценой за 1 панель.
Каталог передаётся тебе в виде JSON в сообщении. Используй ТОЛЬКО его для расчётов по стеновым панелям.

ВАЖНО:
— Никогда не проси у пользователя каталог, JSON, прайс или цены.
— Если JSON каталога отсутствует, честно скажи, что точный расчёт доступен только при наличии каталога (который подгружается система),
  и предложи связаться с менеджером.
— Если клиент выбрал через кнопки конкретную панель, толщину и высоту — ОБЯЗАН использовать именно эту комбинацию.

ОГРАНИЧЕНИЯ:
— WPC повышенной плотности не бывает толщиной 5 мм.
— WPC Бамбук угольный не бывает с защитным слоем.

Если клиент выбрал несколько материалов, в запросе может быть список этих материалов — используй его и в расчёте, и в формулировке….

Также:
— Если ранее уже была проанализирована планировка с размерами, обязательно используй эти данные.
— Отвечай по-русски, кратко, дружелюбно и по делу.
"""

CHAT_SYSTEM_PROMPT = """
Ты — живой, дружелюбный ассистент компании ECO Стены.
Помогаешь с выбором и расчётом:
— стеновых WPC панелей,
— реечных панелей (WPC и деревянные),
— 3D панелей.
— профилей.
— SPC панелей.
"""

# ============================
#   FLASK + TELEGRAM
# ============================

app = Flask(__name__)

tg_application = Application.builder().token(TG_BOT_TOKEN).build()

# ============================
#   КЛАВИАТУРА
# ============================

def build_main_menu_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("🧮 Рассчитать материалы", callback_data="main|calc")],
        [InlineKeyboardButton("ℹ️ Информация", callback_data="main|info")],
        [InlineKeyboardButton("📚 Получить каталоги", callback_data="main|catalogs")],
        [InlineKeyboardButton("📊 Получить презентацию", callback_data="main|presentation")],
        [InlineKeyboardButton("📞 Контактная информация", callback_data="main|contacts")],
        [InlineKeyboardButton("🤝 Партнёрка", callback_data="main|partner")],
    ]
    return InlineKeyboardMarkup(buttons)

def build_calc_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("🧱 Стеновые панели", callback_data="calc|wall")],
        [InlineKeyboardButton("🔩 Реечные панели", callback_data="calc|slat")],
        [InlineKeyboardButton("🎨 3D панели", callback_data="calc|3d")],
        [InlineKeyboardButton("⚙️ Профили", callback_data="calc|profile")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="main|menu")],
    ]
    return InlineKeyboardMarkup(buttons)

def build_unit_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("📏 Метры (м)", callback_data="unit|m")],
        [InlineKeyboardButton("📐 Миллиметры (мм)", callback_data="unit|mm")],
    ]
    return InlineKeyboardMarkup(buttons)

# ============================
#   HANDLERS
# ============================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    stats = load_stats()
    stats['users'].add(user.id)
    stats['users_today'].add(user.id)
    save_stats(stats)

    # Send welcome photo
    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=WELCOME_PHOTO_URL,
        caption=random.choice(GREETING_PHRASES).format(name=user.first_name or "друг"),
        reply_markup=build_main_menu_keyboard(),
    )

async def handle_partner_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    name = update.message.text.strip()
    # Save name or something (e.g., send to admin)
    try:
        await context.bot.send_message(ADMIN_CHAT_ID, f"Новый партнёр: {name} (ID: {user_id})")
    except TelegramError:
        logger.error("Failed to notify admin about partner")
    await update.message.reply_text(
        f"Спасибо, {name}! Теперь вы можете использовать меню для расчёта материалов или другие функции.",
        reply_markup=build_main_menu_keyboard()
    )
    clear_user_state(user_id)

async def contacts_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    contact_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📞 Позвонить", url=f"tel:{PHONE_NUMBER}")],
        [InlineKeyboardButton("💬 Написать в Telegram", url=f"https://t.me/{TG_GROUP.replace('@', '')}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="main|menu")],
    ])

    await query.edit_message_text(
        "📞 Контактная информация:\n"
        "Телефон: +7 (978) 022-32-22\n"
        "Telegram: @ecosteni\n"
        "Email: info@ecosteni.ru",
        reply_markup=contact_keyboard
    )

async def calc_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    unit = get_user_unit(user_id)
    if not unit:
        await query.edit_message_text(
            "Перед расчётом выберите единицу измерения:",
            reply_markup=build_unit_keyboard()
        )
        return
    await query.edit_message_text("Выберите тип расчёта:", reply_markup=build_calc_keyboard())

async def unit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    unit = query.data.split("|")[1]
    user_id = query.from_user.id
    set_user_unit(user_id, unit)
    await query.edit_message_text(
        f"Единица измерения установлена: {unit}. Теперь выберите тип расчёта:",
        reply_markup=build_calc_keyboard()
    )

async def partner_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "Для партнёрства введите ваше имя:",
    )
    set_user_state(query.from_user.id, "partner_name")

# Placeholder for other callbacks (info, catalogs, presentation) - expand as needed
async def info_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "ℹ️ ECO Стены — премиум WPC панели для интерьера. Экологично, влагостойко, просто в монтаже.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="main|menu")]])
    )

async def catalogs_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📚 Каталоги: [Ссылка на PDF или фото]. Свяжитесь для получения!",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="main|menu")]])
    )

async def presentation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📊 Презентация: [Ссылка]",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Скачать PDF", url=PRESENTATION_URL)],
            [InlineKeyboardButton("⬅️ Назад", callback_data="main|menu")]
        ])
    )

# Example for wall calc start - integrate unit and state
async def wall_calc_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    unit = get_user_unit(user_id)
    if not unit:
        await query.edit_message_text("Сначала выберите единицу измерения:", reply_markup=build_unit_keyboard())
        return
    # Start dimension input, e.g., total area or rooms
    await query.edit_message_text(f"Введите общую площадь стен (в {unit}):")
    set_user_state(user_id, "wall_area")

# Message handler for inputs (dimensions, partner name, etc.)
async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    text = update.message.text.strip()
    state = get_user_state(user_id)
    state_data = get_user_state_data(user_id)

    if state == "partner_name":
        await handle_partner_name(update, context)
        return

    if not state:
        # Fallback: echo or forward to admin
        await update.message.reply_text("Используйте меню для навигации!")
        return

    unit = get_user_unit(user_id) or "мм"
    try:
        value = float(text)
        if state == "wall_area":
            # Process area, then ask for windows/doors
            set_user_state(user_id, "windows_count", {"area": value})
            await update.message.reply_text(f"Площадь: {value} {unit}. Сколько окон?")
        elif state == "windows_count":
            count = int(value)
            data = state_data.copy()
            data["windows"] = count
            set_user_state(user_id, "window_width", data)
            await update.message.reply_text(f"Окон: {count}. Ширина первого окна (в {unit}):")
        elif state.endswith("_width"):
            item_type = state.replace("_width", "")
            data = state_data.copy()
            data["width"] = value
            set_user_state(user_id, f"{item_type}_height", data)
            item_ru = "окна" if "window" in item_type else "двери"
            await update.message.reply_text(f"Ширина: {value} {unit}. Высота {item_ru} (в {unit}):")
        elif state.endswith("_height"):
            item_type = state.replace("_height", "")
            data = state_data.copy()
            data["height"] = value
            # Process full item, e.g., subtract area: area_m2 = (width * height / 1000000 if mm else width*height)
            conv = 1000000 if unit == "мм" else 1
            subtract = (data["width"] * value) / conv
            # Assume total area in data["area"]
            net_area = data.get("area", 0) - subtract
            clear_user_state(user_id)
            await update.message.reply_text(f"Размеры обработаны. Чистая площадь: {net_area:.2f} м². Теперь расчёт панелей...")
            # Here integrate catalog calc using WALL_PRODUCTS, increment stats['calc_count'] etc.
            stats = load_stats()
            stats['calc_count'] += 1
            stats['calc_today'] += 1
            save_stats(stats)
            # Send calc result (placeholder)
            await update.message.reply_text("Расчёт: Нужно 10 панелей по 10500 руб. Итого: 105000 руб.", reply_markup=build_main_menu_keyboard())
        # Add more states as needed
    except ValueError:
        await update.message.reply_text(f"Пожалуйста, введите числовое значение (в {unit}).")

# Callback for main menu return
async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Главное меню:", reply_markup=build_main_menu_keyboard())

# Add all handlers
tg_application.add_handler(CommandHandler("start", start))
tg_application.add_handler(CallbackQueryHandler(main_menu_callback, pattern="main\|menu"))
tg_application.add_handler(CallbackQueryHandler(contacts_callback, pattern="main\|contacts"))
tg_application.add_handler(CallbackQueryHandler(calc_callback, pattern="main\|calc"))
tg_application.add_handler(CallbackQueryHandler(unit_callback, pattern="unit\|.*"))
tg_application.add_handler(CallbackQueryHandler(partner_callback, pattern="main\|partner"))
tg_application.add_handler(CallbackQueryHandler(info_callback, pattern="main\|info"))
tg_application.add_handler(CallbackQueryHandler(catalogs_callback, pattern="main\|catalogs"))
tg_application.add_handler(CallbackQueryHandler(presentation_callback, pattern="main\|presentation"))
tg_application.add_handler(CallbackQueryHandler(wall_calc_callback, pattern="calc\|wall"))  # Example for wall
# Add more for slat, 3d, profile similarly
tg_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input))

# ============================
#   WEBHOOK
# ============================

@app.route('/webhook', methods=['POST'])
def webhook():
    loop = get_event_loop()
    update = Update.de_json(request.get_json(), tg_application.bot)
    loop.run_until_complete(tg_application.process_update(update))
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
