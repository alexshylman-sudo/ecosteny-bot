import logging
import os
import math
import random
import re
from typing import Dict, Any, List
from io import BytesIO
import base64
from openai import OpenAI

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Константы
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_ID = 203473623
WELCOME_PHOTO_URL = "https://ecosteni.ru/wp-content/uploads/2025/11/qncccaze.jpg"
CATALOG_PDF_URL = "https://ecosteni.ru/wp-content/uploads/2025/11/ecosteny_prezentacziya.pdf"

# Системный промпт для GPT
SYSTEM_PROMPT = """
Ты эксперт по материалам ECO Стены. Отвечай дружелюбно, на русском языке, с эмодзи. 
Используй знания о WPC, SPC, реечных, 3D-панелях, профилях. Рекомендуй расчёт для точности.
Для фото: Оцени размеры стены, предложи расчёт. Если вопрос не по теме — предложи меню.
"""

# Цены (на основе предоставленной логики)
WALL_PRODUCTS = {
    'wpc_charcoal': {  # WPC Бамбук угольный
        5: {2440: 10500, 2600: 11100, 2800: 12000, 3000: 12900, 3200: 13700},
        8: {2440: 12200, 2600: 13000, 2800: 14000, 3000: 15000, 3200: 16000}
    },
    'wpc_bamboo': {  # WPC Бамбук
        5: {2440: 12200, 2600: 13000, 2800: 14000, 3000: 15000, 3200: 16000},
        8: {2440: 13900, 2600: 14900, 2800: 16000, 3000: 17100, 3200: 18300}
    },
    'wpc_hd': {  # WPC повышенной плотности
        8: {2440: 15500, 2600: 16500, 2800: 17800, 3000: 19100, 3200: 20300}
    },
    'wpc_protect': {  # WPC с защитным слоем
        8: {2440: 16400, 2600: 17500, 2800: 18800, 3000: 20100, 3200: 21500}
    },
    'wpc_hd_protect': {  # WPC ПД с защитным слоем
        8: {2440: 18000, 2600: 19100, 2800: 20600, 3000: 22100, 3200: 23500}
    },
    'spc': {  # SPC Панель (нет толщины)
        0: {2440: 9500, 2600: 10100}  # Пример, добавь больше
    }
}

SLAT_PRICES = {
    'wpc': 1200,  # руб./м.п.
    'wood': 1500
}

THREE_D_PRICES = {
    'small': 3000,  # 600x1200
    'large': 8000   # 1200x3000
}

PROFILE_PRICES = {
    5: {
        'joint': 1350,
        'joint_wide': 1500,
        'joint_light': 1700,
        'finish': 1350,
        'outer_corner': 1450,
        'inner_corner': 1450
    },
    8: {
        'joint': 1450,
        'joint_wide': 1600,
        'joint_light': 1800,
        'finish': 1450,
        'outer_corner': 1550,
        'inner_corner': 1550
    }
}

# Варианты приветствий
WELCOME_MESSAGES = [
    "Рад встрече! Готов посчитать расход WPC?",
    "ECO Стены — для идеального ремонта. Что посоветовать?",
    "Привет! Опиши стену, и я дам точный расчёт."
]

# Функция эскейпа Markdown
def escape_markdown(text: str) -> str:
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    for char in escape_chars:
        text = text.replace(char, f'\\{char}')
    return text

# Клавиатуры
def build_main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🧮 Рассчитать материалы", callback_data="main|calc")],
        [InlineKeyboardButton("📋 Получить каталоги", callback_data="main|catalogs")],
        [InlineKeyboardButton("📞 Контактная информация", callback_data="main|contacts")],
        [InlineKeyboardButton("🤝 Хочу стать партнером", callback_data="main|partner")]
    ]
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("⚙️ Администрирование", callback_data="main|admin")])
    return InlineKeyboardMarkup(keyboard)

def build_calc_category_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🧱 Стеновые панели", callback_data="calc_cat|walls")],
        [InlineKeyboardButton("🔩 Профили", callback_data="calc_cat|profiles")],
        [InlineKeyboardButton("🔲 Реечные панели", callback_data="calc_cat|slats")],
        [InlineKeyboardButton("🎨 3D панели", callback_data="calc_cat|3d")],
        [InlineKeyboardButton("🪨 Гибкий камень", callback_data="calc_cat|stone")],
        [InlineKeyboardButton("🔙 Назад", callback_data="main|back")]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_walls_type_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("WPC Бамбук угольный (от 10500 руб.)", callback_data="product|wpc_charcoal")],
        [InlineKeyboardButton("WPC Бамбук (от 12200 руб.)", callback_data="product|wpc_bamboo")],
        [InlineKeyboardButton("WPC повышенной плотности (от 15500 руб.)", callback_data="product|wpc_hd")],
        [InlineKeyboardButton("WPC с защитным слоем (от 16400 руб.)", callback_data="product|wpc_protect")],
        [InlineKeyboardButton("WPC ПД с защитным слоем (от 18000 руб.)", callback_data="product|wpc_hd_protect")],
        [InlineKeyboardButton("SPC Панель (от 9500 руб.)", callback_data="product|spc")],
        [InlineKeyboardButton("🔙 Назад", callback_data="calc_cat|back")]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_thickness_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("5 мм", callback_data="thickness|5")],
        [InlineKeyboardButton("8 мм", callback_data="thickness|8")],
        [InlineKeyboardButton("🔙 Назад", callback_data="product|back")]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_length_keyboard(product: str, thickness: int) -> InlineKeyboardMarkup:
    lengths = list(WALL_PRODUCTS.get(product, {}).get(thickness, {}).keys())
    keyboard = [[InlineKeyboardButton(f"{length} мм", callback_data=f"length|{length}")] for length in lengths]
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="thickness|back")])
    return InlineKeyboardMarkup(keyboard)

def build_profile_thickness_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("Для 5 мм", callback_data="profile_thick|5")],
        [InlineKeyboardButton("Для 8 мм", callback_data="profile_thick|8")],
        [InlineKeyboardButton("🔙 Назад", callback_data="calc_cat|back")]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_profile_type_keyboard(thickness: int) -> InlineKeyboardMarkup:
    types = ['joint', 'joint_wide', 'joint_light', 'finish', 'outer_corner', 'inner_corner']
    keyboard = [[InlineKeyboardButton(f"{t.replace('_', ' ').title()}", callback_data=f"profile_type|{t}")] for t in types]
    keyboard.append([InlineKeyboardButton("Добавить другой", callback_data="profile_thick|back")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="profile_thick|back")])
    return InlineKeyboardMarkup(keyboard)

def build_slat_type_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("WPC реечные (1200 руб./м.п.)", callback_data="slat|wpc")],
        [InlineKeyboardButton("Деревянные реечные (1500 руб./м.п.)", callback_data="slat|wood")],
        [InlineKeyboardButton("🔙 Назад", callback_data="calc_cat|back")]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_3d_size_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("600x1200 мм (3000 руб.)", callback_data="3d_size|small")],
        [InlineKeyboardButton("1200x3000 мм (8000 руб.)", callback_data="3d_size|large")],
        [InlineKeyboardButton("🔙 Назад", callback_data="calc_cat|back")]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_catalog_category_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🧱 Стеновые панели", callback_data="catalog|walls")],
        [InlineKeyboardButton("🔩 Профили", callback_data="catalog|profiles")],
        [InlineKeyboardButton("🔲 Реечные панели", callback_data="catalog|slats")],
        [InlineKeyboardButton("🎨 3D панели", callback_data="catalog|3d")],
        [InlineKeyboardButton("🔙 Назад", callback_data="main|back")]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_contacts_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("📞 Позвонить", url="tel:+79780223222")],
        [InlineKeyboardButton("✉️ Написать", url="mailto:info@ecosteni.ru")],
        [InlineKeyboardButton("🔙 Назад", callback_data="main|back")]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_partner_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🔙 Назад", callback_data="main|back")]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_admin_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("📊 Статистика", callback_data="admin|stats")],
        [InlineKeyboardButton("📢 Рассылка", callback_data="admin|broadcast")],
        [InlineKeyboardButton("📋 Логи", callback_data="admin|logs")],
        [InlineKeyboardButton("🔙 Назад", callback_data="main|back")]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_calc_type_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("По помещению", callback_data="calc_type|room")],
        [InlineKeyboardButton("По панели", callback_data="calc_type|panel")],
        [InlineKeyboardButton("🔙 Назад к выбору", callback_data="calc_cat|back")]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_after_calc_keyboard(materials_count: int) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("➕ Добавить материал", callback_data="main|calc")],
        [InlineKeyboardButton("📊 Итоговый расчёт", callback_data="calc|summary") if materials_count > 0 else InlineKeyboardButton("🔄 Новый расчёт", callback_data="calc|new")],
        [InlineKeyboardButton("📞 Связаться с менеджером", callback_data="main|contacts")],
        [InlineKeyboardButton("🔙 Главное меню", callback_data="main|back")]
    ]
    return InlineKeyboardMarkup(keyboard)

# Функция расчёта для панелей
def calculate_panels(area: float, product: str, thickness: int, length: int) -> Dict[str, Any]:
    panel_area = 1.22 * (length / 1000)  # Ширина 1220 мм
    price_per_panel = WALL_PRODUCTS.get(product, {}).get(thickness, {}).get(length, 0)
    needed = math.ceil(area / panel_area)
    with_waste = math.ceil(needed * 1.1)  # +10% отходы
    waste_m2 = (with_waste * panel_area - area)
    waste_percent = (waste_m2 / area * 100) if area > 0 else 0
    total_price = with_waste * price_per_panel
    return {
        'quantity': with_waste,
        'waste_percent': round(waste_percent, 1),
        'waste_m2': round(waste_m2, 2),
        'total_price': total_price,
        'panel_area': panel_area,
        'price_per_panel': price_per_panel
    }

# Расчёт для реечных
def calculate_slats(length_mp: float, slat_type: str) -> Dict[str, Any]:
    price_per_m = SLAT_PRICES.get(slat_type, 0)
    total_price = length_mp * price_per_m
    return {'quantity': length_mp, 'total_price': total_price, 'waste_percent': 0, 'waste_m2': 0}

# Расчёт для 3D
def calculate_3d(area: float, size: str) -> Dict[str, Any]:
    panel_area = 0.72 if size == 'small' else 3.6  # 600x1200=0.72, 1200x3000=3.6
    price = THREE_D_PRICES.get(size, 0)
    needed = math.ceil(area / panel_area)
    with_waste = math.ceil(needed * 1.1)
    waste_m2 = (with_waste * panel_area - area)
    waste_percent = (waste_m2 / area * 100) if area > 0 else 0
    total_price = with_waste * price
    return {
        'quantity': with_waste,
        'waste_percent': round(waste_percent, 1),
        'waste_m2': round(waste_m2, 2),
        'total_price': total_price
    }

# Расчёт для профилей
def calculate_profiles(qty: int, thickness: int, ptype: str) -> Dict[str, Any]:
    price = PROFILE_PRICES.get(thickness, {}).get(ptype, 0)
    total_price = qty * price
    return {'quantity': qty, 'total_price': total_price, 'waste_percent': 0, 'waste_m2': 0}

# Обработчики
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    welcome_msg = random.choice(WELCOME_MESSAGES)
    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=WELCOME_PHOTO_URL,
        caption=f"Привет, {escape_markdown(user.first_name or user.username or 'друг')}!\n{welcome_msg}",
        reply_markup=build_main_menu_keyboard(user.id),
        parse_mode='Markdown'
    )
    context.chat_data.clear()

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    logger.info(f"Callback: {data}")

    parts = data.split('|')
    prefix = parts[0] if len(parts) > 0 else ''
    action = parts[1] if len(parts) > 1 else ''

    if prefix == "main":
        if action == "calc":
            await query.edit_message_text(
                "🧮 Выберите категорию для расчёта:",
                reply_markup=build_calc_category_keyboard()
            )
            context.chat_data["calc_mode"] = True
            context.chat_data["calc_phase"] = "choose_category"
            context.chat_data["materials"] = []
            return
        elif action == "catalogs":
            await query.edit_message_text(
                "📋 Выберите каталог:",
                reply_markup=build_catalog_category_keyboard()
            )
            return
        elif action == "contacts":
            contacts_text = escape_markdown("""
**Контакты ECO Стены:**
📱 +7 (978) 022-32-22
📧 info@ecosteni.ru
🕒 Пн-Пт 9:00–18:00
            """)
            await query.edit_message_text(contacts_text, reply_markup=build_contacts_keyboard(), parse_mode='Markdown')
            return
        elif action == "partner":
            await query.edit_message_text(
                "🤝 Давайте узнаем вас лучше!\n\n- Как к вам обращаться?\n(Напишите имя или ник)",
                reply_markup=build_partner_keyboard()
            )
            context.chat_data["partner_phase"] = "name"
            return
        elif action == "admin" and query.from_user.id == ADMIN_ID:
            await query.edit_message_text("⚙️ Админ-панель:", reply_markup=build_admin_keyboard())
            return
        elif action == "back":
            await query.edit_message_text(
                "🏠 Главное меню ECO Стены.",
                reply_markup=build_main_menu_keyboard(query.from_user.id)
            )
            context.chat_data.clear()
            return

    elif prefix == "calc_cat":
        if action == "walls":
            await query.edit_message_text("🧱 Выберите тип:", reply_markup=build_walls_type_keyboard())
            context.chat_data["calc_phase"] = "select_walls_type"
            return
        elif action == "profiles":
            await query.edit_message_text("🔩 Выберите по толщине:", reply_markup=build_profile_thickness_keyboard())
            context.chat_data["calc_phase"] = "select_profile_thick"
            return
        elif action == "slats":
            await query.edit_message_text("🔲 Выберите тип:", reply_markup=build_slat_type_keyboard())
            context.chat_data["calc_phase"] = "select_slat"
            return
        elif action == "3d":
            await query.edit_message_text("🎨 Выберите размер:", reply_markup=build_3d_size_keyboard())
            context.chat_data["calc_phase"] = "select_3d_size"
            return
        elif action == "stone":
            await query.edit_message_text("🪨 Гибкий камень скоро! Вернёмся к панелям.", reply_markup=build_calc_category_keyboard())
            return
        elif action == "back":
            await query.edit_message_text("🧮 Выберите категорию:", reply_markup=build_calc_category_keyboard())
            return

    elif prefix == "product":
        context.chat_data["product"] = action
        if action == "spc":
            await query.edit_message_text(
                "✏️ Опционально: Артикул? Или продолжить.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Продолжить", callback_data="ready|calc")], [InlineKeyboardButton("🔙 Назад", callback_data="calc_cat|back")]])
            )
            context.chat_data["calc_phase"] = "optional_name"
            return
        await query.edit_message_text("📏 Выберите толщину:", reply_markup=build_thickness_keyboard())
        context.chat_data["calc_phase"] = "select_thickness"
        return

    elif prefix == "thickness":
        context.chat_data["thickness"] = int(action)
        product = context.chat_data.get("product")
        await query.edit_message_text("📐 Выберите длину:", reply_markup=build_length_keyboard(product, int(action)))
        context.chat_data["calc_phase"] = "select_length"
        return

    elif prefix == "length":
        context.chat_data["length"] = int(action)
        await query.edit_message_text(
            "✏️ Опционально: Артикул? Или продолжить.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Продолжить", callback_data="ready|calc")], [InlineKeyboardButton("🔙 Назад", callback_data="thickness|back")]])
        )
        context.chat_data["calc_phase"] = "optional_name"
        return

    elif prefix == "ready" and action == "calc":
        # Переход к вопросам
        await query.edit_message_text(
            "📏 Какая ширина покрытия по стене? (м, напр. 3.5 или 3+1.2+2)",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад к выбору", callback_data="calc_cat|back")]])
        )
        context.chat_data["calc_phase"] = "input_width"
        return

    elif prefix == "profile_thick":
        context.chat_data["profile_thick"] = int(action)
        await query.edit_message_text("Выберите тип:", reply_markup=build_profile_type_keyboard(int(action)))
        context.chat_data["calc_phase"] = "select_profile_type"
        return

    elif prefix == "profile_type":
        context.chat_data["profile_type"] = action
        await query.edit_message_text(
            f"Укажите количество {action.replace('_', ' ')} шт.:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="profile_thick|back")]])
        )
        context.chat_data["calc_phase"] = "input_profile_qty"
        return

    elif prefix == "slat":
        context.chat_data["slat_type"] = action
        await query.edit_message_text(
            "📏 Укажите длину (м.п.):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="calc_cat|back")]])
        )
        context.chat_data["calc_phase"] = "input_slat_length"
        return

    elif prefix == "3d_size":
        context.chat_data["3d_size"] = action
        await query.edit_message_text(
            "✏️ Опционально: Артикул? Или продолжить.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Продолжить", callback_data="ready|calc")], [InlineKeyboardButton("🔙 Назад", callback_data="calc_cat|back")]])
        )
        context.chat_data["calc_phase"] = "optional_name"
        return

    elif prefix == "calc_type":
        context.chat_data["calc_type"] = action
        # Вычислить площадь и добавить материал
        width = context.chat_data.get("room_width", 0)
        height = context.chat_data.get("room_height", 0)
        cutouts = context.chat_data.get("cutouts", [])
        cutout_area = sum(w * h for w, h in cutouts)
        area = width * height - cutout_area
        if action == "room":
            # Добавляем недостающую высоту, но для простоты используем room_height
            pass
        # Здесь добавить расчёт в зависимости от категории
        category = context.chat_data.get("current_category", "walls")
        if category == "walls":
            product = context.chat_data.get("product")
            thickness = context.chat_data.get("thickness", 0)
            length = context.chat_data.get("length", 2440)
            calc = calculate_panels(area, product, thickness, length)
            material = {
                'type': product,
                'thickness': thickness,
                'length': length,
                'calc': calc,
                'area': area,
                'custom_name': context.chat_data.get("custom_name", "")
            }
        elif category == "slats":
            slat_type = context.chat_data.get("slat_type")
            length_mp = context.chat_data.get("slat_length", 0)
            calc = calculate_slats(length_mp, slat_type)
            material = {'type': slat_type, 'calc': calc, 'length_mp': length_mp}
        elif category == "3d":
            size = context.chat_data.get("3d_size")
            calc = calculate_3d(area, size)
            material = {'size': size, 'calc': calc, 'area': area}
        elif category == "profiles":
            thick = context.chat_data.get("profile_thick")
            ptype = context.chat_data.get("profile_type")
            qty = context.chat_data.get("profile_qty", 0)
            calc = calculate_profiles(qty, thick, ptype)
            material = {'thick': thick, 'type': ptype, 'calc': calc, 'qty': qty}
        context.chat_data["materials"].append(material)
        text = f"**Расчёт для выбранного материала:**\n- Площадь: {area:.2f} м²\n- Кол-во: {calc['quantity']} шт.\n- Отходы: {calc['waste_percent']}% ({calc['waste_m2']} м²)\n- Цена: {calc['total_price']} руб."
        await query.edit_message_text(escape_markdown(text), reply_markup=build_after_calc_keyboard(len(context.chat_data["materials"])), parse_mode='Markdown')
        context.chat_data["calc_phase"] = "after_calc"
        return

    elif prefix == "catalog":
        await query.edit_message_text(
            f"📋 Каталог {action}: Скачайте PDF [здесь]({CATALOG_PDF_URL})",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="main|back")]]),
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
        return

    elif prefix == "admin" and query.from_user.id == ADMIN_ID:
        if action == "stats":
            await query.edit_message_text("📊 Статистика: Расчётов 50, пользователей 150.", reply_markup=build_admin_keyboard())
            return
        elif action == "broadcast":
            await query.edit_message_text("📢 Введите текст рассылки:", reply_markup=build_admin_keyboard())
            context.chat_data["admin_phase"] = "broadcast"
            return
        elif action == "logs":
            await query.edit_message_text("📋 Последние логи: [пример].", reply_markup=build_admin_keyboard())
            return

    # Fallback
    await query.edit_message_text("❌ Не понял. Вернёмся в меню.", reply_markup=build_main_menu_keyboard(query.from_user.id))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    if "calc_mode" in context.chat_data and context.chat_data["calc_mode"]:
        phase = context.chat_data.get("calc_phase")
        if phase == "input_width":
            # Парсинг ширины (сумма через +)
            nums = re.findall(r'\d+\.?\d*', text.replace('+', ' '))
            width = sum(float(n) for n in nums) if nums else 0
            context.chat_data["room_width"] = width
            await update.message.reply_text(
                f"Ширина {width} м. Теперь высота помещения? (м)",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад к выбору", callback_data="calc_cat|back")]])
            )
            context.chat_data["calc_phase"] = "input_height"
            return
        elif phase == "input_height":
            height = float(re.search(r'\d+\.?\d*', text).group()) if re.search(r'\d+\.?\d*', text) else 0
            context.chat_data["room_height"] = height
            await update.message.reply_text(
                "🪟 Добавить окно? (да/нет)",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад к выбору", callback_data="calc_cat|back")]])
            )
            context.chat_data["calc_phase"] = "add_window"
            return
        elif phase == "add_window":
            if "да" in text.lower():
                await update.message.reply_text("Размер окна (шир. x выс., м, напр. 1.2x1.5)")
                context.chat_data["calc_phase"] = "input_window_size"
                return
            else:
                await update.message.reply_text("🚪 Добавить дверь? (да/нет)")
                context.chat_data["calc_phase"] = "add_door"
                return
        elif phase == "input_window_size":
            match = re.match(r'(\d+\.?\d*)\s*[xх]\s*(\d+\.?\d*)', text)
            if match:
                w, h = float(match.group(1)), float(match.group(2))
                cutouts = context.chat_data.get("cutouts", [])
                cutouts.append(('window', w * h))
                context.chat_data["cutouts"] = cutouts
            await update.message.reply_text("🪟 Ещё окно? (да/нет)")
            context.chat_data["calc_phase"] = "add_window"
            return
        # Аналогично для дверей...
        elif phase == "input_profile_qty":
            qty = int(re.search(r'\d+', text).group()) if re.search(r'\d+', text) else 0
            context.chat_data["profile_qty"] = qty
            context.chat_data["current_category"] = "profiles"
            await update.message.reply_text(
                f"Добавлено {qty} профилей. Выберите тип расчёта:",
                reply_markup=build_calc_type_keyboard()
            )
            context.chat_data["calc_phase"] = "select_calc_type"
            return
        elif phase == "input_slat_length":
            length = float(re.search(r'\d+\.?\d*', text).group()) if re.search(r'\d+\.?\d*', text) else 0
            context.chat_data["slat_length"] = length
            context.chat_data["current_category"] = "slats"
            await update.message.reply_text("Выберите тип расчёта:", reply_markup=build_calc_type_keyboard())
            context.chat_data["calc_phase"] = "select_calc_type"
            return
        elif phase == "optional_name":
            context.chat_data["custom_name"] = text
            await update.message.reply_text("Переходим к расчёту ширины...")
            # Симулируем переход
            context.chat_data["calc_phase"] = "input_width"
            await handle_message(update, context)  # Рекурсия для цепочки
            return
        elif phase == "partner_name":
            context.chat_data["partner_name"] = text
            await update.message.reply_text("Оставьте номер телефона или контакт:")
            context.chat_data["partner_phase"] = "contact"
            return
        # Добавь другие фазы партнёрства...
    else:
        # GPT для общего текста
        if OPENAI_API_KEY:
            client = OpenAI(api_key=OPENAI_API_KEY)
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": text}
                ]
            )
            reply = response.choices[0].message.content
        else:
            reply = "Извините, GPT недоступен. Выберите из меню: /menu"
        await update.message.reply_text(escape_markdown(reply), reply_markup=build_main_menu_keyboard(update.effective_user.id), parse_mode='Markdown')

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    bytes_io = BytesIO()
    await file.download_to_memory(bytes_io)
    img_base64 = base64.b64encode(bytes_io.getvalue()).decode()
    if OPENAI_API_KEY:
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Оцени фото стены: размеры, материал. Предложи расчёт ECO Стены."},
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_base64}"}}]}
            ]
        )
        reply = response.choices[0].message.content
    else:
        reply = "📸 Фото получено! Опиши размеры текстом для расчёта."
    await update.message.reply_text(escape_markdown(reply), reply_markup=build_main_menu_keyboard(update.effective_user.id), parse_mode='Markdown')

def main() -> None:
    application = Application.builder().token(TG_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", start))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logger.info("Бот запущен!")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
