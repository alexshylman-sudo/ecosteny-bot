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
        return num / 1000 if unit == "mm" else num
    except:
        return 0.0

def calculate_item(item, wall_width_m, wall_height_m, deduct_area_m2, unit) -> tuple[str, int]:
    category = item['category']
    cost = 0
    if category == 'walls':
        title = PRODUCT_CODES[item['product_code']]
        thickness = item.get('thickness', 0)
        length_mm = item['length']
        panel = WALL_PRODUCTS[title][thickness]['panels'][length_mm]
        area_m2 = panel['area_m2']
        price = panel['price_rub']
        panel_width_mm = WALL_PRODUCTS[title][thickness]['width_mm']
        gross_area = wall_width_m * wall_height_m
        net_area = gross_area - deduct_area_m2
        panels = math.ceil(net_area / area_m2)
        total_area = panels * area_m2
        waste_area = total_area - net_area
        waste_pct = (waste_area / total_area) * 100 if total_area > 0 else 0
        cost = panels * price
        custom_name = item.get('custom_name', 'Стандартный')
        width_mm = wall_width_m * 1000
        width_m = wall_width_m
        result_text = f"""Выбранный материал: {title}  
Толщина: {thickness} мм  
Высота: {length_mm} мм  
Название/артикул клиента: **«{custom_name}»**  

🔹 Ширина зоны отделки: {width_mm:.1f} мм (или {width_m:.2f} м)  
🔹 Площадь зоны отделки: {width_m:.2f} м × {wall_height_m:.1f} м = {gross_area:.2f} м²  
🔹 Площадь к вычету (окна/двери): {deduct_area_m2:.2f} м²  
🔹 Общая площадь для покрытия: {gross_area:.2f} м² - {deduct_area_m2:.2f} м² = {net_area:.2f} м²  

🔸 Площадь одной панели ({length_mm} мм × {panel_width_mm} мм): {area_m2} м²  
🔸 Необходимое количество панелей: {net_area:.2f} м² ÷ {area_m2} м² ≈ {net_area / area_m2:.2f} (округляем до {panels} панелей)  
🔸 Общая площадь закупаемых панелей: {panels} панелей × {area_m2} м² = {total_area:.1f} м²  

🔹 Отходы:  
- Площадь отходов: {total_area:.1f} м² - {net_area:.2f} м² = {waste_area:.2f} м²  
- Процент отходов: ({waste_area:.2f} м² ÷ {total_area:.1f} м²) × 100 ≈ {waste_pct:.2f}%  

💰 Ориентировочная стоимость: {panels} панелей × {price:,} ₽ = {cost:,} ₽  

____________________________________________________________  
Итог:  
- Необходимое количество панелей: {panels}  
- Общая стоимость: {cost:,} ₽  
- Отходы: {waste_area:.2f} м² ({waste_pct:.2f}%)"""
    elif category == 'profiles':
        thickness = item['thickness']
        type_name = item['type']
        quantity = item['quantity']
        price = PROFILES[thickness][type_name]
        cost = quantity * price
        result_text = f"""
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
        result_text = f"""
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
        gross_area = wall_width_m * wall_height_m
        net_area = gross_area - deduct_area_m2
        panels = math.ceil(net_area / area_m2)
        total_area = panels * area_m2
        waste_area = total_area - net_area
        waste_pct = (waste_area / total_area) * 100 if total_area > 0 else 0
        cost = panels * price
        result_text = f"""
3D панели: {var['code']}
Площадь панели: {area_m2} м²
Количество: {panels} шт.
Общая площадь: {total_area} м²
Отходы: {waste_area:.2f} м² ({waste_pct:.2f}%)
💰 Стоимость: {cost} ₽
"""
    else:
        result_text = ""
    return result_text, cost

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
            context.chat_data['completed_calcs'] = []  # List of (text, cost)
            context.chat_data['phase'] = 'select_cat'
            await query.edit_message_text("Расчёт материалов:", reply_markup=build_calc_category_keyboard())
        elif sub == 'info':
            await query.edit_message_text("Информация в разработке.")
        elif sub == 'catalogs':
            await query.edit_message_text("Каталог в разработке.")
        elif sub == 'presentation':
            await context.bot.send_document(chat_id=query.message.chat_id, document=PRESENTATION_URL, caption="Презентация ECO Стены")
        elif sub == 'contacts':
            text = "Телефон: +7 (978) 022-32-22\nПочта: info@ecosteni.ru\nГрафик: Пн-Пт 9:00–18:00\n\nГруппа в Telegram: https://t.me/ecosteni\nСвязаться с администратором: @DService82\nСайт: https://ecosteni.ru/"
            await query.edit_message_text(text, reply_markup=build_contacts_keyboard())
        elif sub == 'partner':
            context.chat_data['mode'] = 'partner'
            context.chat_data['phase'] = 'partner_name'
            await query.edit_message_text("🤝 Хочу стать партнёром!\n\nКак к вам обращаться? (Введите имя)")
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
        cat = 'walls'
        item = {'category': cat, 'product_code': code, 'thickness': thick, 'length': length}
        context.chat_data['current_item'] = item
        await query.edit_message_text("Знаете точное название/артикул материала?", reply_markup=build_custom_name_keyboard())
    elif action == 'custom_name':
        item = context.chat_data['current_item']
        if parts[1] == 'yes':
            context.chat_data['phase'] = 'custom_name'
            await query.edit_message_text("Введите название/артикул:")
        else:
            # Proceed to units or wall_width
            await proceed_to_wall_input(query, context)
    elif action == 'profile_thick':
        thick = int(parts[1])
        context.chat_data['thickness'] = thick
        await query.edit_message_text("Выберите тип профиля:", reply_markup=build_profile_type_keyboard(thick))
    elif action == 'profile_type':
        thick = int(parts[1])
        type_name = parts[2].replace('_', ' ')  # Restore spaces
        context.chat_data['profile_type'] = type_name
        context.chat_data['phase'] = 'profile_qty'
        await query.edit_message_text("Введите количество штук профиля:")
    elif action == 'slats_type':
        slat_type = parts[1]
        item = {'category': 'slats', 'type': slat_type}
        context.chat_data['current_item'] = item
        # Proceed to units or wall_width
        await proceed_to_wall_input(query, context)
    elif action == '3d_size':
        var = parts[1]
        item = {'category': '3d', 'var': var}
        context.chat_data['current_item'] = item
        # Proceed to units or wall_width
        await proceed_to_wall_input(query, context)
    elif action == 'units':
        unit = parts[1]
        context.user_data['unit'] = unit
        context.chat_data['phase'] = 'wall_width'
        await query.edit_message_text(f"Введите ширину стены ({unit}):")
    elif action == 'add_another':
        if parts[1] == 'yes':
            context.chat_data['phase'] = 'select_cat'
            await query.edit_message_text("Выберите категорию для следующего материала:", reply_markup=build_calc_category_keyboard())
        else:
            # Show full summary
            completed = context.chat_data.get('completed_calcs', [])
            if completed:
                full_text = "\n\n".join([text for text, _ in completed])
                total_cost = sum(cost for _, cost in completed)
                full_text += f"\n\n🎉 Общая стоимость всех материалов: {total_cost:,} ₽"
                await query.edit_message_text(full_text, parse_mode=ParseMode.MARKDOWN)
                stats = load_stats()
                stats['calc_count'] += 1
                stats['calc_today'] += 1
                save_stats(stats)
            else:
                await query.edit_message_text("Расчёт не завершён. Добавьте хотя бы один материал.")
            # Reset
            context.chat_data['phase'] = None
            await context.bot.send_message(query.message.chat_id, "Расчёт завершён! Вернуться в меню?", reply_markup=build_main_menu_keyboard())
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
        context.chat_data['partner_role'] = {
            'retail': 'Розничный магазин',
            'installer': 'Монтажная бригада',
            'designer': 'Дизайнер/Архитектор',
            'other': 'Другое'
        }[role]
        context.chat_data['phase'] = 'partner_message'
        await query.edit_message_text("Расскажите подробнее о вашем бизнесе или вопросе:")
    # Окна/двери (на русском)
    elif action.startswith('okno') or action.startswith('dver'):
        phase_key = 'windows' if action.startswith('okno') else 'doors'
        if parts[1] == 'yes':
            context.chat_data['current_opening_type'] = phase_key  # Запоминаем тип (окно или дверь)
            context.chat_data['phase'] = 'opening_width'
            unit = context.user_data.get('unit', 'm')
            opening_single = "окна" if phase_key == 'windows' else "двери"
            await query.edit_message_text(f"Введите ширину {opening_single[:-1]} (в {unit}):")
        else:
            next_action = 'dver' if action.startswith('okno') else 'finish_calc'
            if next_action == 'finish_calc':
                # Calculate current item
                item = context.chat_data['current_item']
                width = context.chat_data['wall_width_m']
                height = context.chat_data['wall_height_m']
                deduct = context.chat_data['deduct_area']
                unit = context.user_data.get('unit', 'm')
                result_text, cost = calculate_item(item, width, height, deduct, unit)
                context.chat_data['completed_calcs'].append((result_text, cost))
                await query.edit_message_text(result_text, parse_mode=ParseMode.MARKDOWN)
                await context.bot.send_message(query.message.chat_id, "Добавить ещё материал?", reply_markup=build_add_another_keyboard())
                context.chat_data['phase'] = None
            else:
                await query.edit_message_text("Есть двери? (Да/Нет)", reply_markup=build_yes_no_keyboard("dver|yes", "dver|no"))

async def proceed_to_wall_input(query, context):
    unit = context.user_data.get('unit')
    if unit:
        context.chat_data['phase'] = 'wall_width'
        await query.edit_message_text(f"Введите ширину стены ({unit}):")
    else:
        context.chat_data['phase'] = 'units'
        await query.edit_message_text("В каких единицах удобнее работать?", reply_markup=build_units_keyboard())

# ============================
#   MESSAGE HANDLER
# ============================

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    phase = context.chat_data.get('phase')

    if phase == 'partner_name':
        context.chat_data['partner_name'] = text
        context.chat_data['phase'] = 'partner_city'
        await update.message.reply_text("В каком городе вы работаете?")
    elif phase == 'partner_city':
        context.chat_data['partner_city'] = text
        context.chat_data['phase'] = 'partner_phone'
        await update.message.reply_text("Введите ваш контактный телефон (для связи):")
    elif phase == 'partner_phone':
        context.chat_data['partner_phone'] = text
        context.chat_data['phase'] = 'partner_role'
        await update.message.reply_text("Какой у вас тип партнёрства?", reply_markup=build_partner_role_keyboard())
    elif phase == 'partner_message':
        context.chat_data['partner_message'] = text
        # Send to admin
        partner_data = {
            'name': context.chat_data.get('partner_name'),
            'city': context.chat_data.get('partner_city'),
            'phone': context.chat_data.get('partner_phone'),
            'role': context.chat_data.get('partner_role'),
            'message': text
        }
        username = update.effective_user.username
        username_str = f"@{username}" if username else "Без никнейма"
        msg = f"Новая заявка партнёра от {username_str}:\n👤 Имя: {partner_data['name']}\n🏙️ Город: {partner_data['city']}\n📱 Тел: {partner_data['phone']}\n🔹 Роль: {partner_data['role']}\n💬 Сообщение: {partner_data['message']}"
        await context.bot.send_message(ADMIN_CHAT_ID, msg)
        await update.message.reply_text("Спасибо! Менеджер свяжется с вами в ближайшее время.\n\n😊 Добро пожаловать в команду ECO Стены!", reply_markup=build_main_menu_keyboard())
        # Reset
        context.chat_data['phase'] = None
    elif phase == 'custom_name':
        item = context.chat_data['current_item']
        item['custom_name'] = text
        context.chat_data['current_item'] = item
        unit = context.user_data.get('unit')
        if unit:
            context.chat_data['phase'] = 'wall_width'
            await update.message.reply_text(f"Введите ширину стены ({unit}):")
        else:
            context.chat_data['phase'] = 'units'
            await update.message.reply_text("В каких единицах удобнее работать?", reply_markup=build_units_keyboard())
    elif phase == 'profile_qty':
        try:
            qty = int(text)
            item = {'category': 'profiles', 'thickness': context.chat_data['thickness'], 'type': context.chat_data['profile_type'], 'quantity': qty}
            width = context.chat_data.get('wall_width_m', 0)  # For profiles, assume wall width if set, else prompt? But for simplicity, proceed to calc assuming qty is total
            height = context.chat_data.get('wall_height_m', 0)
            deduct = context.chat_data.get('deduct_area', 0)
            unit = context.user_data.get('unit', 'm')
            result_text, cost = calculate_item(item, width or 1, height or 1, deduct, unit)  # Dummy if no dims
            context.chat_data['completed_calcs'].append((result_text, cost))
            await update.message.reply_text(result_text + "\n\nДобавить ещё материал?", reply_markup=build_add_another_keyboard())
            context.chat_data['phase'] = None
        except:
            await update.message.reply_text("Непонял количество. Попробуйте заново.")
    elif phase == 'wall_width':
        width = parse_size(text, context.user_data.get('unit', 'm'))
        if width <= 0:
            await update.message.reply_text("Неверное значение. Введите ширину заново:")
            return
        context.chat_data['wall_width_m'] = width
        context.chat_data['phase'] = 'wall_height'
        await update.message.reply_text(f"Введите высоту стены ({context.user_data.get('unit', 'm')}):")
    elif phase == 'wall_height':
        height = parse_size(text, context.user_data.get('unit', 'm'))
        if height <= 0:
            await update.message.reply_text("Неверное значение. Введите высоту заново:")
            return
        context.chat_data['wall_height_m'] = height
        context.chat_data['phase'] = 'okno'  # Русский: окно
        context.chat_data['windows'] = []
        context.chat_data['doors'] = []
        context.chat_data['deduct_area'] = 0.0
        await update.message.reply_text("Есть окна? (Да/Нет)", reply_markup=build_yes_no_keyboard("okno|yes", "okno|no"))
    elif phase == 'opening_width':
        w = parse_size(text, context.user_data.get('unit', 'm'))
        if w <= 0:
            await update.message.reply_text("Неверное значение. Введите ширину заново:")
            return
        context.chat_data['temp_opening_width'] = w
        context.chat_data['phase'] = 'opening_height'
        opening_single = "окна" if context.chat_data['current_opening_type'] == 'windows' else "двери"
        await update.message.reply_text(f"Введите высоту {opening_single[:-1]} (в {context.user_data.get('unit', 'm')}):")
    elif phase == 'opening_height':
        h = parse_size(text, context.user_data.get('unit', 'm'))
        if h <= 0:
            await update.message.reply_text("Неверное значение. Введите высоту заново:")
            return
        area = context.chat_data['temp_opening_width'] * h
        phase_key = context.chat_data['current_opening_type']
        context.chat_data[phase_key].append(area)
        context.chat_data['deduct_area'] += area
        if phase_key == 'windows':
            added_text = "Окно добавлено"
            more_text = "окно"
            yes_data = "okno|yes"
            no_data = "okno|no"
        else:
            added_text = "Дверь добавлена"
            more_text = "дверь"
            yes_data = "dver|yes"
            no_data = "dver|no"
        await update.message.reply_text(f"{added_text}. Ещё {more_text}? (Да/Нет)", reply_markup=build_yes_no_keyboard(yes_data, no_data))
        context.chat_data['phase'] = None  # Reset temp
    elif phase == 'broadcast':
        # Send to group
        await context.bot.send_message(TG_GROUP, text)
        await update.message.reply_text("Рассылка отправлена!")
        context.chat_data['phase'] = None
    else:
        # Default
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

# Initialize application once at startup (sync)
asyncio.run(tg_application.initialize())

tg_application.add_handler(CommandHandler("start", start))
tg_application.add_handler(CallbackQueryHandler(callback_handler))
tg_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
tg_application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

# ============================
#   WEBHOOK SETUP WITH DEBUG
# ============================

async def setup_webhook(application: Application, webhook_url: str):
    # Ждём стабилизации loop (фикс для RuntimeError)
    await asyncio.sleep(0.1)
    
    # Сначала удаляем старый webhook, чтобы очистить last_error
    try:
        await application.bot.delete_webhook(drop_pending_updates=True)
        logger.info("Old webhook deleted, pending updates dropped.")
    except (TelegramError, RuntimeError) as e:
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

@app.route(f"/{TG_BOT_TOKEN}", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        # Игнорируем GET (health check или probe) — просто OK
        return jsonify({"ok": True, "method": "GET"}), 200
    
    if request.method == "POST":
        try:
            update_json = request.get_json()
            logger.info(f"Received update: {json.dumps(update_json, indent=2)[:200]}...")
            if update_json:
                update = Update.de_json(update_json, tg_application.bot)
                loop = get_event_loop()
                loop.run_until_complete(tg_application.process_update(update))
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

def main():
    port = int(os.getenv("PORT", 8443))
    webhook_url = os.getenv("WEBHOOK_URL")
    if webhook_url:
        # Setup webhook in async context
        loop = get_event_loop()
        loop.run_until_complete(setup_webhook(tg_application, webhook_url))
        logger.info("Starting Flask server with webhook mode")
        app.run(host="0.0.0.0", port=port, debug=False)
    else:
        logger.info("No WEBHOOK_URL, starting polling")
        asyncio.run(tg_application.run_polling())

if __name__ == "__main__":
    main()
