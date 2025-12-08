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
from telegram.constants import ParseMode
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

GREETING_PHRASES = [
    "Привет, {name}! Я ассистент компании ECO Стены. Помогу с подбором материалов и расчётом панелей. 😊",
    "Рад знакомству, {name}! Я здесь, чтобы помочь вам с продукцией ECO Стены и ответить на вопросы.",
    "Здравствуйте, {name}! Если планируете ремонт или обновление интерьера — давайте подберём материалы вместе.",
    "{name}, привет! Я подскажу по WPC панелям, профилям, каталогу и примерному расчёту под ваши размеры.",
    "Добро пожаловать, {name}! Рассказывайте, какой у вас объект — подберём оптимальное решение из наших материалов.",
]

# Файл для хранения статистики (на Render - ephemeral, но для простоты)
STATS_FILE = "/tmp/eco_stats.json"

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

# ============================
#   КАТАЛОГ МАТЕРИАЛОВ
# ============================

WALL_PRODUCTS = {
    "WPC Бамбук угольный": {
        5: {
            "width_mm": 1220,
            "weight_kg_per_m2": 4.0,
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
            "weight_kg_per_m2": 5.0,
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
            "weight_kg_per_m2": 4.0,
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
            "weight_kg_per_m2": 5.0,
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
            "weight_kg_per_m2": 5.6,
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
            "weight_kg_per_m2": 6.0,
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
            "weight_kg_per_m2": 8.0,
            "panels": {
                2440: {"area_m2": 2.928, "price_rub": 18000},
                2600: {"area_m2": 3.12, "price_rub": 19100},
                2800: {"area_m2": 3.36, "price_rub": 20600},
                3000: {"area_m2": 3.6, "price_rub": 22100},
                3200: {"area_m2": 3.84, "price_rub": 23500},
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
        [InlineKeyboardButton("🧱 Рассчитать материалы", callback_data="main|calc")],
        [InlineKeyboardButton("ℹ️ Информация", callback_data="main|info")],
        [InlineKeyboardButton("📚 Получить каталоги", callback_data="main|catalogs")],
        [InlineKeyboardButton("📄 Получить презентацию", callback_data="main|presentation")],
        [InlineKeyboardButton("📞 Контактная информация", callback_data="main|contacts")],
        [InlineKeyboardButton("🤝 Хочу стать партнёром", callback_data="main|partner")],
    ]
    if ADMIN_CHAT_ID:
        buttons.append([InlineKeyboardButton("⚙️ Администрирование", callback_data="main|admin")])
    return InlineKeyboardMarkup(buttons)

def build_back_button(text="Назад"):
    return [[InlineKeyboardButton(text, callback_data="back|main")]]

def build_calc_category_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🧱 Стеновые панели WPC", callback_data="calc_cat|walls")],
        [InlineKeyboardButton("🔩 Профили", callback_data="calc_cat|profiles")],
        [InlineKeyboardButton("📏 Реечные панели", callback_data="calc_cat|slats")],
        [InlineKeyboardButton("🎨 3D-панели", callback_data="calc_cat|3d")],
        [InlineKeyboardButton("🪨 Гибкий камень", callback_data="calc_cat|flex")],
    ]
    rows += build_back_button("В главное меню")
    return InlineKeyboardMarkup(rows)

def build_wall_product_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for code, title in PRODUCT_CODES.items():
        buttons.append([InlineKeyboardButton(text=title, callback_data=f"product|{code}")])
    buttons += build_back_button("Назад")
    return InlineKeyboardMarkup(buttons)

def build_thickness_keyboard(code: str) -> InlineKeyboardMarkup:
    title = PRODUCT_CODES[code]
    thicknesses = WALL_PRODUCTS[title].keys()
    buttons = [[InlineKeyboardButton(f"{thick} мм", callback_data=f"thickness|{code}|{thick}")] for thick in thicknesses]
    buttons += build_back_button("Назад")
    return InlineKeyboardMarkup(buttons)

def build_length_keyboard(code: str, thick: int) -> InlineKeyboardMarkup:
    title = PRODUCT_CODES[code]
    lengths = WALL_PRODUCTS[title][thick]['panels'].keys()
    buttons = [[InlineKeyboardButton(f"{length} мм", callback_data=f"length|{code}|{thick}|{length}")] for length in lengths]
    buttons += build_back_button("Назад")
    return InlineKeyboardMarkup(buttons)

def build_profile_thickness_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("5 мм", callback_data="profile_thick|5")],
        [InlineKeyboardButton("8 мм", callback_data="profile_thick|8")],
    ]
    buttons += build_back_button("Назад")
    return InlineKeyboardMarkup(buttons)

def build_profile_type_keyboard(thick: int) -> InlineKeyboardMarkup:
    types = PROFILES[thick].keys()
    buttons = [[InlineKeyboardButton(name, callback_data=f"profile_type|{thick}|{name.replace(' ', '_')}")] for name in types]
    buttons += build_back_button("Назад")
    return InlineKeyboardMarkup(buttons)

def build_slats_type_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("WPC рейки", callback_data="slats_type|wpc")],
        [InlineKeyboardButton("Деревянные рейки", callback_data="slats_type|wood")],
    ]
    buttons += build_back_button("Назад")
    return InlineKeyboardMarkup(buttons)

def build_3d_size_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("600x1200 мм", callback_data="3d_size|var1")],
        [InlineKeyboardButton("1200x3000 мм", callback_data="3d_size|var2")],
    ]
    buttons += build_back_button("Назад")
    return InlineKeyboardMarkup(buttons)

def build_add_another_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("Да, добавить ещё материал", callback_data="add_another|yes")],
        [InlineKeyboardButton("Расчёт окончен", callback_data="add_another|no")],
    ]
    return InlineKeyboardMarkup(buttons)

def build_custom_name_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("Да, знаю название/артикул", callback_data="custom_name|yes")],
        [InlineKeyboardButton("Нет, стандартный", callback_data="custom_name|no")],
    ]
    return InlineKeyboardMarkup(buttons)

def build_units_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("Метры (м)", callback_data="units|m")],
        [InlineKeyboardButton("Миллиметры (мм)", callback_data="units|mm")],
    ]
    return InlineKeyboardMarkup(buttons)

def build_yes_no_keyboard(yes_data, no_data) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("Да", callback_data=yes_data)],
        [InlineKeyboardButton("Нет", callback_data=no_data)],
    ]
    return InlineKeyboardMarkup(buttons)

def build_contacts_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("Группа в Telegram", url="https://t.me/ecosteni")],
        [InlineKeyboardButton("Связаться с администратором", url="https://t.me/DService82")],
        [InlineKeyboardButton("Сайт", url="https://ecosteni.ru/")],
        [InlineKeyboardButton("Написать в WhatsApp", url="https://wa.me/79780223222")],
    ]
    buttons += build_back_button("Назад")
    return InlineKeyboardMarkup(buttons)

def build_admin_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("📊 Сатистика", callback_data="admin|stats")],
        [InlineKeyboardButton("📢 Рассылка", callback_data="admin|broadcast")],
    ]
    buttons += build_back_button("Назад")
    return InlineKeyboardMarkup(buttons)

def build_partner_role_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("🛒 Розничный магазин", callback_data="partner_role|retail")],
        [InlineKeyboardButton("🔨 Монтажная бригада", callback_data="partner_role|installer")],
        [InlineKeyboardButton("🎨 Дизайнер/Архитектор", callback_data="partner_role|designer")],
        [InlineKeyboardButton("❓ Другое", callback_data="partner_role|other")],
    ]
    return InlineKeyboardMarkup(buttons)

async def send_greeting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.first_name or user.username or "друг"
    greeting = random.choice(GREETING_PHRASES).format(name=name)
    try:
        await context.bot.send_photo(chat_id=update.effective_chat.id, photo=WELCOME_PHOTO_URL, caption=greeting)
    except Exception as e:
        logger.error(f"Error sending photo: {e}")
        await context.bot.send_message(chat_id=update.effective_chat.id, text=greeting)
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Чем могу помочь?", reply_markup=build_main_menu_keyboard())

# For stats: on start, add user
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = load_stats()
    today = datetime.now(timezone.utc).date().isoformat()
    if stats['today'] != today:
        stats['users_today'] = set()
        stats['calc_today'] = 0
        stats['today'] = today
    stats['users'].add(update.effective_chat.id)
    stats['users_today'].add(update.effective_chat.id)
    save_stats(stats)
    await send_greeting(update, context)

# ============================
#   РАССЧЁТ
# ============================

def parse_size(text: str, unit: str) -> float:
    try:
        # Улучшенный парсинг: поддержка простых выражений вроде "1.2 + 3.4"
        # Безопасный eval только для математических операций
        allowed_names = {"__builtins__": {}, "math": math}
        expr = re.sub(r'[^\d\s+\-*/().]', '', text.strip())  # Очистка от нецифр кроме операторов
        if expr:
            num = eval(expr, allowed_names)
        else:
            num = float(text.strip())
        return num
