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
#   КАТАЛОГ МАТЕРИАЛОВ (с добавленным весом)
# ============================

WALL_PRODUCTS = {
    "WPC Бамбук угольный": {
        5: {
            "width_mm": 1220,
            "weight_per_m2": 4,  # Добавлено: кг/м²
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
            "weight_per_m2": 5,  # Добавлено: кг/м²
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
            "weight_per_m2": 4,  # Добавлено
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
            "weight_per_m2": 5,  # Добавлено
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
            "weight_per_m2": 5.6,  # Добавлено
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
            "weight_per_m2": 5,  # Добавлено (предположительно, как для WPC Бамбук 8мм; уточните если нужно)
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
            "weight_per_m2": 8,  # Добавлено
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

У тебя есть каталог стеновых WPC панелей с размерами, площадью покрытия, ценой и весом за 1 м².
Каталог передаётся тебе в виде JSON в сообщении. Используй ТОЛЬКО его для расчётов по стеновым панелям.

ВАЖНО:
— Никогда не проси у пользователя каталог, JSON, прайс или цены.
— Если JSON каталога отсутствует, честно скажи, что точный расчёт доступен только при наличии каталога (который подгружается система),
  и предложи связаться с менеджером.
— Если клиент выбрал через кнопки конкретную панель, толщину и высоту — ОБЯЗАН использовать именно эту комбинацию.
— В расчёте учитывай вес: общий вес = площадь * weight_per_m2.

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
        [InlineKeyboardButton("Расчитать материалы", callback_data="main|calc")],
        [InlineKeyboardButton("Информация", callback_data="main|info")],
        [InlineKeyboardButton("Получить каталоги", callback_data="main|catalogs")],
        [InlineKeyboardButton("Получить презентацию", callback_data="main|presentation")],
        [InlineKeyboardButton("Контактная информация", callback_data="main|contacts")],
        [InlineKeyboardButton("Хочу стать партнёром", callback_data="main|partner")],
    ]
    if ADMIN_CHAT_ID:
        buttons.append([InlineKeyboardButton("Администрирование", callback_data="main|admin")])
    return InlineKeyboardMarkup(buttons)

def build_back_button(text="Назад"):
    return [[InlineKeyboardButton(text, callback_data="back|main")]]

def build_calc_category_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("Стеновые WPC панели", callback_data="calc|wall")],
        [InlineKeyboardButton("Реечные панели WPC", callback_data="calc|slat_wpc")],
        [InlineKeyboardButton("Реечные панели дерево", callback_data="calc|slat_wood")],
        [InlineKeyboardButton("3D панели", callback_data="calc|3d")],
        [InlineKeyboardButton("Профили", callback_data="calc|profile")],
    ]
    rows.append(build_back_button()[0])
    return InlineKeyboardMarkup(rows)

def build_wall_type_keyboard() -> InlineKeyboardMarkup:
    types = list(WALL_PRODUCTS.keys())
    buttons = [[InlineKeyboardButton(type_name, callback_data=f"calc_type|{type_name}")] for type_name in types]
    buttons.append(build_back_button()[0])
    return InlineKeyboardMarkup(buttons)

def build_thickness_keyboard(thicknesses: list) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(f"{t} мм", callback_data=f"thickness|{t}")] for t in thicknesses]
    buttons.append(build_back_button()[0])
    return InlineKeyboardMarkup(buttons)

def build_method_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("По размерам помещения", callback_data="calc_method|room")],
        [InlineKeyboardButton("По количеству панелей", callback_data="calc_method|panels")],
    ]
    buttons.append(build_back_button()[0])
    return InlineKeyboardMarkup(buttons)

def build_length_keyboard(panels: dict) -> InlineKeyboardMarkup:
    lengths = list(panels.keys())
    buttons = [[InlineKeyboardButton(f"{l} мм", callback_data=f"calc_length|{l}")] for l in lengths]
    buttons.append(build_back_button()[0])
    return InlineKeyboardMarkup(buttons)

# ============================
#   ОБРАБОТЧИКИ
# ============================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    first_name = user.first_name or user.username or "друг"
    greeting = random.choice(GREETING_PHRASES).format(name=first_name)
    
    # Статистика
    stats = load_stats()
    stats['users'].add(user.id)
    stats['users_today'].add(user.id)
    if stats['today'] != datetime.now(timezone.utc).date().isoformat():
        stats['today'] = datetime.now(timezone.utc).date().isoformat()
        stats['users_today'] = set([user.id])
        stats['calc_today'] = 0
    save_stats(stats)
    
    await update.message.reply_photo(
        photo=WELCOME_PHOTO_URL,
        caption=greeting,
        reply_markup=build_main_menu_keyboard(),
        parse_mode=ParseMode.HTML
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "main|calc":
        await query.edit_message_text("Выберите категорию для расчёта:", reply_markup=build_calc_category_keyboard())
        return

    if data == "calc|wall":
        await query.edit_message_text("Выберите тип панели:", reply_markup=build_wall_type_keyboard())
        return

    parts = data.split('|')
    if parts[0] == "calc_type" and len(parts) == 2:
        type_name = parts[1]
        context.user_data['calc_type'] = type_name
        thicknesses = list(WALL_PRODUCTS[type_name].keys())
        await query.edit_message_text(f"Выбрано: {type_name}. Выберите толщину:", reply_markup=build_thickness_keyboard(thicknesses))
        return

    if parts[0] == "thickness" and len(parts) == 2:
        thickness = int(parts[1])
        type_name = context.user_data['calc_type']
        product = WALL_PRODUCTS[type_name][thickness]
        context.user_data['calc_product'] = product
        context.user_data['calc_thickness'] = thickness
        # НОВОЕ: Выбор метода расчёта
        keyboard = build_method_keyboard()
        await query.edit_message_text(
            f"Выбрано: {type_name}, {thickness} мм.\n\nКак рассчитать количество?",
            reply_markup=keyboard
        )
        return

    # НОВОЕ: Обработка метода расчёта
    if parts[0] == "calc_method" and len(parts) == 2:
        method = parts[1]
        context.user_data['calc_method'] = method
        product = context.user_data['calc_product']
        if method == "room":
            await query.edit_message_text(
                "Введите размеры помещения: длина (м), ширина (м), высота (м).\n"
                "Формат: 5, 4, 2.7\n"
                "(Это площадь стен без окон/дверей)",
                reply_markup=build_back_button()
            )
            context.user_data['waiting_for'] = 'room_dimensions'
        else:  # panels
            lengths_keyboard = build_length_keyboard(product['panels'])
            await query.edit_message_text("Выберите длину панели:", reply_markup=lengths_keyboard)
        return

    if parts[0] == "calc_length" and len(parts) == 2:
        length = int(parts[1])
        context.user_data['calc_length'] = length
        await query.edit_message_text(
            "Сколько таких панелей нужно? Введите число:",
            reply_markup=build_back_button()
        )
        context.user_data['waiting_for'] = 'panel_count'
        return

    # Пример обработки back
    if data.startswith("back|"):
        await query.edit_message_text("Главное меню:", reply_markup=build_main_menu_keyboard())
        if 'waiting_for' in context.user_data:
            del context.user_data['waiting_for']
        return

    # ... (добавь обработчики для других кнопок, если нужно, напр. info, catalogs и т.д.)

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    user_data = context.user_data
    if 'waiting_for' not in user_data:
        return

    waiting = user_data['waiting_for']
    product = user_data['calc_product']
    thickness = user_data['calc_thickness']
    method = user_data.get('calc_method', 'room')  # По умолчанию room

    if waiting == 'room_dimensions':
        try:
            dims = [float(x.strip()) for x in text.split(',')]
            if len(dims) != 3:
                raise ValueError
            length, width, height = dims
            area_m2 = 2 * (length + width) * height  # Площадь стен
            # Выбор длины
            lengths_keyboard = build_length_keyboard(product['panels'])
            await update.message.reply_text(
                f"Площадь стен: {area_m2:.2f} м².\nВыберите длину панели для расчёта:",
                reply_markup=lengths_keyboard
            )
            user_data['calc_area'] = area_m2
            user_data['waiting_for'] = 'length_choice_after_room'  # Переходим к выбору длины
        except ValueError:
            await update.message.reply_text("Неверный формат. Попробуйте: 5, 4, 2.7")

    elif waiting == 'panel_count':
        try:
            count = int(text)
            length = user_data['calc_length']
            panel_info = product['panels'][length]
            total_area = count * panel_info['area_m2']
            total_price = count * panel_info['price_rub']
            total_weight = total_area * product['weight_per_m2']
            # Сохраняем статистику
            stats = load_stats()
            stats['calc_count'] += 1
            stats['calc_today'] += 1
            save_stats(stats)
            # Результат
            result_text = (
                f"Расчёт для {count} панелей длиной {length} мм:\n"
                f"• Площадь покрытия: {total_area:.2f} м²\n"
                f"• Стоимость: {total_price:,} руб.\n"
                f"• Вес панелей: {total_weight:.1f} кг\n\n"
                f"Нужны профили? Или другой расчёт?"
            )
            await update.message.reply_text(result_text, reply_markup=build_main_menu_keyboard(), parse_mode=ParseMode.HTML)
            # Очистка
            user_data.clear()
        except ValueError:
            await update.message.reply_text("Введите целое число панелей.")

    elif waiting == 'length_choice_after_room':
        # Обработка выбора длины после ввода размеров (предполагаем, что это callback, но для message - fallback)
        try:
            length = int(text)  # Если ввод числа; иначе используй кнопки
            if length not in product['panels']:
                raise ValueError
            area = user_data['calc_area']
            panel_info = product['panels'][length]
            num_panels = math.ceil(area / panel_info['area_m2'])
            total_area = num_panels * panel_info['area_m2']
            total_price = num_panels * panel_info['price_rub']
            total_weight = total_area * product['weight_per_m2']
            # Статистика
            stats = load_stats()
            stats['calc_count'] += 1
            stats['calc_today'] += 1
            save_stats(stats)
            # Результат (добавлен вес)
            result_text = (
                f"Для площади {area:.2f} м² нужно {num_panels} панелей длиной {length} мм:\n"
                f"• Площадь покрытия: {total_area:.2f} м²\n"
                f"• Стоимость: {total_price:,} руб.\n"
                f"• Вес панелей: {total_weight:.1f} кг\n\n"
                f"Учитывайте +10% на подрезку. Нужны профили?"
            )
            await update.message.reply_text(result_text, reply_markup=build_main_menu_keyboard(), parse_mode=ParseMode.HTML)
            user_data.clear()
        except ValueError:
            await update.message.reply_text("Выберите длину из кнопок или введите число из доступных (2440, 2600 и т.д.).")

    # ... (другие waiting_for для профилей, реек и т.д. остаются)

# Регистрация обработчиков
tg_application.add_handler(CommandHandler("start", start))
tg_application.add_handler(CallbackQueryHandler(button_handler))
tg_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

# ============================
#   FLASK ROUTES (WEBHOOK)
# ============================

@app.route('/', methods=['GET', 'HEAD'])
def index():
    """Health-check для Render."""
    return jsonify({"status": "OK"}), 200

@app.route(f'/{TG_BOT_TOKEN}', methods=['POST'])
def webhook():
    """Telegram webhook handler."""
    try:
        update = Update.de_json(request.get_json(force=True), tg_application.bot)
        if update:
            loop = get_event_loop()
            loop.run_until_complete(tg_application.process_update(update))
        return jsonify({"status": "OK"}), 200
    except TelegramError as e:
        logger.error(f"Telegram error in webhook: {e}")
        return jsonify({"status": "Error", "message": str(e)}), 500
    except Exception as e:
        logger.error(f"Unexpected error in webhook: {e}")
        return jsonify({"status": "Error", "message": str(e)}), 500

if __name__ == "__main__":
    # Для локального запуска (используй ngrok для теста webhook)
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
