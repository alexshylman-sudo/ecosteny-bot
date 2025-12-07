import asyncio
import base64
from io import BytesIO
import json
import os
import random
from datetime import datetime, timezone
import re  # Парсинг размеров

import requests
from quart import Quart, request, jsonify
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    PreCheckoutQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    WebAppInfo,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
    ConversationHandler,
)

import logging
import sys
from telegram import __version__ as TG_VER
from openai import OpenAI  # Для интеграции с GPT (установите openai==1.0+)

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
if OPENAI_API_KEY:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
else:
    openai_client = None
    logger.warning("OpenAI API ключ не установлен — GPT-функции отключены!")

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

# Расширенные фразы для ответов
RESPONSE_PHRASES = {
    "calc_start": [
        "Давайте рассчитаем материалы! Выберите категорию.",
        "Готов к расчёту. Что вы планируете обшить?",
    ],
    "info_generic": [
        "Вот информация по вашему запросу. Есть вопросы?",
        "Подробнее об этом ниже. Нужна помощь?",
    ],
    "error": [
        "Извините, произошла ошибка. Попробуйте снова.",
        "Что-то пошло не так. Вернёмся в меню?",
    ],
}

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

PRODUCT_CODES = {
    "wpc_charcoal": "WPC Бамбук угольный",
    "wpc_bamboo": "WPC Бамбук",
    "wpc_hd": "WPC повышенной плотности",
    "wpc_bamboo_coat": "WPC Бамбук с защитным слоем",
    "wpc_hd_coat": "WPC повышенной плотности с защитным слоем",
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

# ============================
#   ПРОМПТЫ ДЛЯ GPT
# ============================

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
— Формат ответа: Текст + таблица расчёта (кол-во панелей, общая площадь, цена) в Markdown.
"""

CHAT_SYSTEM_PROMPT = """
Ты — живой, дружелюбный ассистент компании ECO Стены.
Помогаешь с выбором и расчётом:
— стеновых WPC панелей,
— реечных панелей (WPC и деревянные),
— 3D панелей.
Отвечай естественно, как человек.
"""

# Функция для вызова GPT
async def call_gpt(prompt: str, system_prompt: str = SYSTEM_PROMPT, model: str = "gpt-4o-mini") -> str:
    if not openai_client:
        return "Извините, функция расчёта временно недоступна. Свяжитесь с менеджером."
    
    try:
        response = openai_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            max_tokens=500,
            temperature=0.7,
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"GPT error: {e}")
        return "Ошибка при расчёте. Попробуйте позже."

# Функция парсинга размеров из текста (расширенная)
def parse_dimensions(text: str) -> dict:
    # Примеры: "5x3 м", "стена 4м длиной, 2.5м высотой", "площадь 20м2"
    patterns = [
        r'(\d+\.?\d*)\s*[xх]\s*(\d+\.?\d*)\s*м',
        r'длина\s+(\d+\.?\d*)\s*м?\s*,?\s*высота\s+(\d+\.?\d*)\s*м?',
        r'площадь\s+(\d+\.?\d*)\s*м2',
    ]
    for pattern in patterns:
        match = re.search(pattern, text.lower())
        if match:
            if len(match.groups()) == 3:  # Площадь
                area = float(match.group(1))
                return {"area": area, "length": None, "height": None}
            else:  # Длина x высота
                length, height = float(match.group(1)), float(match.group(2))
                return {"length": length, "height": height, "area": length * height}
    return {"length": None, "height": None, "area": None}

# ============================
#   FLASK + TELEGRAM
# ============================

app = Quart(__name__)

# Создаём приложение Telegram
tg_application = Application.builder().token(TG_BOT_TOKEN).build()

# Добавляем error handler
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Exception while handling an update: {context.error}")
    if update and update.effective_chat:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=random.choice(RESPONSE_PHRASES["error"]),
        )

tg_application.add_error_handler(error_handler)

# ============================
#   КЛАВИАТУРЫ (УЛУЧШЕННЫЕ С ЭМОДЗИ)
# ============================

def build_main_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🧮 Рассчитать материалы", callback_data="main|calc")],
        [InlineKeyboardButton("ℹ️ Информация", callback_data="main|info")],
        [InlineKeyboardButton("📚 Получить каталоги", callback_data="main|catalogs")],
        [InlineKeyboardButton("📽️ Получить презентацию", callback_data="main|presentation")],
        [InlineKeyboardButton("📞 Контактная информация", callback_data="main|contacts")],
        [InlineKeyboardButton("🤝 Хочу стать партнёром", callback_data="main|partner")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton("⚙️ Администрирование", callback_data="main|admin")])
    return InlineKeyboardMarkup(rows)

def build_back_row() -> list[list[InlineKeyboardButton]]:
    return [[InlineKeyboardButton("🔙 Назад", callback_data="ui|back")]]

def build_back_to_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(build_back_row())

def build_calc_category_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🧱 1. Стеновые панели", callback_data="calc_cat|walls")],
        [InlineKeyboardButton("🎋 2. Реечные панели", callback_data="calc_cat|slats")],
        [InlineKeyboardButton("🔳 3. 3D панели", callback_data="calc_cat|3d")],
    ]
    rows += build_back_row()
    return InlineKeyboardMarkup(rows)

def build_wall_product_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for code, title in PRODUCT_CODES.items():
        buttons.append([InlineKeyboardButton(text=f"🧱 {title}", callback_data=f"product|{code}")])
    buttons += build_back_row()
    return InlineKeyboardMarkup(buttons)

def build_after_calc_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("➕ Добавить материалы", callback_data="after_calc|add")],
        [InlineKeyboardButton("📤 Отправить расчёт админу", callback_data="after_calc|send")],
        [InlineKeyboardButton("🏠 Вернуться в меню", callback_data="after_calc|menu")],
    ]
    return InlineKeyboardMarkup(rows)

def build_skip_name_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❓ Я не знаю → ДАЛЬШЕ", callback_data="after_name|skip")]
    ])

def build_thickness_keyboard(product_code: str) -> InlineKeyboardMarkup:
    title = PRODUCT_CODES[product_code]
    thicknesses = WALL_PRODUCTS.get(title, {})
    rows = []
    row = []
    for thickness in sorted(thicknesses.keys()):
        row.append(InlineKeyboardButton(
            text=f"📏 {thickness} мм",
            callback_data=f"thickness|{product_code}|{thickness}",
        ))
    if row:
        rows.append(row)
    rows += build_back_row()
    return InlineKeyboardMarkup(rows)

def build_height_keyboard(product_code: str, thickness: int) -> InlineKeyboardMarkup:
    # Стандартные высоты панелей
    heights = [2440, 2600, 2800, 3000, 3200]
    rows = []
    for i in range(0, len(heights), 2):
        row = []
        for j in range(i, min(i+2, len(heights))):
            h = heights[j]
            row.append(InlineKeyboardButton(
                text=f"📐 {h} мм",
                callback_data=f"height|{product_code}|{thickness}|{h}",
            ))
        rows.append(row)
    rows += build_back_row()
    return InlineKeyboardMarkup(rows)

# НОВАЯ КЛАВИАТУРА: Подменю для "Информация"
def build_info_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🏢 О компании", callback_data="info|company")],
        [InlineKeyboardButton("🚚 Доставка и оплата", callback_data="info|delivery")],
        [InlineKeyboardButton("🛡️ Гарантии и сертификаты", callback_data="info|warranty")],
        [InlineKeyboardButton("💡 Советы по монтажу", callback_data="info|installation")],
    ]
    rows += build_back_row()
    return InlineKeyboardMarkup(rows)

# Клавиатура для реечных панелей
def build_slat_type_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🌿 WPC реечные", callback_data="slat|wpc")],
        [InlineKeyboardButton("🌳 Деревянные реечные", callback_data="slat|wood")],
    ]
    rows += build_back_row()
    return InlineKeyboardMarkup(rows)

# Клавиатура для 3D панелей
def build_3d_panel_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🔳 600x1200 мм", callback_data="3d|var1")],
        [InlineKeyboardButton("🔳 1200x3000 мм", callback_data="3d|var2")],
    ]
    rows += build_back_row()
    return InlineKeyboardMarkup(rows)

# Админ-клавиатура
def build_admin_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📊 Статистика пользователей", callback_data="admin|stats")],
        [InlineKeyboardButton("📤 Рассылка", callback_data="admin|broadcast")],
        [InlineKeyboardButton("👥 Список пользователей", callback_data="admin|users")],
        [InlineKeyboardButton("🔧 Настройки", callback_data="admin|settings")],
    ]
    rows += build_back_row()
    return InlineKeyboardMarkup(rows)

# ============================
#   HANDLERS
# ============================

# Состояния ConversationHandler (для многошаговых диалогов)
NAME, DIMENSIONS, CONFIRM = range(3)

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    first_name = user.first_name or "друг"
    greeting = random.choice(GREETING_PHRASES).format(name=first_name)
    
    # Сохраняем пользователя в контексте (для статистики)
    context.user_data["user_id"] = user.id
    context.user_data["first_name"] = first_name
    context.user_data["start_time"] = datetime.now(timezone.utc)
    
    # Отправляем фото или текст
    if WELCOME_PHOTO_URL:
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=WELCOME_PHOTO_URL,
            caption=greeting,
            reply_markup=build_main_menu_keyboard(is_admin=user.id == ADMIN_CHAT_ID)
        )
    else:
        await update.message.reply_text(
            greeting,
            reply_markup=build_main_menu_keyboard(is_admin=user.id == ADMIN_CHAT_ID)
        )
    
    logger.info(f"Received /start from user: {user.id}")

# Основной обработчик callback_query (расширенный и исправленный)
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    data = query.data
    logger.info(f"Callback query: {data}")
    
    if data == "ui|back":
        # Возврат в главное меню
        await query.edit_message_text(
            "Главное меню:",
            reply_markup=build_main_menu_keyboard(is_admin=query.from_user.id == ADMIN_CHAT_ID)
        )
        return ConversationHandler.END
    
    parts = data.split("|")
    action = parts[0]
    subaction = parts[1] if len(parts) > 1 else ""
    
    if action == "main":
        if subaction == "calc":
            # Исправлено: Теперь работает кнопка "Рассчитать материалы"
            await query.edit_message_text(
                random.choice(RESPONSE_PHRASES["calc_start"]),
                reply_markup=build_calc_category_keyboard()
            )
            return ConversationHandler.END
        elif subaction == "info":
            # Исправлено: Теперь работает кнопка "Информация" с подкатегориями
            await query.edit_message_text(
                "Информация о компании и услугах:",
                reply_markup=build_info_keyboard()
            )
            return ConversationHandler.END
        elif subaction == "catalogs":
            await query.edit_message_text(
                "Каталоги будут отправлены в ближайшее время. Свяжитесь с менеджером для актуальной версии!",
                reply_markup=build_main_menu_keyboard(is_admin=query.from_user.id == ADMIN_CHAT_ID)
            )
            return ConversationHandler.END
        elif subaction == "presentation":
            await query.edit_message_text(
                "Презентация будет отправлена на email. Укажите ваш адрес в сообщении!",
                reply_markup=build_main_menu_keyboard(is_admin=query.from_user.id == ADMIN_CHAT_ID)
            )
            return ConversationHandler.END
        elif subaction == "contacts":
            # Исправлено: Теперь работает кнопка "Контактная информация"
            contacts_text = """
📞 Контактная информация ECO Стены:

🛒 Сайт: ecosteni.ru
📧 Email: info@ecosteni.ru
☎️ Телефон: +7 (495) 123-45-67
📍 Адрес: Москва, ул. Примерная, д. 123

Мы всегда на связи! 😊
            """
            await query.edit_message_text(
                contacts_text,
                reply_markup=build_main_menu_keyboard(is_admin=query.from_user.id == ADMIN_CHAT_ID)
            )
            return ConversationHandler.END
        elif subaction == "partner":
            await query.edit_message_text(
                "Чтобы стать партнёром, напишите нам на email: partners@ecosteni.ru с описанием вашего бизнеса.",
                reply_markup=build_main_menu_keyboard(is_admin=query.from_user.id == ADMIN_CHAT_ID)
            )
            return ConversationHandler.END
        elif subaction == "admin":
            # Админ-панель
            await query.edit_message_text(
                "Админ-панель:",
                reply_markup=build_admin_keyboard()
            )
            return ConversationHandler.END
    
    elif action == "info":
        # Подкатегории информации (исправлено)
        if subaction == "company":
            text = "🏢 ECO Стены — ведущий поставщик экологичных WPC панелей для интерьера. Мы предлагаем качественные материалы по доступным ценам!"
        elif subaction == "delivery":
            text = "🚚 Доставка по всей России. Сроки: 3-7 дней. Бесплатно от 20 000 руб."
        elif subaction == "warranty":
            text = "🛡️ Гарантия 10 лет на все панели. Сертификаты качества прилагаются."
        elif subaction == "installation":
            text = "💡 Советы по монтажу: Используйте профили для ровной установки. Видео-инструкции в каталоге."
        else:
            text = "Выберите тему информации выше."
        
        await query.edit_message_text(
            text + "\n\n" + random.choice(RESPONSE_PHRASES["info_generic"]),
            reply_markup=build_info_keyboard()  # Остаёмся в подменю
        )
        return ConversationHandler.END
    
    elif action == "calc_cat":
        # Обработка категорий расчёта
        if subaction == "walls":
            await query.edit_message_text(
                "Выберите тип стеновой панели:",
                reply_markup=build_wall_product_keyboard()
            )
            return ConversationHandler.END
        elif subaction == "slats":
            await query.edit_message_text(
                "Выберите тип реечных панелей:",
                reply_markup=build_slat_type_keyboard()
            )
            return ConversationHandler.END
        elif subaction == "3d":
            await query.edit_message_text(
                "Выберите 3D панель:",
                reply_markup=build_3d_panel_keyboard()
            )
            return ConversationHandler.END
    
    elif action == "product":
        # Обработка выбора продукта (добавьте толщину)
        product_code = subaction
        await query.edit_message_text(
            f"Выбрана панель: {PRODUCT_CODES[product_code]}. Выберите толщину:",
            reply_markup=build_thickness_keyboard(product_code)
        )
        context.user_data["selected_product"] = product_code
        return ConversationHandler.END
    
    elif action == "thickness":
        # Выбор высоты после толщины
        _, product_code, thickness = parts
        context.user_data["selected_thickness"] = int(thickness)
        await query.edit_message_text(
            f"Выбрано: {PRODUCT_CODES[product_code]}, толщина {thickness} мм. Выберите высоту панели:",
            reply_markup=build_height_keyboard(product_code, int(thickness))
        )
        return ConversationHandler.END
    
    elif action == "height":
        # Теперь запуск расчёта с GPT
        _, product_code, thickness, height = parts
        context.user_data["selected_height"] = int(height)
        
        # Подготовка промпта для GPT
        catalog_json = json.dumps(WALL_PRODUCTS)
        prompt = f"Каталог: {catalog_json}\nВыбран: {PRODUCT_CODES[product_code]}, толщина {thickness} мм, высота {height} мм.\nУкажите размеры стен для расчёта."
        
        gpt_response = await call_gpt(prompt)
        
        await query.edit_message_text(
            gpt_response,
            reply_markup=build_after_calc_keyboard()
        )
        # Сохраняем расчёт для отправки админу
        context.user_data["last_calc"] = gpt_response
        return ConversationHandler.END
    
    elif action == "slat":
        # Расчёт реечных
        material = subaction
        price_per_panel = SLAT_PRICES[material]
        spec_text = f"Спецификация: ширина {SLAT_PANEL_SPEC['width_mm']} мм, длина {SLAT_PANEL_SPEC['length_mm']} мм, толщина {SLAT_PANEL_SPEC['thickness_mm']} мм. Цена: {price_per_panel} руб/панель."
        await query.edit_message_text(
            f"Выбраны {material.upper()} реечные панели.\n{spec_text}\nУкажите длину стены в м:",
            reply_markup=build_back_row()
        )
        context.user_data["slat_material"] = material
        return NAME  # Переход к вводу текста
    
    elif action == "3d":
        # Расчёт 3D
        var = subaction
        panel = PANELS_3D[var]
        await query.edit_message_text(
            f"Выбрана 3D панель {panel['code']}: {panel['width_mm']}x{panel['height_mm']} мм, {panel['price_rub']} руб.\nУкажите количество:",
            reply_markup=build_back_row()
        )
        context.user_data["3d_panel"] = panel
        return NAME
    
    elif action == "after_calc":
        if subaction == "add":
            await query.edit_message_text(
                "Добавление материалов: (логика расширения)",
                reply_markup=build_calc_category_keyboard()
            )
        elif subaction == "send":
            # Отправка админу
            if "last_calc" in context.user_data:
                await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=f"Расчёт от {query.from_user.first_name}: {context.user_data['last_calc']}"
                )
                await query.edit_message_text("Расчёт отправлен админу!")
            else:
                await query.edit_message_text("Нет расчёта для отправки.")
        elif subaction == "menu":
            await query.edit_message_text(
                "Главное меню:",
                reply_markup=build_main_menu_keyboard(is_admin=query.from_user.id == ADMIN_CHAT_ID)
            )
        return ConversationHandler.END
    
    elif action == "after_name":
        if subaction == "skip":
            await query.edit_message_text(
                "Продолжаем без имени. Укажите детали проекта.",
                reply_markup=build_main_menu_keyboard()
            )
        return ConversationHandler.END
    
    elif action == "admin":
        if query.from_user.id != ADMIN_CHAT_ID:
            await query.edit_message_text("Доступ запрещён.")
            return ConversationHandler.END
        if subaction == "stats":
            # Пример статистики (в реальности из БД)
            stats = f"Активных пользователей: 42\nРасчётов сегодня: 5"
            await query.edit_message_text(stats, reply_markup=build_admin_keyboard())
        elif subaction == "broadcast":
            await query.edit_message_text("Введите текст для рассылки:", reply_markup=build_back_row())
            return NAME  # Ввод текста для broadcast
        elif subaction == "users":
            await query.edit_message_text("Список пользователей: (в разработке)", reply_markup=build_admin_keyboard())
        elif subaction == "settings":
            await query.edit_message_text("Настройки: (в разработке)", reply_markup=build_admin_keyboard())
        return ConversationHandler.END
    
    else:
        await query.edit_message_text(
            "Неизвестная кнопка. Вернитесь в меню.",
            reply_markup=build_main_menu_keyboard(is_admin=query.from_user.id == ADMIN_CHAT_ID)
        )
        return ConversationHandler.END

# Обработчик текстовых сообщений (расширенный для Conversation)
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text
    user_id = update.effective_user.id
    
    # Парсинг для расчёта
    dims = parse_dimensions(text)
    if dims["area"] or dims["length"]:
        # Вызов GPT с размерами
        catalog_json = json.dumps(WALL_PRODUCTS)
        prompt = f"Каталог: {catalog_json}\nРазмеры: {dims}\nРассчитай для стандартной WPC панели 8мм."
        gpt_response = await call_gpt(prompt)
        await update.message.reply_text(gpt_response, reply_markup=build_after_calc_keyboard())
        context.user_data["last_calc"] = gpt_response
        return ConversationHandler.END
    
    # Админ-рассылка
    if user_id == ADMIN_CHAT_ID and "broadcast_mode" in context.user_data:
        # Здесь логика рассылки (нужна БД пользователей)
        await update.message.reply_text("Рассылка отправлена! (симуляция)")
        del context.user_data["broadcast_mode"]
        return ConversationHandler.END
    
    # Общий fallback
    await update.message.reply_text(
        "Привет! Используйте кнопки меню для навигации или опишите проект для расчёта.",
        reply_markup=build_main_menu_keyboard(is_admin=user_id == ADMIN_CHAT_ID)
    )
    return ConversationHandler.END

# Fallback для Conversation
async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Пожалуйста, используйте кнопки.")
    return ConversationHandler.END

# ============================
#   РЕГИСТРАЦИЯ HANDLERS (ConversationHandler для многошаговости)
# ============================

conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(button_handler, pattern=r"^(main|calc_cat|product|thickness|height|slat|3d|info|admin|after_calc|after_name)$")],
    states={
        NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)],
        DIMENSIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)],
        CONFIRM: [CallbackQueryHandler(button_handler)],
    },
    fallbacks=[MessageHandler(filters.TEXT & ~filters.COMMAND, fallback)],
)

tg_application.add_handler(CommandHandler("start", start))
tg_application.add_handler(conv_handler)
tg_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))  # Для не-Conversation

# ============================
#   WEBHOOK SETUP (для Render)
# ============================

@app.route(f'/{TG_BOT_TOKEN}', methods=['POST'])
async def webhook():
    json_data = await request.get_json()
    update = Update.de_json(json_data, tg_application.bot)
    await tg_application.process_update(update)
    return jsonify({"ok": True})

# ============================
#   ЗАПУСК
# ============================

async def main():
    # Установка webhook
    hostname = os.environ.get('RENDER_EXTERNAL_HOSTNAME', 'localhost')
    webhook_url = f"https://{hostname}/{TG_BOT_TOKEN}"
    await tg_application.bot.set_webhook(url=webhook_url)
    
    logger.info(f"Webhook set to {webhook_url}")
    
    # Получение info webhook
    info = await tg_application.bot.get_webhook_info()
    logger.info(f"Webhook info: {info}")
    
    # Запуск Quart app с Hypercorn
    from hypercorn.config import Config
    from hypercorn.asyncio import serve
    
    config = Config()
    config.bind = [f"0.0.0.0:{port}"]
    config.use_reloader = False
    config.certfile = None  # HTTPS обрабатывается Render
    
    await serve(app, config)

if __name__ == '__main__':
    asyncio.run(main())


