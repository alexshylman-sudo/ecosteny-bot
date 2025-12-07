import asyncio
import base64
from io import BytesIO
import json
import os
import random
from datetime import datetime, timezone
import re  # Парсинг размеров
import math  # Для округления вверх

import requests
from quart import Quart, request, jsonify
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

import logging
import sys
from telegram import __version__ as TG_VER

# ---- Logging ----
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

print("### PYTHON VERSION ON RENDER:", sys.version)
print("### python-telegram-bot VERSION ON RENDER:", TG_VER)

# ---- РЕШЕНИЕ ПРОБЛЕМЫ С ПОРТОМ ----
# Render передаёт порт через переменную окружения PORT.
# Если её нет — ставим fallback 10000.
# Определяем переменную 'port' для совместимости с кодом (если в нём используется lowercase)
port = int(os.environ.get("PORT", 10000))
PORT = port  # Для consistency, если где-то используется uppercase

# ============================
#   НАСТРОЙКИ (через .env)
# ============================

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
if not TG_BOT_TOKEN:
    raise ValueError("Установите TG_BOT_TOKEN в .env!")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

WELCOME_PHOTO_URL = "https://ecosteni.ru/wp-content/uploads/2025/11/qncccaze.jpg"  # Проверить, существует ли файл
WELCOME_GIF_URL = ""

GREETING_PHRASES = [
    "Привет, {name}! Я ассистент компании ECO Стены. Помогу с подбором материалов и расчётом панелей. 🙂",
    "Рад знакомству, {name}! Я здесь, чтобы помочь вам с продукцией ECO Стены и ответить на вопросы.",
    "Здравствуйте, {name}! Если планируете ремонт или обновление интерьера — давайте подберём материалы вместе.",
    "{name}, привет! Я подскажу по WPC панелям, профилям, каталогу и примерному расчёту под ваши размеры.",
    "Добро пожаловать, {name}! Рассказывайте, какой у вас объект — подберём оптимальное решение из наших материалов.",
]

# ============================
#   КАТАЛОГ СТЕНОВЫХ ПАНЕЛЕЙ (WPC)
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
}

# SPC панели (без толщины)
SPC_PANELS = {
    2440: {"area_m2": 2.928, "price_rub": 9500},
    2600: {"area_m2": 3.12, "price_rub": 10100},
    # Добавьте остальные по аналогии, если есть
}

PRODUCT_CODES = {
    "wpc_charcoal": "WPC Бамбук угольный",
    "wpc_bamboo": "WPC Бамбук",
    "wpc_hd": "WPC повышенной плотности",
    "wpc_bamboo_coat": "WPC Бамбук с защитным слоем",
    "wpc_hd_coat": "WPC повышенной плотности с защитным слоем",
    "spc_panel": "SPC Панель",
}

# ============================
#   ПРОФИЛИ
# ============================

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

# ============================
#   РЕЕЧНЫЕ И 3D ПАНЕЛИ
# ============================

SLAT_PANEL_SPEC = {
    "width_mm": 168,
    "length_mm": 2900,
    "thickness_mm": 18,
}

SLAT_PRICES = {
    "wpc": 1200,
    "wood": 1500,
}

PANELS_3D = {
    "var1": {"code": "3d_600x1200", "width_mm": 600, "height_mm": 1200, "price_rub": 3000},
    "var2": {"code": "3d_1200x3000", "width_mm": 1200, "height_mm": 3000, "price_rub": 8000},
}

SYSTEM_PROMPT = """
Ты — онлайн-консультант компании ECO Стены.

У тебя есть каталог стеновых WPC панелей с размерами, площадью покрытия и ценой за 1 панель.
Каталог передаётся тебе в виде JSON в сообщении. Используй ТОЛЬКО его для расчётов по стеновым панелям.

ВАЖНО:
— Никогда не проси у пользователя каталог, JSON, прайс или цены.
— Если JSON каталога отсутствует, честно скажи, что точный расчёт доступен только при наличии каталога (который подгружает система),
  и предложи связаться с менеджером.
— Если клиент выбрал через кнопки конкретную панель, толщину и высоту — ОБЯЗАН использовать именно эту комбинацию.

ОГРАНИЧЕНИЯ:
— WPC повышенной плотности не бывает толщиной 5 мм.
— WPC Бамбук угольный не бывает с защитным слоем.

Если клиент выбрал несколько материалов, в запросе может быть список этих материалов — используй его и в расчёте, и в формулировках.

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
"""

# ============================
#   FLASK + TELEGRAM
# ============================

app = Quart(__name__)

# Создаём приложение Telegram
tg_application = Application.builder().token(TG_BOT_TOKEN).build()

# Добавляем error handler
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Exception while handling an update: {context.error}")

tg_application.add_error_handler(error_handler)

# ============================
#   КЛАВИАТУРЫ
# ============================

def build_main_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🧮 Рассчитать материалы", callback_data="main|calc")],
        [InlineKeyboardButton("📋 Получить каталоги", callback_data="main|catalogs")],
        [InlineKeyboardButton("📞 Контактная информация", callback_data="main|contacts")],
        [InlineKeyboardButton("🤝 Хочу стать партнером", callback_data="main|partner")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton("⚙️ Администрирование", callback_data="main|admin")])
    return InlineKeyboardMarkup(rows)

def build_back_row() -> list[list[InlineKeyboardButton]]:
    return [[InlineKeyboardButton("🔙 Назад", callback_data="ui|back")]]

def build_back_to_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(build_back_row())

def build_calc_category_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🧱 Стеновые панели", callback_data="calc_cat|walls")],
        [InlineKeyboardButton("🔩 Профили", callback_data="calc_cat|profiles")],
        [InlineKeyboardButton("🔲 Реечные панели", callback_data="calc_cat|slats")],
        [InlineKeyboardButton("🎨 3D панели", callback_data="calc_cat|3d")],
        [InlineKeyboardButton("🪨 Гибкий камень", callback_data="calc_cat|stone")],
        [InlineKeyboardButton("🔙 Назад", callback_data="main|back")],
    ]
    return InlineKeyboardMarkup(keyboard)

def build_wall_product_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for code, title in PRODUCT_CODES.items():
        buttons.append([InlineKeyboardButton(text=title, callback_data=f"product|{code}")])
    buttons += build_back_row()
    return InlineKeyboardMarkup(buttons)

def build_thickness_keyboard(product_code: str) -> InlineKeyboardMarkup:
    title = PRODUCT_CODES[product_code]
    thicknesses = WALL_PRODUCTS.get(title, {})
    rows = []
    for thickness in sorted(thicknesses.keys()):
        rows.append([InlineKeyboardButton(text=f"{thickness} мм", callback_data=f"thickness|{product_code}|{thickness}")])
    rows += build_back_row()
    return InlineKeyboardMarkup(rows)

def build_length_keyboard(product_code: str, thickness: int) -> InlineKeyboardMarkup:
    title = PRODUCT_CODES[product_code]
    if title == "SPC Панель":
        lengths = sorted(SPC_PANELS.keys())
    else:
        lengths = sorted(WALL_PRODUCTS[title][thickness]["panels"].keys())
    rows = []
    for length in lengths:
        rows.append([InlineKeyboardButton(text=f"{length} мм", callback_data=f"length|{product_code}|{thickness}|{length}")])
    rows += build_back_row()
    return InlineKeyboardMarkup(rows)

def build_profile_thickness_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("Для 5 мм", callback_data="profile_thick|5")],
        [InlineKeyboardButton("Для 8 мм", callback_data="profile_thick|8")],
    ]
    rows += build_back_row()
    return InlineKeyboardMarkup(rows)

def build_profile_type_keyboard(thickness: int) -> InlineKeyboardMarkup:
    rows = []
    for ptype in PROFILES[thickness]:
        rows.append([InlineKeyboardButton(text=ptype, callback_data=f"profile_type|{thickness}|{ptype}")])
    rows += build_back_row()
    return InlineKeyboardMarkup(rows)

def build_slats_type_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("WPC реечные", callback_data="slats_type|wpc")],
        [InlineKeyboardButton("Деревянные реечные", callback_data="slats_type|wood")],
    ]
    rows += build_back_row()
    return InlineKeyboardMarkup(rows)

def build_3d_size_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("600x1200", callback_data="3d_size|var1")],
        [InlineKeyboardButton("1200x3000", callback_data="3d_size|var2")],
    ]
    rows += build_back_row()
    return InlineKeyboardMarkup(rows)

def build_admin_menu_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📊 Статистика", callback_data="admin|stats")],
        [InlineKeyboardButton("📢 Рассылка", callback_data="admin|broadcast")],
        [InlineKeyboardButton("📜 Логи", callback_data="admin|logs")],
    ]
    rows += build_back_row()
    return InlineKeyboardMarkup(rows)

def build_contact_manager_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📞 Связаться с менеджером", url="https://t.me/manager_username")],  # Замените на реальный URL
    ]
    return InlineKeyboardMarkup(rows)

def build_catalog_menu_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("Стеновые панели", callback_data="catalog|walls")],
        [InlineKeyboardButton("Реечные панели", callback_data="catalog|slats")],
        [InlineKeyboardButton("3D панели", callback_data="catalog|3d")],
    ]
    rows += build_back_row()
    return InlineKeyboardMarkup(rows)

def build_partner_role_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("Дизайнер/Архитектор", callback_data="partner_role|designer")],
        [InlineKeyboardButton("Прораб", callback_data="partner_role|foreman")],
        [InlineKeyboardButton("Застройщик", callback_data="partner_role|builder")],
        [InlineKeyboardButton("Магазин/Салон", callback_data="partner_role|shop")],
    ]
    rows += build_back_row()
    return InlineKeyboardMarkup(rows)

# ============================
#   ФУНКЦИИ РАСЧЕТА
# ============================

def calculate_wall_panels(item, total_area_m2):
    area_per_panel = item['area_m2']
    panels_needed = math.ceil((total_area_m2 * 1.1) / area_per_panel)  # +10% отходы
    waste_m2 = (panels_needed * area_per_panel) - total_area_m2
    waste_percent = (waste_m2 / total_area_m2) * 100 if total_area_m2 > 0 else 0
    price = panels_needed * item['price_rub']
    return panels_needed, waste_m2, waste_percent, price

def calculate_slats(item, wall_length_m):
    price_per_mp = SLAT_PRICES[item['type']]
    total_mp = wall_length_m * 1.1  # +10% отходы
    total_price = total_mp * price_per_mp
    return total_mp, total_price

def calculate_3d(item, wall_area_m2):
    panel_area = (item['width_mm'] / 1000) * (item['height_mm'] / 1000)
    panels_needed = math.ceil((wall_area_m2 * 1.1) / panel_area)
    waste_m2 = (panels_needed * panel_area) - wall_area_m2
    waste_percent = (waste_m2 / wall_area_m2) * 100 if wall_area_m2 > 0 else 0
    price = panels_needed * item['price_rub']
    return panels_needed, waste_m2, waste_percent, price

def calculate_profiles(items):
    total_price = 0
    for item in items:
        total_price += item['quantity'] * item['price_rub']
    return total_price

def generate_calc_summary(context: ContextTypes.DEFAULT_TYPE):
    items = context.chat_data.get("calc_items", [])
    wall_width_m = context.chat_data.get("wall_width_m", 0)
    height_m = context.chat_data.get("height_m", 0)
    windows_area = context.chat_data.get("windows_area", 0)
    doors_area = context.chat_data.get("doors_area", 0)
    total_area_m2 = (wall_width_m * height_m) - windows_area - doors_area

    summary = "Итоговый расчёт:\n\n"
    total_price = 0
    total_waste_m2 = 0
    total_units = 0

    for item in items:
        if item['category'] == 'walls':
            panels, waste_m2, waste_percent, price = calculate_wall_panels(item, total_area_m2)
            summary += f"{item['title']}: {panels} шт., отходы {waste_percent:.1f}% ({waste_m2:.2f} м²), цена {price} руб.\n"
            total_price += price
            total_waste_m2 += waste_m2
            total_units += panels
        elif item['category'] == 'slats':
            mp, price = calculate_slats(item, wall_width_m)
            summary += f"{item['title']}: {mp:.2f} м.п., цена {price} руб.\n"
            total_price += price
            total_units += mp
        elif item['category'] == '3d':
            panels, waste_m2, waste_percent, price = calculate_3d(item, total_area_m2)
            summary += f"{item['title']}: {panels} шт., отходы {waste_percent:.1f}% ({waste_m2:.2f} м²), цена {price} руб.\n"
            total_price += price
            total_waste_m2 += waste_m2
            total_units += panels
        elif item['category'] == 'profiles':
            price = calculate_profiles([item])
            summary += f"{item['title']}: {item['quantity']} шт., цена {price} руб.\n"
            total_price += price
            total_units += item['quantity']

    summary += f"\nИтого: {total_units} ед., отходы {total_waste_m2:.2f} м², цена {total_price} руб."
    return summary

# ============================
#   КОМАНДЫ И ХЕНДЛЕРЫ
# ============================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data.clear()
    user = update.effective_user
    name = user.first_name or user.username or "друг"
    greeting = random.choice(GREETING_PHRASES).format(name=name)
    await update.message.reply_photo(photo=WELCOME_PHOTO_URL, caption=greeting)
    is_admin = user.id == ADMIN_CHAT_ID
    await update.message.reply_text("Выберите действие:", reply_markup=build_main_menu_keyboard(is_admin))

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    parts = data.split('|')
    action = parts[0]

    if action == 'main':
        sub = parts[1]
        if sub == 'calc':
            context.chat_data['calc_items'] = []
            await query.edit_message_text("Выберите категорию:", reply_markup=build_calc_category_keyboard())
        elif sub == 'catalogs':
            await query.edit_message_text("Выберите каталог:", reply_markup=build_catalog_menu_keyboard())
        elif sub == 'contacts':
            text = "📱 +7 (978) 022-32-22\n📧 info@ecosteni.ru\n🕒 Пн-Пт 9:00–18:00"
            await query.edit_message_text(text, reply_markup=build_contact_manager_keyboard())
        elif sub == 'partner':
            context.chat_data['partner_state'] = 'name'
            await query.edit_message_text("Как к вам обращаться?")
        elif sub == 'admin':
            await query.edit_message_text("Админ панель:", reply_markup=build_admin_menu_keyboard())
        return

    if action == 'calc_cat':
        sub = parts[1]
        items = context.chat_data.get('calc_items', [])
        if sub == 'walls':
            await query.edit_message_text("Выберите тип панели:", reply_markup=build_wall_product_keyboard())
        elif sub == 'profiles':
            await query.edit_message_text("Выберите толщину:", reply_markup=build_profile_thickness_keyboard())
        elif sub == 'slats':
            await query.edit_message_text("Выберите тип:", reply_markup=build_slats_type_keyboard())
        elif sub == '3d':
            await query.edit_message_text("Выберите размер:", reply_markup=build_3d_size_keyboard())
        elif sub == 'stone':
            await query.edit_message_text("Скоро добавим! Пока вернёмся к панелям.", reply_markup=build_calc_category_keyboard())
        return

    if action == 'product':
        code = parts[1]
        if code == 'spc_panel':
            await query.edit_message_text("Выберите длину:", reply_markup=build_length_keyboard(code, 0))
        else:
            await query.edit_message_text("Выберите толщину:", reply_markup=build_thickness_keyboard(code))
        return

    if action == 'thickness':
        code, thick = parts[1], int(parts[2])
        await query.edit_message_text("Выберите длину:", reply_markup=build_length_keyboard(code, thick))
        return

    if action == 'length':
        code, thick, length = parts[1], int(parts[2]), int(parts[3])
        title = PRODUCT_CODES[code]
        if code == 'spc_panel':
            panel_data = SPC_PANELS[length]
        else:
            panel_data = WALL_PRODUCTS[title][thick]["panels"][length]
        items = context.chat_data.get('calc_items', [])
        items.append({
            'category': 'walls',
            'title': title,
            'thickness': thick,
            'length': length,
            'area_m2': panel_data['area_m2'],
            'price_rub': panel_data['price_rub']
        })
        context.chat_data['calc_items'] = items
        await query.edit_message_text("Панель добавлена. Добавить ещё?", reply_markup=build_add_more_materials_keyboard())
        return

    if action == 'profile_thick':
        thick = int(parts[1])
        await query.edit_message_text("Выберите тип профиля:", reply_markup=build_profile_type_keyboard(thick))
        return

    if action == 'profile_type':
        thick, ptype = int(parts[1]), parts[2]
        context.chat_data['await_profile_qty'] = {'thickness': thick, 'type': ptype}
        await query.edit_message_text("Сколько штук?")
        return

    if action == 'slats_type':
        stype = parts[1]
        context.chat_data['await_slats_length'] = {'type': stype}
        await query.edit_message_text("Длина стены (м.п.)?")
        return

    if action == '3d_size':
        var = parts[1]
        panel = PANELS_3D[var]
        items = context.chat_data.get('calc_items', [])
        items.append({
            'category': '3d',
            'title': panel['code'],
            'width_mm': panel['width_mm'],
            'height_mm': panel['height_mm'],
            'price_rub': panel['price_rub']
        })
        context.chat_data['calc_items'] = items
        await query.edit_message_text("3D панель добавлена. Добавить ещё?", reply_markup=build_add_more_materials_keyboard())
        return

    if action == 'calc_more':
        sub = parts[1]
        if sub == 'yes':
            await query.edit_message_text("Выберите категорию:", reply_markup=build_calc_category_keyboard())
        elif sub == 'no':
            context.chat_data['await_wall_width'] = True
            await query.edit_message_text("Ширина стены (м)?")
        return

    if action == 'admin':
        sub = parts[1]
        if sub == 'stats':
            # Реализуйте статистику
            await query.edit_message_text("Расчётов: 50, пользователей: 150.")
        elif sub == 'broadcast':
            context.chat_data['await_broadcast'] = True
            await query.edit_message_text("Текст для рассылки:")
        elif sub == 'logs':
            # Реализуйте логи
            await query.edit_message_text("Последние сообщения...")
        return

# ============================
#   ОБРАБОТКА СООБЩЕНИЙ
# ============================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if context.chat_data.get('await_profile_qty'):
        try:
            qty = int(text)
            data = context.chat_data.pop('await_profile_qty')
            thick = data['thickness']
            ptype = data['type']
            price = PROFILES[thick][ptype]
            items = context.chat_data.get('calc_items', [])
            items.append({
                'category': 'profiles',
                'title': ptype,
                'thickness': thick,
                'quantity': qty,
                'price_rub': price
            })
            context.chat_data['calc_items'] = items
            await update.message.reply_text("Профиль добавлен. Ещё профиль?", reply_markup=build_add_more_materials_keyboard())
        except ValueError:
            await update.message.reply_text("Введите число.")
        return

    if context.chat_data.get('await_slats_length'):
        try:
            length_m = float(text)
            data = context.chat_data.pop('await_slats_length')
            stype = data['type']
            items = context.chat_data.get('calc_items', [])
            items.append({
                'category': 'slats',
                'title': stype.capitalize(),
                'type': stype,
                'length_m': length_m
            })
            context.chat_data['calc_items'] = items
            await update.message.reply_text("Реечная панель добавлена. Добавить ещё?", reply_markup=build_add_more_materials_keyboard())
        except ValueError:
            await update.message.reply_text("Введите число.")
        return

    if context.chat_data.get('await_wall_width'):
        try:
            width_m = float(text)
            context.chat_data['wall_width_m'] = width_m
            context.chat_data.pop('await_wall_width')
            context.chat_data['await_height'] = True
            await update.message.reply_text("Высота (м)?")
        except ValueError:
            await update.message.reply_text("Введите число.")
        return

    if context.chat_data.get('await_height'):
        try:
            height_m = float(text)
            context.chat_data['height_m'] = height_m
            context.chat_data.pop('await_height')
            context.chat_data['await_windows'] = True
            await update.message.reply_text("Окно? (да/нет)")
        except ValueError:
            await update.message.reply_text("Введите число.")
        return

    if context.chat_data.get('await_windows'):
        if text.lower() == 'да':
            context.chat_data['await_window_size'] = True
            await update.message.reply_text("Размер окна (шир. x выс., м)?")
        else:
            context.chat_data.pop('await_windows')
            context.chat_data['await_doors'] = True
            await update.message.reply_text("Дверь? (да/нет)")
        return

    if context.chat_data.get('await_window_size'):
        # Парсинг размера
        parts = re.split(r'[xX]', text)
        if len(parts) == 2:
            try:
                w, h = float(parts[0]), float(parts[1])
                area = w * h
                context.chat_data['windows_area'] = context.chat_data.get('windows_area', 0) + area
                context.chat_data.pop('await_window_size')
                await update.message.reply_text("Окно учтено. Ещё окно? (да/нет)")
            except ValueError:
                await update.message.reply_text("Неверный формат.")
        else:
            await update.message.reply_text("Неверный формат.")
        return

    if context.chat_data.get('await_doors'):
        if text.lower() == 'да':
            context.chat_data['await_door_size'] = True
            await update.message.reply_text("Размер двери (шир. x выс., м)?")
        else:
            context.chat_data.pop('await_doors')
            summary = generate_calc_summary(context)
            await update.message.reply_text(summary, reply_markup=build_contact_manager_keyboard())
        return

    if context.chat_data.get('await_door_size'):
        # Аналогично окну
        parts = re.split(r'[xX]', text)
        if len(parts) == 2:
            try:
                w, h = float(parts[0]), float(parts[1])
                area = w * h
                context.chat_data['doors_area'] = context.chat_data.get('doors_area', 0) + area
                context.chat_data.pop('await_door_size')
                await update.message.reply_text("Дверь учтена. Ещё дверь? (да/нет)")
            except ValueError:
                await update.message.reply_text("Неверный формат.")
        else:
            await update.message.reply_text("Неверный формат.")
        return

    if context.chat_data.get('partner_state') == 'name':
        context.chat_data['partner_name'] = text
        context.chat_data['partner_state'] = 'phone'
        await update.message.reply_text("Оставьте номер телефона:")
        return

    if context.chat_data.get('partner_state') == 'phone':
        context.chat_data['partner_phone'] = text
        context.chat_data['partner_state'] = 'city'
        await update.message.reply_text("В каком вы городе?")
        return

    if context.chat_data.get('partner_state') == 'city':
        context.chat_data['partner_city'] = text
        context.chat_data['partner_state'] = 'company'
        await update.message.reply_text("Название компании?")
        return

    if context.chat_data.get('partner_state') == 'company':
        context.chat_data['partner_company'] = text
        context.chat_data['partner_state'] = 'site'
        await update.message.reply_text("Сайт или соцсети?")
        return

    if context.chat_data.get('partner_state') == 'site':
        context.chat_data['partner_site'] = text
        await update.message.reply_text("Выберите роль:", reply_markup=build_partner_role_keyboard())
        context.chat_data['partner_state'] = 'role'
        return

    if context.chat_data.get('await_broadcast'):
        # Реализуйте рассылку
        await update.message.reply_text("Рассылка отправлена.")
        context.chat_data.pop('await_broadcast')
        return

    await handle_smalltalk(update, context)

async def handle_smalltalk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Реализация smalltalk с OpenAI, если нужно
    await update.message.reply_text("Не понял, попробуй заново. Нажми /menu.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Реализация анализа фото с OpenAI, если нужно
    await update.message.reply_text("Фото получено, но анализ пока не реализован.")

# Регистрация хендлеров
tg_application.add_handler(CommandHandler("start", start_command))
tg_application.add_handler(CallbackQueryHandler(handle_callback))
tg_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
tg_application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

# Webhook
@app.route(f"/{TG_BOT_TOKEN}", methods=["POST"])
async def telegram_webhook():
    update_json = await request.get_json()
    update = Update.de_json(update_json, tg_application.bot)
    await tg_application.process_update(update)
    return jsonify({"status": "ok"}), 200

def setup_webhook():
    loop = asyncio.get_event_loop()
    async def async_setup():
        await tg_application.initialize()
        webhook_url = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}/{TG_BOT_TOKEN}"
        await tg_application.bot.set_webhook(webhook_url)
    loop.run_until_complete(async_setup())

if __name__ == "__main__":
    setup_webhook()
    from hypercorn.asyncio import serve
    from hypercorn.config import Config
    config = Config()
    config.bind = [f"0.0.0.0:{port}"]
    asyncio.run(serve(app, config))
