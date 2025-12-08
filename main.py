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

GREETING_PHRASES = [
    "Привет, {name}! Я ассистент компании ECO Стены. Помогу с подбором материалов и расчётом панелей. 😊",
    "Рад знакомству, {name}! Я здесь, чтобы помочь вам с продукцией ECO Стены и ответить на вопросы.",
    "Здравствуйте, {name}! Если планируете ремонт или обновление интерьера — давайте подберём материалы вместе.",
    "{name}, привет! Я подскажу по WPC панелям, профилям, каталогу и примерному расчёту под ваши размеры.",
    "Добро пожаловать, {name}! Рассказывайте, какой у вас объект — подберём оптимальное решение из наших материалов.",
]

# Файл для хранения статистики (на Render - ephemeral, но для простоты)
STATS_FILE = "/tmp/eco_stats.json"
USER_DATA_FILE = "/tmp/eco_user_data.json"  # Новый файл для хранения данных пользователей (units, etc.)

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
    default_data = {}
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
    return default_data

def save_user_data(user_data):
    try:
        with open(USER_DATA_FILE, 'w') as f:
            json.dump(user_data, f)
    except Exception as e:
        logger.error(f"Failed to save user data: {e}")

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
    # SPC переименована и отделена
    "Стеновые панели SPC": {  # Без толщины
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
    "spc_panel": "Стеновые панели SPC",
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

Если клиент выбрал несколько материалов, в запросе может быть список этих материалов — используй его и в расчёте, и в формулировке....

Также:
— Если ранее уже была проанализирована планировка с размерами, обязательно используй эти данные.
— Для вычета площадей используй термины ОКНО и ДВЕРЬ вместо window и door.
— Сначала спрашивай ширину окна/двери, потом высоту.
— Отвечай по-русски, кратко, дружелюбно и по делу.
— Если пользователь вводит ширину стены как несколько значений через +, суммируй их автоматически.
— Единицы измерения (м или мм) спрашивай только один раз за сессию и запоминай.
"""

CHAT_SYSTEM_PROMPT = """
Ты — живой, дружелюбный ассистент компании ECO Стены.
Помогаешь с выбором и расчётом:
— стеновых WPC панелей,
— реечных панелей (WPC и деревянные),
— 3D панелей.
— профилей.
— Стеновых панелей SPC.
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
        [InlineKeyboardButton("🤝 Партнёрская программа", callback_data="main|partner")],
    ]
    return InlineKeyboardMarkup(buttons)

def build_calc_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("🧱 WPC панели", callback_data="calc|wpc")],
        [InlineKeyboardButton("🔲 Стеновые панели SPC", callback_data="calc|spc")],
        [InlineKeyboardButton("📏 Реечные панели", callback_data="calc|slats")],
        [InlineKeyboardButton("🎨 3D панели", callback_data="calc|3d")],
        [InlineKeyboardButton("🔧 Профили", callback_data="calc|profiles")],
        [InlineKeyboardButton("🔙 Назад", callback_data="main|menu")],
    ]
    return InlineKeyboardMarkup(buttons)

def build_wpc_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("🌿 WPC Бамбук угольный", callback_data="wpc|charcoal")],
        [InlineKeyboardButton("🌱 WPC Бамбук", callback_data="wpc|bamboo")],
        [InlineKeyboardButton("💎 WPC повышенной плотности", callback_data="wpc|hd")],
        [InlineKeyboardButton("🛡️ WPC Бамбук с защитным слоем", callback_data="wpc|bamboo_coat")],
        [InlineKeyboardButton("🛡️ WPC повышенной плотности с защитным слоем", callback_data="wpc|hd_coat")],
        [InlineKeyboardButton("🔙 Назад", callback_data="calc|back")],
    ]
    return InlineKeyboardMarkup(buttons)

# Другие клавиатуры аналогично, с эмодзи

def build_thickness_keyboard(thicknesses) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(f"{t} мм", callback_data=f"thickness|{t}")] for t in thicknesses]
    buttons.append([InlineKeyboardButton("🔙 Назад", callback_data="wpc|back")])
    return InlineKeyboardMarkup(buttons)

# ... (другие клавиатуры с эмодзи)

# ============================
#   WEBHOOK PROCESSOR
# ============================

async def process_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await tg_application.process_update(update)
    except Exception as e:
        logger.error(f"Error processing update: {e}")

def handle_webhook():
    data = request.get_json()
    if not data:
        return jsonify({"status": "ok"}), 200

    update = Update.de_json(data, tg_application.bot)
    if update:
        loop = get_event_loop()
        asyncio.run_coroutine_threadsafe(process_update(update, tg_application), loop)
    return jsonify({"status": "ok"}), 200

app.add_url_rule('/webhook', 'webhook', handle_webhook, methods=['POST'])

# ============================
#   HANDLERS
# ============================

# Хранение состояний для AI-логики (простой dict, persistent через файл)
user_states = {}  # user_id -> state dict
user_data = load_user_data()  # Загружаем при старте

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.first_name or "Друг"

    # Загружаем stats
    stats = load_stats()
    if user_id not in stats['users']:
        stats['users'].add(user_id)
        if datetime.now(timezone.utc).date().isoformat() == stats['today']:
            stats['users_today'].add(user_id)
        else:
            stats['today'] = datetime.now(timezone.utc).date().isoformat()
            stats['users_today'] = {user_id}
            stats['calc_today'] = 0
    save_stats(stats)

    # Приветствие
    greeting = random.choice(GREETING_PHRASES).format(name=username)
    await update.message.reply_photo(
        photo=WELCOME_PHOTO_URL,
        caption=greeting,
        reply_markup=build_main_menu_keyboard(),
        parse_mode='HTML'
    )

    # Инициализация user_data если нет
    if user_id not in user_data:
        user_data[user_id] = {'units': None}  # m or mm
        save_user_data(user_data)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split('|')

    user_id = query.from_user.id

    if data[0] == 'main':
        if data[1] == 'calc':
            await query.edit_message_reply_markup(reply_markup=build_calc_keyboard())
            await query.message.reply_text("Выберите тип материалов для расчёта:", reply_markup=build_calc_keyboard())
        elif data[1] == 'info':
            await query.edit_message_text(
                text="ECO Стены — премиум материалы для отделки: WPC панели, реечные системы, 3D панели и профили. "
                     "Экологично, влагостойко, просто в монтаже! 🌿",
                reply_markup=build_main_menu_keyboard()
            )
        elif data[1] == 'catalogs':
            await query.edit_message_text(
                text=f"Каталоги в PDF: <a href='https://ecosteni.ru/catalog/'>Скачать здесь</a>",
                reply_markup=build_main_menu_keyboard(),
                parse_mode='HTML'
            )
        elif data[1] == 'presentation':
            await query.edit_message_text(
                text=f"Презентация компании: <a href='{PRESENTATION_URL}'>Скачать PDF</a>",
                reply_markup=build_main_menu_keyboard(),
                parse_mode='HTML'
            )
        elif data[1] == 'contacts':
            # Исправлено: вместо tel: используем текст с номером и ссылкой на звонок через Telegram
            text = (
                "📞 Контакты:\n"
                "Тел: +7 (978) 022-32-22\n"
                "Группа: {group}\n"
                "Email: info@ecosteni.ru\n\n"
                "Напишите в группу для быстрой связи!"
            ).format(group=TG_GROUP)
            await query.edit_message_text(text=text, reply_markup=build_main_menu_keyboard())
        elif data[1] == 'partner':
            # Логика партнёрки: спрашиваем имя, затем организуем полный flow
            user_states[user_id] = {'mode': 'partner_name'}
            await query.edit_message_text(
                text="🤝 Партнёрская программа!\n\n"
                     "Введите ваше имя для регистрации в программе:",
                reply_markup=None
            )
        elif data[1] == 'menu':
            await query.edit_message_reply_markup(reply_markup=build_main_menu_keyboard())

    elif data[0] == 'calc':
        # ... (логика для calc, без SPC в WPC)
        pass  # Дополнить по аналогии

    # ... (другие callbacks)

    # Для толщины, etc.

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    # Загрузка user_data
    global user_data
    user_units = user_data.get(user_id, {}).get('units')

    if user_states.get(user_id, {}).get('mode') == 'partner_name':
        # Полная логика партнёрки: после имени - приветствие и возврат к меню
        name = text.strip()
        await update.message.reply_text(
            f"Отлично, {name}! Вы зарегистрированы в партнёрской программе. "
            f"Вам предоставят доступ к специальным условиям. "
            f"Для расчёта используйте меню ниже. 😊",
            reply_markup=build_main_menu_keyboard()
        )
        user_states[user_id] = {}  # Сброс состояния
        # Здесь можно отправить уведомление админу
        await context.bot.send_message(ADMIN_CHAT_ID, f"Новый партнёр: {name} (ID: {user_id})")
        return

    # Обработка единиц измерения: только если не заданы
    if user_units is None:
        # Предполагаем, что это первый ввод размеров - спросить units
        if re.match(r'^\d+(?:\s*[\+\s]\d+)*$', text):  # Похоже на ширину с +
            await update.message.reply_text(
                "Укажите единицы измерения: м или мм? (Запомню на всю сессию)"
            )
            user_states[user_id] = {'mode': 'units_setup', 'pending_input': text}
            return
        elif re.match(r'^(м|мм)$', text.lower()):
            # Если уже ввели units
            pass
        else:
            return  # Не трогаем

    if user_states.get(user_id, {}).get('mode') == 'units_setup':
        units = text.lower().strip()
        if units in ['м', 'мм']:
            user_data[user_id]['units'] = units
            save_user_data(user_data)
            pending = user_states[user_id]['pending_input']
            # Обработать pending как ширину стены
            total_width = sum(float(x.strip()) for x in pending.split('+'))
            # Продолжить логику расчёта с total_width
            await update.message.reply_text(
                f"Единицы {units} запомнены. Ширина стены: {total_width} {units} (сумма введённых)."
            )
            # Здесь продолжить flow: спросить высоту, etc.
            user_states[user_id] = {'mode': 'wall_height', 'wall_width': total_width}
        else:
            await update.message.reply_text("Пожалуйста, введите 'м' или 'мм'.")
        return

    # Общая логика для сообщений: передача в AI
    # (Предполагаем, что есть функция call_openai с prompt включая catalog, units, etc.)
    # Для ширины: если в контексте расчёта, парсить + и суммировать
    if re.match(r'^\d+(?:\s*[\+\s]\d+)*$', text):
        # Автоматическая сумма для ширины
        total = sum(float(x.strip()) for x in text.split('+'))
        await update.message.reply_text(f"Суммарная ширина: {total}")

    # Для окон/дверей: в prompt указать порядок: сначала ширина, потом высота
    # В SYSTEM_PROMPT уже добавлено

    # Вызов AI
    # catalog_json = json.dumps(WALL_PRODUCTS)  # Передать в prompt
    # response = call_openai(text, catalog_json, user_units)
    # await update.message.reply_text(response)

# Регистрация handlers
tg_application.add_handler(CommandHandler("start", start))
tg_application.add_handler(CallbackQueryHandler(button_callback))
tg_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# ============================
#   LAUNCH - ONLY WEBHOOK
# ============================

if __name__ == '__main__':
    # Только webhook, без polling
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

    # Set webhook (один раз при деплое)
    # await tg_application.bot.set_webhook(url=f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}/webhook")
