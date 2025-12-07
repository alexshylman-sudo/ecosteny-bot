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

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

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
            # Optionally remove corrupted file
            os.remove(STATS_FILE)
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
        [InlineKeyboardButton("Рассчитать материалы", callback_data="main|calc")],
        [InlineKeyboardButton("Информация", callback_data="main|info")],
        [InlineKeyboardButton("Получить каталоги", callback_data="main|catalogs")],
        [InlineKeyboardButton("Получить презентацию", callback_data="main|presentation")],
        [InlineKeyboardButton("Контактная информация", callback_data="main|contacts")],
        [InlineKeyboardButton("Хочу стать партнёром", callback_data="main|partner")],
    ]
    if ADMIN_CHAT_ID:
        buttons.append([InlineKeyboardButton("Администрирование", callback_data="main|admin")])
    buttons.append([InlineKeyboardButton("Поделиться", callback_data="share")])
    return InlineKeyboardMarkup(buttons)

def build_back_button(text="Назад"):
    return [[InlineKeyboardButton(text, callback_data="back|main")]]

def build_calc_category_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("Стеновые панели WPC", callback_data="calc_cat|walls")],
        [InlineKeyboardButton("SPC панель", callback_data="calc_cat|spc")],
        [InlineKeyboardButton("Профили", callback_data="calc_cat|profiles")],
        [InlineKeyboardButton("Реечные панели", callback_data="calc_cat|slats")],
        [InlineKeyboardButton("3D-панели", callback_data="calc_cat|3d")],
        [InlineKeyboardButton("Гибкий камень", callback_data="calc_cat|flex")],
    ]
    rows += build_back_button("В главное меню")
    return InlineKeyboardMarkup(rows)

def build_wall_product_keyboard(is_spc=False) -> InlineKeyboardMarkup:
    buttons = []
    codes = PRODUCT_CODES if not is_spc else {"spc_panel": "SPC Панель"}
    for code, title in codes.items():
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

def build_add_more_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("Да, добавить ещё", callback_data="add_more|yes")],
        [InlineKeyboardButton("Нет, продолжить расчёт", callback_data="add_more|no")],
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
        [InlineKeyboardButton("Написать в Telegram", url=f"https://t.me/{TG_GROUP}")],
    ]
    buttons += build_back_button("Назад")
    return InlineKeyboardMarkup(buttons)

def build_admin_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("Сатистика", callback_data="admin|stats")],
        [InlineKeyboardButton("Рассылка", callback_data="admin|broadcast")],
    ]
    buttons += build_back_button("Назад")
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
        num = float(text.strip())
        return num / 1000 if unit == "mm" else num
    except:
        return 0.0

def calculate_item(item, wall_width_m, wall_height_m, deduct_area_m2, unit):
    category = item['category']
    if category in ['walls', 'spc']:
        title = PRODUCT_CODES[item['product_code']]
        thickness = item.get('thickness', 0)
        length_mm = item['length']
        panel = WALL_PRODUCTS[title][thickness]['panels'][length_mm]
        area_m2 = panel['area_m2']
        price = panel['price_rub']
        net_area = wall_width_m * wall_height_m - deduct_area_m2
        required_area = net_area * 1.1  # 10% reserve
        panels = math.ceil(required_area / area_m2)
        total_area = panels * area_m2
        waste_area = total_area - net_area
        waste_pct = (waste_area / total_area) * 100 if total_area > 0 else 0
        cost = panels * price
        return f"""
Выбранный материал: {title}
Толщина: {thickness} мм (если применимо)
Высота: {length_mm} мм
Название/артикул: {item.get('custom_name', 'Не указано')}
📏 Ширина зоны: {wall_width_m * 1000 if unit == 'mm' else wall_width_m} {unit}
📏 Площадь зоны: {wall_width_m} м × {wall_height_m} м = {wall_width_m * wall_height_m} м²
📏 Вычет (окна/двери): {deduct_area_m2} м²
📏 Чистая площадь: {net_area} м²
📦 Площадь панели: {area_m2} м²
📦 Количество: {panels} шт.
📦 Общая площадь: {total_area} м²
📏 Отходы: {waste_area:.2f} м² ({waste_pct:.2f}%)
💰 Стоимость: {cost} ₽
"""

    elif category == 'profiles':
        thickness = item['thickness']
        type_name = item['type']
        quantity = item['quantity']
        price = PROFILES[thickness][type_name]
        cost = quantity * price
        return f"""
Профиль: {type_name}, {thickness} мм
Количество: {quantity} шт.
💰 Стоимость: {cost} ₽
"""

    elif category == 'slats':
        type_name = 'WPC' if item['type'] == 'wpc' else 'Деревянные'
        price_mp = SLAT_PRICES[item['type']]
        length_m = wall_width_m  # Длина стены в м
        required = length_m * 1.1
        cost = math.ceil(required) * price_mp  # Округление вверх
        waste = required - length_m
        return f"""
Реечные панели: {type_name}
Длина стены: {length_m} м.п.
Необходимая длина: {required:.2f} м.п.
Отходы: {waste:.2f} м.п. (10%)
💰 Стоимость: {cost} ₽
"""

    elif category == '3d':
        var = PANELS_3D[item['var']]
        area_m2 = var['area_m2']
        price = var['price_rub']
        net_area = wall_width_m * wall_height_m - deduct_area_m2
        required_area = net_area * 1.1
        panels = math.ceil(required_area / area_m2)
        total_area = panels * area_m2
        waste_area = total_area - net_area
        waste_pct = (waste_area / total_area) * 100 if total_area > 0 else 0
        cost = panels * price
        return f"""
3D панели: {var['code']}
Площадь панели: {area_m2} м²
Количество: {panels} шт.
Общая площадь: {total_area} м²
Отходы: {waste_area:.2f} м² ({waste_pct:.2f}%)
💰 Стоимость: {cost} ₽
"""

    return ""

# ============================
#   CALLBACK HANDLER
# ============================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    parts = data.split('|')
    action = parts[0]

    if action == 'main':
        sub = parts[1]
        if sub == 'calc':
            context.chat_data['mode'] = 'calc'
            context.chat_data['calc_items'] = []
            context.chat_data['phase'] = 'select_cat'
            await query.edit_message_text("Расчёт материалов:", reply_markup=build_calc_category_keyboard())
        elif sub == 'info':
            await query.edit_message_text("Информация в разработке.")
        elif sub == 'catalogs':
            await query.edit_message_text("Каталог в разработке.")
        elif sub == 'presentation':
            await context.bot.send_document(chat_id=query.message.chat_id, document=PRESENTATION_URL, caption="Презентация ECO Стены")
        elif sub == 'contacts':
            text = "Телефон: +7 (978) 022-32-22\nПочта: info@ecosteni.ru\nГрафик: Пн-Пт 9:00–18:00"
            await query.edit_message_text(text, reply_markup=build_contacts_keyboard())
        elif sub == 'partner':
            context.chat_data['mode'] = 'partner'
            context.chat_data['partner_state'] = 'name'
            await query.edit_message_text("Как к вам обращаться?")
        elif sub == 'admin':
            if update.effective_user.id == ADMIN_CHAT_ID:
                await query.edit_message_text("Администрирование:", reply_markup=build_admin_keyboard())
            else:
                await query.edit_message_text("Доступ запрещён.")
    elif action == 'calc_cat':
        cat = parts[1]
        context.chat_data['current_cat'] = cat
        if cat == 'walls':
            await query.edit_message_text("Выберите тип WPC:", reply_markup=build_wall_product_keyboard())
        elif cat == 'spc':
            await query.edit_message_text("Выберите тип SPC:", reply_markup=build_wall_product_keyboard(is_spc=True))
        elif cat == 'profiles':
            await query.edit_message_text("Выберите толщину профиля:", reply_markup=build_profile_thickness_keyboard())
        elif cat == 'slats':
            await query.edit_message_text("Выберите тип реечных панелей:", reply_markup=build_slats_type_keyboard())
        elif cat == '3d':
            await query.edit_message_text("Выберите размер 3D панели:", reply_markup=build_3d_size_keyboard())
        elif cat == 'flex':
            await query.edit_message_text("Гибкий камень в разработке.")
    elif action == 'product':
        code = parts[1]
        context.chat_data['product_code'] = code
        title = PRODUCT_CODES[code]
        if title == "SPC Панель":
            await query.edit_message_text("Выберите длину SPC:", reply_markup=build_length_keyboard(code, 0))
        else:
            await query.edit_message_text("Выберите толщину:", reply_markup=build_thickness_keyboard(code))
    elif action == 'thickness':
        code = parts[1]
        thick = int(parts[2])
        context.chat_data['thickness'] = thick
        await query.edit_message_text("Выберите длину:", reply_markup=build_length_keyboard(code, thick))
    elif action == 'length':
        code = parts[1]
        thick = int(parts[2])
        length = int(parts[3])
        cat = 'spc' if code == 'spc_panel' else 'walls'
        item = {'category': cat, 'product_code': code, 'thickness': thick, 'length': length}
        context.chat_data['calc_items'].append(item)
        await query.edit_message_text("Материал добавлен. Добавить ещё?", reply_markup=build_add_more_keyboard())
    elif action == 'profile_thick':
        thick = int(parts[1])
        context.chat_data['thickness'] = thick
        await query.edit_message_text("Выберите тип профиля:", reply_markup=build_profile_type_keyboard(thick))
    elif action == 'profile_type':
        thick = int(parts[1])
        type_name = '|'.join(parts[2:])  # If type has |
        context.chat_data['profile_type'] = type_name
        context.chat_data['phase'] = 'profile_qty'
        await query.edit_message_text("Введите количество штук профиля:")
    elif action == 'slats_type':
        slat_type = parts[1]
        item = {'category': 'slats', 'type': slat_type}
        context.chat_data['calc_items'].append(item)
        await query.edit_message_text("Материал добавлен. Добавить ещё?", reply_markup=build_add_more_keyboard())
    elif action == '3d_size':
        var = parts[1]
        item = {'category': '3d', 'var': var}
        context.chat_data['calc_items'].append(item)
        await query.edit_message_text("Материал добавлен. Добавить ещё?", reply_markup=build_add_more_keyboard())
    elif action == 'add_more':
        if parts[1] == 'yes':
            await query.edit_message_text("Выберите категорию:", reply_markup=build_calc_category_keyboard())
        else:
            context.chat_data['phase'] = 'units'
            await query.edit_message_text("В каких единицах удобнее работать?", reply_markup=build_units_keyboard())
    elif action == 'units':
        unit = parts[1]
        context.chat_data['unit'] = unit
        context.chat_data['phase'] = 'wall_width'
        await query.edit_message_text(f"Введите ширину стены ({unit}):")
    elif action == 'back':
        await query.edit_message_text("Главное меню:", reply_markup=build_main_menu_keyboard())
    elif action == 'admin':
        sub = parts[1]
        if sub == 'stats':
            stats = load_stats()
            text = f"Пользователей сегодня: {len(stats['users_today'])}\nРасчётов сегодня: {stats['calc_today']}\nВсего пользователей: {len(stats['users'])}\nВсего расчётов: {stats['calc_count']}"
            await query.edit_message_text(text)
        elif sub == 'broadcast':
            context.chat_data['phase'] = 'broadcast'
            await query.edit_message_text("Введите текст для рассылки в группу:")
    elif action == 'partner_role':
        role = parts[1]
        context.chat_data['partner_role'] = role
        await query.edit_message_text("Спасибо! Менеджер свяжется с вами.")
        # Send to admin
        partner_data = context.chat_data.get('partner_data', {})
        msg = f"Новая заявка партнёра: {partner_data}"
        await context.bot.send_message(ADMIN_CHAT_ID, msg)
    # Add more for yes/no windows/doors

# ============================
#   MESSAGE HANDLER
# ============================

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    phase = context.chat_data.get('phase')

    if phase == 'name':  # For partner, but sequential
        # Implement partner sequential as in example
        pass  # Skip for brevity, similar to example

    if phase == 'profile_qty':
        try:
            qty = int(text)
            item = {'category': 'profiles', 'thickness': context.chat_data['thickness'], 'type': context.chat_data['profile_type'], 'quantity': qty}
            context.chat_data['calc_items'].append(item)
            await update.message.reply_text("Профиль добавлен. Добавить ещё?", reply_markup=build_add_more_keyboard())
            context.chat_data['phase'] = None
        except:
            await update.message.reply_text("Непонял количество. Попробуйте заново.")
    elif phase == 'wall_width':
        width = parse_size(text, context.chat_data['unit'])
        context.chat_data['wall_width_m'] = width
        context.chat_data['phase'] = 'wall_height'
        await update.message.reply_text(f"Введите высоту стены ({context.chat_data['unit']}):")
    elif phase == 'wall_height':
        height = parse_size(text, context.chat_data['unit'])
        context.chat_data['wall_height_m'] = height
        context.chat_data['phase'] = 'windows'
        context.chat_data['windows'] = []
        context.chat_data['doors'] = []
        context.chat_data['deduct_area'] = 0.0
        await update.message.reply_text("Есть окна? (Да/Нет)", reply_markup=build_yes_no_keyboard("window|yes", "window|no"))
    # For windows/doors, use callback for yes/no, then message for size
    elif phase == 'window_size':
        sizes = re.split(r'[xX]', text)
        if len(sizes) == 2:
            try:
                w = parse_size(sizes[0].strip(), context.chat_data['unit'])
                h = parse_size(sizes[1].strip(), context.chat_data['unit'])
                area = w * h
                context.chat_data['windows'].append(area)
                context.chat_data['deduct_area'] += area
                await update.message.reply_text("Окно добавлено. Ещё окно? (Да/Нет)", reply_markup=build_yes_no_keyboard("window|yes", "window|no"))
            except:
                await update.message.reply_text("Неверный формат. Пример: 1.2 x 0.9")
        else:
            await update.message.reply_text("Неверный формат. Используйте 'ширина x высота'")
    # Similar for doors
    elif phase == 'door_size':
        # Similar logic for doors
        pass
    elif phase == 'broadcast':
        # Send to group
        await context.bot.send_message(TG_GROUP, text)
        await update.message.reply_text("Рассылка отправлена!")
        context.chat_data['phase'] = None
    else:
        # Default: use OpenAI for chat
        # Implement chat with OpenAI if needed
        await update.message.reply_text("Используйте кнопки меню для расчёта или напишите /start")

# ============================
#   PHOTO HANDLER (НОВИНКА)
# ============================

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Спасибо за фото! Это поможет мне лучше понять ваш проект. "
        "Опишите размеры комнаты или используйте кнопки меню для расчёта материалов. "
        "Если нужно, я могу отправить фото менеджеру для консультации.",
        reply_markup=build_main_menu_keyboard()
    )
    # Опционально: сохранить фото или отправить админу
    # photo = await update.message.photo[-1].get_file()
    # await context.bot.send_photo(ADMIN_CHAT_ID, photo.file_id, caption=f"Фото от {update.effective_user.first_name}")

# ============================
#   REGISTRATION
# ============================

tg_application.add_handler(CommandHandler("start", start))
tg_application.add_handler(CallbackQueryHandler(callback_handler))
tg_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
tg_application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

# ============================
#   WEBHOOK SETUP WITH DEBUG
# ============================

async def setup_webhook(application: Application, webhook_url: str):
    # Сначала удаляем старый webhook, чтобы очистить last_error
    try:
        await application.bot.delete_webhook(drop_pending_updates=True)
        logger.info("Old webhook deleted, pending updates dropped.")
    except TelegramError as e:
        logger.warning(f"Failed to delete old webhook: {e} (may not exist)")

    webhook_path = f"{webhook_url}/{TG_BOT_TOKEN}"
    await application.bot.set_webhook(url=webhook_path)
    logger.info(f"New webhook set to: {webhook_path}")

    # Check webhook info
    info = await application.bot.get_webhook_info()
    logger.info(f"Webhook info: url={info.url}, pending_updates={info.pending_update_count}, last_error={info.last_error_date}")


@app.route("/", methods=["GET"])
def health():
    return "OK", 200

@app.route(f"/{TG_BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        update_json = request.get_json()
        logger.info(f"Received update: {json.dumps(update_json, indent=2)[:200]}...")  # Log first 200 chars
        if update_json:
            update = Update.de_json(update_json, tg_application.bot)
            asyncio.run(tg_application.process_update(update))  # Wrap in asyncio.run()
            return jsonify({"ok": True})
        else:
            logger.warning("Empty update received")
            return jsonify({"ok": False}), 400
    except Exception as e:
        logger.error(f"Error processing update: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

# ============================
#   MAIN
# ============================

async def main():
    await tg_application.initialize()
    await tg_application.start()
    port = int(os.getenv("PORT", 8443))
    webhook_url = os.getenv("WEBHOOK_URL")
    if webhook_url:
        await setup_webhook(tg_application, webhook_url)
        app.run(host="0.0.0.0", port=port, debug=False)
    else:
        logger.info("No WEBHOOK_URL, starting polling")
        await tg_application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
