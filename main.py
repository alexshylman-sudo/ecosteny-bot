# main.py
# ECO Стены — стабильный webhook сервер для Telegram bot
# Архитектура: Flask для webhook + background asyncio loop для python-telegram-bot Application
# Перед деплоем: установить TG_BOT_TOKEN в переменных окружения.
# Опционально: WEBHOOK_URL (https://your-app.onrender.com) чтобы автоматически зарегистрировать webhook.
# Запуск локально (dev): python main.py (будет работать в webhook-mode, если WEBHOOK_URL задан, иначе polling режим)

import os
import json
import math
import re
import time
import random
import logging
import threading
import traceback
from io import BytesIO
from datetime import datetime, timezone
from concurrent.futures import Future

from flask import Flask, request, jsonify

import asyncio
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.error import TelegramError

# -----------------------
# Logging
# -----------------------
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger("ecosteny_bot")

# -----------------------
# Config
# -----------------------
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
if not TG_BOT_TOKEN:
    raise RuntimeError("Установите переменную окружения TG_BOT_TOKEN")

WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # example: https://ecosteny-bot.onrender.com
PORT = int(os.getenv("PORT", "8443"))
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "203473623"))  # используем указанный ранее ID
WELCOME_PHOTO_URL = os.getenv("WELCOME_PHOTO_URL", "https://ecosteni.ru/wp-content/uploads/2025/11/qncccaze.jpg")
PRESENTATION_URL = os.getenv("PRESENTATION_URL", "https://ecosteni.ru/wp-content/uploads/2025/11/ecosteny_prezentacziya.pdf")
TG_GROUP = os.getenv("TG_GROUP", "@ecosteni")

# -----------------------
# Simple stats storage (ephemeral on Render) - file in /tmp
# -----------------------
STATS_FILE = "/tmp/eco_stats.json"

def load_stats():
    default = {
        "users": [],
        "calc_count": 0,
        "today": datetime.now(timezone.utc).date().isoformat(),
        "users_today": [],
        "calc_today": 0,
    }
    try:
        if os.path.exists(STATS_FILE):
            with open(STATS_FILE, "r") as f:
                data = json.load(f)
                return data
    except Exception as e:
        logger.warning("Could not load stats: %s", e)
    return default

def save_stats(stats):
    try:
        with open(STATS_FILE, "w") as f:
            json.dump(stats, f)
    except Exception as e:
        logger.error("Failed to save stats: %s", e)

# -----------------------
# Catalogs / Prices (из твоего файла)
# -----------------------
WALL_PRODUCTS = {
    "WPC Бамбук угольный": {
        5: {"width_mm": 1220, "panels": {2440: {"area_m2": 2.928, "price_rub": 10500}, 2600: {"area_m2": 3.12, "price_rub": 11100}}},
        8: {"width_mm": 1220, "panels": {2440: {"area_m2": 2.928, "price_rub": 12200}, 2600: {"area_m2": 3.12, "price_rub": 13000}}},
    },
    "WPC Бамбук": {
        5: {"width_mm": 1220, "panels": {2440: {"area_m2": 2.928, "price_rub": 12200}, 2600: {"area_m2": 3.12, "price_rub": 13000}}},
        8: {"width_mm": 1220, "panels": {2440: {"area_m2": 2.928, "price_rub": 13900}, 2600: {"area_m2": 3.12, "price_rub": 14900}}},
    },
    "WPC повышенной плотности": {
        8: {"width_mm": 1220, "panels": {2440: {"area_m2": 2.928, "price_rub": 15500}, 2600: {"area_m2": 3.12, "price_rub": 16500}}},
    },
    "SPC Панель": {
        0: {"width_mm": 1220, "panels": {2440: {"area_m2": 2.928, "price_rub": 9500}, 2600: {"area_m2": 3.12, "price_rub": 10100}}},
    },
}

PRODUCT_CODES = {
    "wpc_charcoal": "WPC Бамбук угольный",
    "wpc_bamboo": "WPC Бамбук",
    "wpc_hd": "WPC повышенной плотности",
    "spc_panel": "SPC Панель",
}

PROFILES = {
    5: {"Стыковочный": 1350, "Финишный": 1350, "Внешний угол": 1450},
    8: {"Стыковочный": 1450, "Финишный": 1450, "Внешний угол": 1550},
}

SLAT_PRICES = {"wpc": 1200, "wood": 1500}

PANELS_3D = {
    "var1": {"code": "3d_600x1200", "area_m2": 0.72, "price_rub": 3000},
    "var2": {"code": "3d_1200x3000", "area_m2": 3.6, "price_rub": 8000},
}

GREETING_PHRASES = [
    "Привет, {name}! Я ассистент компании ECO Стены. Помогу с подбором материалов и расчётом панелей. 😊",
    "Рад знакомству, {name}! Я здесь, чтобы помочь вам с продукцией ECO Стены и ответить на вопросы.",
    "Здравствуйте, {name}! Если планируете ремонт или обновление интерьера — давайте подберём материалы вместе.",
]

# -----------------------
# Flask app
# -----------------------
app = Flask(__name__)

# -----------------------
# Globals for Application and its event loop/thread
# -----------------------
tg_application: Application = None
tg_loop: asyncio.AbstractEventLoop = None
_bot_thread: threading.Thread = None
_start_lock = threading.Lock()
_started_event = threading.Event()

# -----------------------
# Keyboards (copied/adapted)
# -----------------------
def build_main_menu_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("Рассчитать материалы", callback_data="main|calc")],
        [InlineKeyboardButton("Информация", callback_data="main|info")],
        [InlineKeyboardButton("Получить каталоги", callback_data="main|catalogs")],
        [InlineKeyboardButton("Получить презентацию", callback_data="main|presentation")],
        [InlineKeyboardButton("Контактная информация", callback_data="main|contacts")],
        [InlineKeyboardButton("Хочу стать партнёром", callback_data="main|partner")],
    ]
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
    title = PRODUCT_CODES.get(code, "Unknown")
    thicknesses = WALL_PRODUCTS.get(title, {}).keys()
    buttons = [[InlineKeyboardButton(f"{thick} мм", callback_data=f"thickness|{code}|{thick}")] for thick in thicknesses]
    buttons += build_back_button("Назад")
    return InlineKeyboardMarkup(buttons)

def build_length_keyboard(code: str, thick: int) -> InlineKeyboardMarkup:
    title = PRODUCT_CODES.get(code, "Unknown")
    lengths = WALL_PRODUCTS.get(title, {}).get(thick, {}).get('panels', {}).keys()
    buttons = [[InlineKeyboardButton(f"{length} мм", callback_data=f"length|{code}|{thick}|{length}")] for length in lengths]
    buttons += build_back_button("Назад")
    return InlineKeyboardMarkup(buttons)

def build_profile_thickness_keyboard() -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton("5 мм", callback_data="profile_thick|5")], [InlineKeyboardButton("8 мм", callback_data="profile_thick|8")]]
    buttons += build_back_button("Назад")
    return InlineKeyboardMarkup(buttons)

def build_profile_type_keyboard(thick: int) -> InlineKeyboardMarkup:
    types = PROFILES.get(thick, {}).keys()
    buttons = [[InlineKeyboardButton(name, callback_data=f"profile_type|{thick}|{name.replace(' ', '_')}")] for name in types]
    buttons += build_back_button("Назад")
    return InlineKeyboardMarkup(buttons)

def build_slats_type_keyboard() -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton("WPC рейки", callback_data="slats_type|wpc")], [InlineKeyboardButton("Деревянные рейки", callback_data="slats_type|wood")]]
    buttons += build_back_button("Назад")
    return InlineKeyboardMarkup(buttons)

def build_3d_size_keyboard() -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton("600x1200 мм", callback_data="3d_size|var1")], [InlineKeyboardButton("1200x3000 мм", callback_data="3d_size|var2")]]
    buttons += build_back_button("Назад")
    return InlineKeyboardMarkup(buttons)

def build_add_another_keyboard() -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton("Да, добавить ещё материал", callback_data="add_another|yes")], [InlineKeyboardButton("Расчёт окончен", callback_data="add_another|no")]]
    return InlineKeyboardMarkup(buttons)

def build_custom_name_keyboard() -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton("Да, знаю название/артикул", callback_data="custom_name|yes")], [InlineKeyboardButton("Нет, стандартный", callback_data="custom_name|no")]]
    return InlineKeyboardMarkup(buttons)

def build_units_keyboard() -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton("Метры (м)", callback_data="units|m")], [InlineKeyboardButton("Миллиметры (мм)", callback_data="units|mm")]]
    return InlineKeyboardMarkup(buttons)

def build_yes_no_keyboard(yes_data, no_data) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton("Да", callback_data=yes_data)], [InlineKeyboardButton("Нет", callback_data=no_data)]]
    return InlineKeyboardMarkup(buttons)

def build_contacts_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("Группа в Telegram", url="https://t.me/ecosteni")],
        [InlineKeyboardButton("Связаться с администратором", url="https://t.me/DService82")],
        [InlineKeyboardButton("Сайт", url="https://ecosteni.ru/")],
        [InlineKeyboardButton("Позвонить", url="tel:+79780223222")],
    ]
    buttons += build_back_button("Назад")
    return InlineKeyboardMarkup(buttons)

def build_admin_keyboard() -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton("Сатистика", callback_data="admin|stats")], [InlineKeyboardButton("Рассылка", callback_data="admin|broadcast")]]
    buttons += build_back_button("Назад")
    return InlineKeyboardMarkup(buttons)

def build_partner_role_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("Розничный магазин", callback_data="partner_role|retail")],
        [InlineKeyboardButton("Монтажная бригада", callback_data="partner_role|installer")],
        [InlineKeyboardButton("Дизайнер/Архитектор", callback_data="partner_role|designer")],
        [InlineKeyboardButton("Другое", callback_data="partner_role|other")],
    ]
    return InlineKeyboardMarkup(buttons)

# -----------------------
# Utility / Calc functions (сохранены из твоего файла)
# -----------------------
def parse_size(text: str, unit: str) -> float:
    try:
        num = float(text.strip())
        return num / 1000.0 if unit == "mm" else num
    except:
        return 0.0

def calculate_item(item, wall_width_m, wall_height_m, deduct_area_m2, unit) -> tuple:
    category = item.get('category')
    cost = 0
    result_text = ""
    if category in ['walls', 'spc']:
        title = PRODUCT_CODES.get(item['product_code'])
        thickness = item.get('thickness', 0)
        length_mm = item['length']
        panel = WALL_PRODUCTS[title][thickness]['panels'][length_mm]
        area_m2 = panel['area_m2']
        price = panel['price_rub']
        net_area = wall_width_m * wall_height_m - deduct_area_m2
        required_area = net_area * 1.1
        panels = math.ceil(required_area / area_m2)
        total_area = panels * area_m2
        waste_area = total_area - net_area
        waste_pct = (waste_area / total_area) * 100 if total_area > 0 else 0
        cost = panels * price
        result_text = f"Выбранный материал: {title}\nТолщина: {thickness} мм\nВысота: {length_mm} мм\nПлощадь зоны: {wall_width_m * wall_height_m:.2f} м²\nЧистая площадь: {net_area:.2f} м²\nПанелей: {panels} шт.\nОтходы: {waste_area:.2f} м² ({waste_pct:.2f}%)\nСтоимость: {cost} ₽"
    elif category == 'profiles':
        thickness = item['thickness']
        type_name = item['type']
        quantity = item['quantity']
        price = PROFILES[thickness][type_name]
        cost = quantity * price
        result_text = f"Профиль: {type_name}, {thickness} мм\nКоличество: {quantity} шт.\nСтоимость: {cost} ₽"
    elif category == 'slats':
        type_name = 'WPC' if item['type'] == 'wpc' else 'Деревянные'
        price_mp = SLAT_PRICES[item['type']]
        length_m = wall_width_m
        required = length_m * 1.1
        cost = math.ceil(required) * price_mp
        waste = required - length_m
        result_text = f"Реечные панели: {type_name}\nДлина стены: {length_m} м.п.\nНеобходимая длина: {required:.2f} м.п.\nОтходы: {waste:.2f} м.п.\nСтоимость: {cost} ₽"
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
        result_text = f"3D панели: {var['code']}\nПлощадь панели: {area_m2} м²\nКоличество: {panels} шт.\nОтходы: {waste_area:.2f} м² ({waste_pct:.2f}%)\nСтоимость: {cost} ₽"
    return result_text, cost

# -----------------------
# Handlers (async) — сохранены и немного упрощены для устойчивости
# -----------------------
async def send_greeting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.first_name or user.username or "друг"
    greeting = random.choice(GREETING_PHRASES).format(name=name)
    try:
        await context.bot.send_photo(chat_id=update.effective_chat.id, photo=WELCOME_PHOTO_URL, caption=greeting)
    except Exception as e:
        logger.warning("send photo failed: %s", e)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=greeting)
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Чем могу помочь?", reply_markup=build_main_menu_keyboard())

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        stats = load_stats()
        today = datetime.now(timezone.utc).date().isoformat()
        if stats.get('today') != today:
            stats['today'] = today
            stats['users_today'] = []
            stats['calc_today'] = 0
        uid = str(update.effective_chat.id)
        if uid not in stats.get('users', []):
            stats.setdefault('users', []).append(uid)
        if uid not in stats.get('users_today', []):
            stats.setdefault('users_today', []).append(uid)
        save_stats(stats)
        await send_greeting(update, context)
    except Exception:
        logger.error("start_cmd error:\n%s", traceback.format_exc())

async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.effective_message.text or ""
        await update.effective_message.reply_text("Используйте меню для расчёта или отправьте /start")
    except Exception:
        logger.error("text handler error:\n%s", traceback.format_exc())

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        data = query.data or ""
        parts = data.split("|")
        action = parts[0]
        if action == "main":
            sub = parts[1] if len(parts) > 1 else ""
            if sub == "calc":
                context.chat_data['mode'] = 'calc'
                context.chat_data['completed_calcs'] = []
                context.chat_data['phase'] = 'select_cat'
                await query.edit_message_text("Расчёт материалов:", reply_markup=build_calc_category_keyboard())
            elif sub == "info":
                await query.edit_message_text("Информация в разработке.")
            elif sub == "catalogs":
                await query.edit_message_text("Каталоги скоро будут.")
            elif sub == "presentation":
                await context.bot.send_document(chat_id=query.message.chat_id, document=PRESENTATION_URL, caption="Презентация ECO Стены")
            elif sub == "contacts":
                await query.edit_message_text("Контакты:", reply_markup=build_contacts_keyboard())
            elif sub == "partner":
                context.chat_data['mode'] = 'partner'
                context.chat_data['partner_state'] = 'name'
                await query.edit_message_text("Как к вам обращаться? (Введите имя)")
        elif action == "calc_cat":
            cat = parts[1]
            context.chat_data['current_cat'] = cat
            if cat == "walls":
                await query.edit_message_text("Выберите тип WPC:", reply_markup=build_wall_product_keyboard())
            elif cat == "spc":
                await query.edit_message_text("Выберите тип SPC:", reply_markup=build_wall_product_keyboard(is_spc=True))
            elif cat == "profiles":
                await query.edit_message_text("Выберите толщину профиля:", reply_markup=build_profile_thickness_keyboard())
            elif cat == "slats":
                await query.edit_message_text("Выберите тип реечных панелей:", reply_markup=build_slats_type_keyboard())
            elif cat == "3d":
                await query.edit_message_text("Выберите размер 3D панели:", reply_markup=build_3d_size_keyboard())
        elif action == "product":
            code = parts[1]
            context.chat_data['product_code'] = code
            title = PRODUCT_CODES.get(code, "Неизвестно")
            if title == "SPC Панель":
                await query.edit_message_text("Выберите длину SPC:", reply_markup=build_length_keyboard(code, 0))
            else:
                await query.edit_message_text("Выберите толщину:", reply_markup=build_thickness_keyboard(code))
        elif action == "thickness":
            code = parts[1]; thick = int(parts[2])
            context.chat_data['thickness'] = thick
            await query.edit_message_text("Выберите длину:", reply_markup=build_length_keyboard(code, thick))
        elif action == "length":
            code = parts[1]; thick = int(parts[2]); length = int(parts[3])
            cat = 'spc' if code == 'spc_panel' else 'walls'
            item = {'category': cat, 'product_code': code, 'thickness': thick, 'length': length}
            context.chat_data['current_item'] = item
            await query.edit_message_text("Знаете точное название/артикул материала?", reply_markup=build_custom_name_keyboard())
        elif action == "custom_name":
            if parts[1] == 'yes':
                context.chat_data['phase'] = 'custom_name'
                await query.edit_message_text("Введите название/артикул:")
            else:
                context.chat_data['phase'] = 'units'
                await query.edit_message_text("В каких единицах удобнее работать?", reply_markup=build_units_keyboard())
        elif action == "profile_thick":
            thick = int(parts[1])
            context.chat_data['thickness'] = thick
            await query.edit_message_text("Выберите тип профиля:", reply_markup=build_profile_type_keyboard(thick))
        elif action == "profile_type":
            thick = int(parts[1]); type_name = parts[2].replace("_", " ")
            context.chat_data['profile_type'] = type_name
            context.chat_data['phase'] = 'profile_qty'
            await query.edit_message_text("Введите количество штук профиля:")
        elif action == "slats_type":
            slat_type = parts[1]
            item = {'category': 'slats', 'type': slat_type}
            context.chat_data['current_item'] = item
            context.chat_data['phase'] = 'units'
            await query.edit_message_text("В каких единицах удобнее работать?", reply_markup=build_units_keyboard())
        elif action == "3d_size":
            var = parts[1]
            item = {'category': '3d', 'var': var}
            context.chat_data['current_item'] = item
            context.chat_data['phase'] = 'units'
            await query.edit_message_text("В каких единицах удобнее работать?", reply_markup=build_units_keyboard())
        elif action == "units":
            unit = parts[1]
            context.chat_data['unit'] = unit
            context.chat_data['phase'] = 'wall_width'
            await query.edit_message_text(f"Введите ширину стены ({unit}):")
        elif action == "add_another":
            if parts[1] == 'yes':
                context.chat_data['phase'] = 'select_cat'
                await query.edit_message_text("Выберите категорию для следующего материала:", reply_markup=build_calc_category_keyboard())
            else:
                completed = context.chat_data.get('completed_calcs', [])
                if completed:
                    full_text = "\n\n".join([text for text, _ in completed])
                    total_cost = sum(cost for _, cost in completed)
                    full_text += f"\n\n🎉 Общая стоимость всех материалов: {total_cost} ₽"
                    await query.edit_message_text(full_text)
                    stats = load_stats()
                    stats['calc_count'] = stats.get('calc_count', 0) + 1
                    stats['calc_today'] = stats.get('calc_today', 0) + 1
                    save_stats(stats)
                else:
                    await query.edit_message_text("Расчёт не завершён. Добавьте хотя бы один материал.")
                context.chat_data['phase'] = None
                await context.bot.send_message(query.message.chat_id, "Расчёт завершён! Вернуться в меню?", reply_markup=build_main_menu_keyboard())
        elif action == "back":
            await query.edit_message_text("Главное меню:", reply_markup=build_main_menu_keyboard())
        elif action == "admin":
            sub = parts[1]
            if sub == "stats":
                stats = load_stats()
                text = f"Пользователей сегодня: {len(stats.get('users_today', []))}\nРасчётов сегодня: {stats.get('calc_today', 0)}\nВсего пользователей: {len(stats.get('users', []))}\nВсего расчётов: {stats.get('calc_count', 0)}"
                await query.edit_message_text(text)
            elif sub == "broadcast":
                context.chat_data['phase'] = 'broadcast'
                await query.edit_message_text("Введите текст для рассылки в группу:")
        elif action == "partner_role":
            role = parts[1]
            context.chat_data['partner_role'] = role
            context.chat_data['partner_state'] = 'message'
            await query.edit_message_text("Расскажите подробнее о вашем бизнесе или вопросе:")
        else:
            await query.answer()
    except Exception:
        logger.error("callback_handler error:\n%s", traceback.format_exc())

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text or ""
        phase = context.chat_data.get('phase')
        if phase == 'custom_name':
            item = context.chat_data.get('current_item', {})
            item['custom_name'] = text
            context.chat_data['current_item'] = item
            context.chat_data['phase'] = 'units'
            await update.message.reply_text("В каких единицах удобнее работать?", reply_markup=build_units_keyboard())
        elif phase == 'profile_qty':
            try:
                qty = int(text)
                item = {'category': 'profiles', 'thickness': context.chat_data['thickness'], 'type': context.chat_data['profile_type'], 'quantity': qty}
                width = context.chat_data.get('wall_width_m', 1)
                height = context.chat_data.get('wall_height_m', 1)
                deduct = context.chat_data.get('deduct_area', 0)
                unit = context.chat_data.get('unit', 'm')
                result_text, cost = calculate_item(item, width or 1, height or 1, deduct, unit)
                context.chat_data.setdefault('completed_calcs', []).append((result_text, cost))
                await update.message.reply_text(result_text + "\n\nДобавить ещё материал?", reply_markup=build_add_another_keyboard())
                context.chat_data['phase'] = None
            except:
                await update.message.reply_text("Непонял количество. Попробуйте заново.")
        elif phase == 'wall_width':
            width = parse_size(text, context.chat_data.get('unit', 'm'))
            if width <= 0:
                await update.message.reply_text("Неверное значение. Введите ширину заново:")
                return
            context.chat_data['wall_width_m'] = width
            context.chat_data['phase'] = 'wall_height'
            await update.message.reply_text(f"Введите высоту стены ({context.chat_data.get('unit', 'm')}):")
        elif phase == 'wall_height':
            height = parse_size(text, context.chat_data.get('unit', 'm'))
            if height <= 0:
                await update.message.reply_text("Неверное значение. Введите высоту заново:")
                return
            context.chat_data['wall_height_m'] = height
            context.chat_data['phase'] = 'windows'
            context.chat_data['windows'] = []
            context.chat_data['doors'] = []
            context.chat_data['deduct_area'] = 0.0
            await update.message.reply_text("Есть окна? (Да/Нет)", reply_markup=build_yes_no_keyboard("window|yes", "window|no"))
        elif phase == 'window_size':
            sizes = re.split(r'[xX]', text)
            if len(sizes) == 2:
                try:
                    w = parse_size(sizes[0].strip(), context.chat_data.get('unit', 'm'))
                    h = parse_size(sizes[1].strip(), context.chat_data.get('unit', 'm'))
                    area = w * h
                    context.chat_data.setdefault('windows', []).append(area)
                    context.chat_data['deduct_area'] = context.chat_data.get('deduct_area', 0) + area
                    await update.message.reply_text("Окно добавлено. Ещё окно? (Да/Нет)", reply_markup=build_yes_no_keyboard("window|yes", "window|no"))
                except:
                    await update.message.reply_text("Неверный формат. Пример: 1.2 x 0.9")
            else:
                await update.message.reply_text("Неверный формат. Используйте 'ширина x высота'")
        elif phase == 'door_size':
            sizes = re.split(r'[xX]', text)
            if len(sizes) == 2:
                try:
                    w = parse_size(sizes[0].strip(), context.chat_data.get('unit', 'm'))
                    h = parse_size(sizes[1].strip(), context.chat_data.get('unit', 'm'))
                    area = w * h
                    context.chat_data.setdefault('doors', []).append(area)
                    context.chat_data['deduct_area'] = context.chat_data.get('deduct_area', 0) + area
                    await update.message.reply_text("Дверь добавлена. Ещё дверь? (Да/Нет)", reply_markup=build_yes_no_keyboard("door|yes", "door|no"))
                except:
                    await update.message.reply_text("Неверный формат. Пример: 1.2 x 0.9")
            else:
                await update.message.reply_text("Неверный формат. Используйте 'ширина x высота'")
        elif phase == 'broadcast':
            # Отправляем в группу
            await context.bot.send_message(TG_GROUP, text)
            await update.message.reply_text("Рассылка отправлена!")
            context.chat_data['phase'] = None
        else:
            await update.message.reply_text("Используйте кнопки меню для расчёта или напишите /start")
    except Exception:
        logger.error("message_handler error:\n%s", traceback.format_exc())

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text("Спасибо за фото! Опишите размеры или воспользуйтесь меню.", reply_markup=build_main_menu_keyboard())
    except Exception:
        logger.error("photo handler error:\n%s", traceback.format_exc())

# -----------------------
# Background Application initialization and loop
# -----------------------
def _bg_thread_run():
    """Background thread: create loop, initialize and start application, then run_forever."""
    global tg_loop, tg_application
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        tg_loop = loop

        async def _init_and_start():
            try:
                logger.info("Initializing tg_application in background loop...")
                await tg_application.initialize()
                logger.info("tg_application.initialize done")
                # Optionally set webhook if WEBHOOK_URL exists
                if WEBHOOK_URL:
                    try:
                        webhook_path = f"{WEBHOOK_URL}/{TG_BOT_TOKEN}"
                        await tg_application.bot.delete_webhook(drop_pending_updates=True)
                        await tg_application.bot.set_webhook(url=webhook_path)
                        logger.info("Webhook set to %s", webhook_path)
                    except Exception as e:
                        logger.warning("Failed to set webhook in background: %s", e)
                await tg_application.start()
                logger.info("tg_application.start done")
            except Exception:
                logger.error("Exception during init/start in bg loop:\n%s", traceback.format_exc())
                raise

        loop.run_until_complete(_init_and_start())
        _started_event.set()
        loop.run_forever()
    except Exception:
        logger.error("Background thread crashed:\n%s", traceback.format_exc())
        _started_event.set()

def start_bot_background():
    """Create Application (if not created), register handlers and start background thread."""
    global tg_application, _bot_thread
    with _start_lock:
        if tg_application is None:
            logger.info("Building tg_application...")
            tg_application = Application.builder().token(TG_BOT_TOKEN).build()
            # Register handlers
            tg_application.add_handler(CommandHandler("start", start_cmd))
            tg_application.add_handler(CallbackQueryHandler(callback_handler))
            tg_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
            tg_application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
            logger.info("Handlers registered")
        if _bot_thread and _bot_thread.is_alive():
            return
        _bot_thread = threading.Thread(target=_bg_thread_run, name="tg-bot-thread", daemon=True)
        _bot_thread.start()
        started = _started_event.wait(timeout=10.0)
        if not started:
            logger.warning("Background bot thread did not signal start within timeout.")

def submit_update_to_app(update_obj: Update) -> Future:
    """Submit Update to application's loop and return concurrent.futures.Future"""
    global tg_loop, tg_application
    if tg_application is None or tg_loop is None:
        raise RuntimeError("Bot not ready")
    coro = tg_application.process_update(update_obj)
    fut = asyncio.run_coroutine_threadsafe(coro, tg_loop)
    return fut

# -----------------------
# Flask webhook endpoints
# -----------------------
@app.route("/", methods=["GET"])
def root():
    return "OK", 200

# Webhook endpoint — Telegram will post updates here.
@app.route(f"/{TG_BOT_TOKEN}", methods=["POST", "GET"])
def webhook():
    # GET used for simple probe
    if request.method == "GET":
        return jsonify({"ok": True, "method": "GET"}), 200
    # POST — actual update
    try:
        # Ensure bot background started
        start_bot_background()

        try:
            update_json = request.get_json(force=True)
        except Exception as e:
            logger.error("Failed to parse JSON from webhook: %s", e)
            return jsonify({"ok": True, "note": "invalid_json"}), 200

        if not update_json:
            logger.warning("Empty update_json")
            return jsonify({"ok": True, "note": "empty"}), 200

        # Ensure tg_application.bot available; wait a bit if necessary
        wait_start = time.time()
        while (tg_application is None or getattr(tg_application, "bot", None) is None) and time.time() - wait_start < 5.0:
            time.sleep(0.05)

        try:
            update = Update.de_json(update_json, tg_application.bot if tg_application else None)
        except Exception as e:
            logger.error("Failed to build Update: %s\nJSON: %s", e, update_json)
            return jsonify({"ok": True, "note": "invalid_update"}), 200

        try:
            fut = submit_update_to_app(update)
            # don't block long; small wait to get early errors
            try:
                fut.result(timeout=2.0)
            except Exception as e:
                # Usually process_update takes longer; ignore timeout
                logger.debug("process_update result/timeout: %s", e)
        except Exception:
            logger.error("Failed to submit update to app:\n%s", traceback.format_exc())
            return jsonify({"ok": True, "note": "submit_failed"}), 200

        return jsonify({"ok": True}), 200
    except Exception:
        logger.error("Unhandled error in webhook:\n%s", traceback.format_exc())
        # Return 200 to avoid Telegram marking webhook as broken; details in logs
        return jsonify({"ok": True, "note": "internal_error"}), 200

@app.route("/health", methods=["GET"])
def health():
    ready = tg_application is not None and getattr(tg_application, "bot", None) is not None
    return jsonify({"status": "ok", "bot_ready": ready}), 200

# -----------------------
# Main: if WEBHOOK_URL provided, we'll set webhook from background loop.
# If not provided, fallback to polling (not recommended on Render)
# -----------------------
def main():
    # Start background bot thread (it will also set webhook if WEBHOOK_URL present)
    start_bot_background()

    if WEBHOOK_URL:
        logger.info("Running in webhook mode. WEBHOOK_URL=%s", WEBHOOK_URL)
        # Run Flask server; webhook already set by background thread's init if possible
        app.run(host="0.0.0.0", port=PORT)
    else:
        # No WEBHOOK_URL — fallback to polling (for local dev)
        logger.info("No WEBHOOK_URL set — running polling (local dev).")
        # Wait until background start attempted
        _started_event.wait(timeout=5.0)
        try:
            # run polling in main thread event loop
            asyncio.run(tg_application.run_polling())
        except Exception:
            logger.error("Polling failed:\n%s", traceback.format_exc())

if __name__ == "__main__":
    main()
