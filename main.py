import logging
import os
import math
import random
import re
from typing import Dict, Any, List
from io import BytesIO
import base64
from openai import OpenAI
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# Загрузка .env
load_dotenv()

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
CONTACT_PHONE = "+7 (978) 022-32-22"
CONTACT_EMAIL = "info@ecosteni.ru"

# Системный промпт для GPT
SYSTEM_PROMPT = """
Ты эксперт по материалам ECO Стены. Отвечай дружелюбно, на русском языке, с эмодзи. 
Используй знания о WPC, SPC, реечных, 3D-панелях, профилях. Рекомендуй расчёт для точности.
Для фото: Оцени размеры стены, предложи расчёт. Если вопрос не по теме — предложи меню.
"""

# Цены (полные из таблицы)
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
    'wpc_protect': {  # WPC Бамбук с защитным слоем
        8: {2440: 16400, 2600: 17500, 2800: 18800, 3000: 20100, 3200: 21500}
    },
    'wpc_hd_protect': {  # WPC ПД с защитным слоем
        8: {2440: 18000, 2600: 19100, 2800: 20600, 3000: 22100, 3200: 23500}
    },
    'spc': {  # SPC Панель (нет толщины, ключ 0)
        0: {2440: 9500, 2600: 10100}  # Добавь больше длин по необходимости
    }
}

SLAT_PRICES = {
    'wpc': 1200,   # руб./м.п.
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

# Роли для партнёрства
PARTNER_ROLES = [
    "Дизайнер/Архитектор",
    "Прораб",
    "Застройщик",
    "Магазин/Салон"
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
    lengths = list(WALL_PRODUCTS.get(product, {}).get(thickness if thickness != 0 else 0, {}).keys())
    keyboard = [[InlineKeyboardButton(f"{length} мм", callback_data=f"length|{length}")] for length in lengths]
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="thickness|back")])
    return InlineKeyboardMarkup(keyboard)

def build_optional_name_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("Продолжить", callback_data="ready|calc")],
        [InlineKeyboardButton("Указать", callback_data="input|name")],
        [InlineKeyboardButton("🔙 Назад", callback_data="product|back")]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_profile_thickness_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("Для 5 мм", callback_data="profile_thick|5")],
        [InlineKeyboardButton("Для 8 мм", callback_data="profile_thick|8")],
        [InlineKeyboardButton("🔙 Назад", callback_data="calc_cat|back")]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_profile_type_keyboard(thickness: int) -> InlineKeyboardMarkup:
    types_map = {
        'joint': 'Стыковочный',
        'joint_wide': 'Стыковочный широкий',
        'joint_light': 'Стыковочный с подсветкой',
        'finish': 'Финишный',
        'outer_corner': 'Внешний угол',
        'inner_corner': 'Внутренний угол'
    }
    keyboard = [[InlineKeyboardButton(f"{types_map.get(t, t)}", callback_data=f"profile_type|{t}")] for t in types_map]
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
        [InlineKeyboardButton("📞 Позвонить", url=f"tel:{CONTACT_PHONE}")],
        [InlineKeyboardButton("✉️ Написать", url=f"mailto:{CONTACT_EMAIL}")],
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

def build_partner_role_keyboard() -> InlineKeyboardMarkup:
    keyboard = [[InlineKeyboardButton(role, callback_data=f"partner_role|{role.lower().replace('/', '_').replace(' ', '_')}")] for role in PARTNER_ROLES]
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="main|back")])
    return InlineKeyboardMarkup(keyboard)

# Функции расчёта
def calculate_panels(area: float, product: str, thickness: int, length: int) -> Dict[str, Any]:
    panel_area = 1.22 * (length / 1000)  # Ширина 1220 мм
    price_per_panel = WALL_PRODUCTS.get(product, {}).get(thickness if thickness != 0 else 0, {}).get(length, 0)
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
        'panel_area': round(panel_area, 3),
        'price_per_panel': price_per_panel
    }

def calculate_slats(length_mp: float, slat_type: str) -> Dict[str, Any]:
    price_per_m = SLAT_PRICES.get(slat_type, 0)
    total_price = length_mp * price_per_m
    return {'quantity': length_mp, 'total_price': total_price, 'waste_percent': 0, 'waste_m2': 0}

def calculate_3d(area: float, size: str) -> Dict[str, Any]:
    panel_area = 0.72 if size == 'small' else 3.6
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

def calculate_profiles(qty: int, thickness: int, ptype: str) -> Dict[str, Any]:
    price = PROFILE_PRICES.get(thickness, {}).get(ptype, 0)
    total_price = qty * price
    return {'quantity': qty, 'total_price': total_price, 'waste_percent': 0, 'waste_m2': 0}

def format_material_summary(materials: List[Dict]) -> str:
    if not materials:
        return "Нет материалов для итога."
    total_price = 0
    summary_lines = []
    for mat in materials:
        if 'type' in mat and mat['type'].startswith('wpc') or mat['type'] == 'spc':
            line = f"{mat.get('custom_name', mat['type'].replace('_', ' ').title())} ({mat['thickness']} мм, {mat['length']} мм): {mat['calc']['quantity']} шт., отходы {mat['calc']['waste_percent']}%, {mat['calc']['total_price']} руб."
        elif mat.get('current_category') == 'slats':
            line = f"{mat['type'].title()} реечные: {mat['calc']['quantity']} м.п., {mat['calc']['total_price']} руб."
        elif mat.get('current_category') == '3d':
            line = f"3D {mat['size']}: {mat['calc']['quantity']} шт., отходы {mat['calc']['waste_percent']}%, {mat['calc']['total_price']} руб."
        elif mat.get('current_category') == 'profiles':
            line = f"Профиль {mat['type'].replace('_', ' ').title()} ({mat['thick']} мм): {mat['calc']['quantity']} шт., {mat['calc']['total_price']} руб."
        summary_lines.append(line)
        total_price += mat['calc']['total_price']
    summary = "\n".join(summary_lines) + f"\n\n**Итого: {total_price} руб.**"
    return summary

# Обработчики
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    welcome_msg = random.choice(WELCOME_MESSAGES)
    caption = f"Привет, {escape_markdown(user.first_name or user.username or 'друг')}!\nЯ бот ECO Стены. Помогу рассчитать панели по твоим размерам. Экологично и стильно! 👋\n\n{welcome_msg}"
    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=WELCOME_PHOTO_URL,
        caption=caption,
        reply_markup=build_main_menu_keyboard(user.id),
        parse_mode='Markdown'
    )
    context.chat_data.clear()

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    logger.info(f"Callback: {data}, user: {query.from_user.id}")

    parts = data.split('|')
    prefix = parts[0] if len(parts) > 0 else ''
    action = parts[1] if len(parts) > 1 else ''

    chat_data = context.chat_data

    if prefix == "main":
        if action == "calc":
            await query.edit_message_text("🧮 Выберите категорию для расчёта:", reply_markup=build_calc_category_keyboard())
            chat_data["calc_mode"] = True
            chat_data["calc_phase"] = "choose_category"
            chat_data.setdefault("materials", [])
            chat_data.setdefault("cutouts", [])
            return
        elif action == "catalogs":
            await query.edit_message_text("📋 Выберите каталог:", reply_markup=build_catalog_category_keyboard())
            return
        elif action == "contacts":
            contacts_text = escape_markdown(f"""
**Контакты ECO Стены:**
📱 {CONTACT_PHONE}
📧 {CONTACT_EMAIL}
🕒 Пн-Пт 9:00–18:00
            """)
            await query.edit_message_text(contacts_text, reply_markup=build_contacts_keyboard(), parse_mode='Markdown')
            return
        elif action == "partner":
            await query.edit_message_text("🤝 Давайте узнаем вас лучше!\n\nКак к вам обращаться? (Напишите имя или ник)", reply_markup=build_partner_keyboard())
            chat_data["partner_phase"] = "name"
            return
        elif action == "admin" and query.from_user.id == ADMIN_ID:
            await query.edit_message_text("⚙️ Админ-панель:", reply_markup=build_admin_keyboard())
            return
        elif action == "back":
            await query.edit_message_text("🏠 Главное меню ECO Стены.", reply_markup=build_main_menu_keyboard(query.from_user.id))
            chat_data.clear()
            return
        elif action == "calc" and "summary" in action:  # calc|summary
            summary = format_material_summary(chat_data.get("materials", []))
            await query.edit_message_text(f"**Итоговый расчёт:**\n{escape_markdown(summary)}", reply_markup=build_after_calc_keyboard(len(chat_data.get("materials", []))), parse_mode='Markdown')
            return

    elif prefix == "calc_cat":
        chat_data["current_category"] = action
        if action == "walls":
            await query.edit_message_text("🧱 Выберите тип (цены в таблице):", reply_markup=build_walls_type_keyboard())
            chat_data["calc_phase"] = "select_walls_type"
        elif action == "profiles":
            await query.edit_message_text("🔩 Выберите по толщине:", reply_markup=build_profile_thickness_keyboard())
            chat_data["calc_phase"] = "select_profile_thick"
        elif action == "slats":
            await query.edit_message_text("🔲 Выберите тип (цены за м.п.):", reply_markup=build_slat_type_keyboard())
            chat_data["calc_phase"] = "select_slat"
        elif action == "3d":
            await query.edit_message_text("🎨 Выберите размер (цены выше):", reply_markup=build_3d_size_keyboard())
            chat_data["calc_phase"] = "select_3d_size"
        elif action == "stone":
            await query.edit_message_text("🪨 Гибкий камень скоро! Вернёмся к панелям.", reply_markup=build_calc_category_keyboard())
        elif action == "back":
            await query.edit_message_text("🧮 Выберите категорию:", reply_markup=build_calc_category_keyboard())
        return

    elif prefix == "product":
        chat_data["product"] = action
        if action == "spc":
            thickness = 0
            chat_data["thickness"] = thickness
            await query.edit_message_text("✏️ Опционально: Артикул или название? Или ‘Продолжить’.", reply_markup=build_optional_name_keyboard())
            chat_data["calc_phase"] = "optional_name"
        else:
            await query.edit_message_text("📏 Выберите толщину:", reply_markup=build_thickness_keyboard())
            chat_data["calc_phase"] = "select_thickness"
        return

    elif prefix == "thickness":
        chat_data["thickness"] = int(action)
        product = chat_data.get("product")
        await query.edit_message_text("📐 Выберите длину:", reply_markup=build_length_keyboard(product, int(action)))
        chat_data["calc_phase"] = "select_length"
        return

    elif prefix == "length":
        chat_data["length"] = int(action)
        await query.edit_message_text("✏️ Опционально: Артикул или название? Или ‘Продолжить’.", reply_markup=build_optional_name_keyboard())
        chat_data["calc_phase"] = "optional_name"
        return

    elif prefix == "ready" and action == "calc":
        await query.edit_message_text("📏 Какая ширина покрытия по стене? (м, напр. 3.5 или 3+1.2+2)", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад к выбору", callback_data="calc_cat|back")]]))
        chat_data["calc_phase"] = "input_width"
        return

    elif prefix == "input" and action == "name":
        await query.edit_message_text("Введите артикул или название:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="product|back")]]))
        chat_data["calc_phase"] = "input_name"
        return

    elif prefix == "profile_thick":
        chat_data["profile_thick"] = int(action)
        await query.edit_message_text("Выберите тип (цены от 1350 руб.):", reply_markup=build_profile_type_keyboard(int(action)))
        chat_data["calc_phase"] = "select_profile_type"
        return

    elif prefix == "profile_type":
        chat_data["profile_type"] = action
        await query.edit_message_text(f"Укажите количество {action.replace('_', ' ')} шт.:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="profile_thick|back")]]))
        chat_data["calc_phase"] = "input_profile_qty"
        return

    elif prefix == "slat":
        chat_data["slat_type"] = action
        await query.edit_message_text("📏 Укажите длину (м.п.):", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="calc_cat|back")]]))
        chat_data["calc_phase"] = "input_slat_length"
        return

    elif prefix == "3d_size":
        chat_data["3d_size"] = action
        await query.edit_message_text("✏️ Опционально: Артикул или название? Или ‘Продолжить’.", reply_markup=build_optional_name_keyboard())
        chat_data["calc_phase"] = "optional_name"
        return

    elif prefix == "calc_type":
        chat_data["calc_type"] = action
        # Вычисление площади
        width = chat_data.get("room_width", 0)
        height = chat_data.get("room_height", 0)
        cutout_area = sum(w * h for _, w, h in chat_data.get("cutouts", []))
        area = width * height - cutout_area
        category = chat_data.get("current_category", "walls")
        chat_data["area"] = area
        if category == "walls":
            product = chat_data.get("product")
            thickness = chat_data.get("thickness", 0)
            length = chat_data.get("length", 2440)
            calc = calculate_panels(area, product, thickness, length)
            material = {
                'category': category,
                'product': product,
                'thickness': thickness,
                'length': length,
                'calc': calc,
                'area': area,
                'custom_name': chat_data.get("custom_name", f"{product.replace('_', ' ').title()}")
            }
        elif category == "profiles":
            thick = chat_data.get("profile_thick")
            ptype = chat_data.get("profile_type")
            qty = chat_data.get("profile_qty", 0)
            calc = calculate_profiles(qty, thick, ptype)
            material = {'category': category, 'thick': thick, 'ptype': ptype, 'qty': qty, 'calc': calc}
        elif category == "slats":
            slat_type = chat_data.get("slat_type")
            length_mp = chat_data.get("slat_length", 0)
            calc = calculate_slats(length_mp, slat_type)
            material = {'category': category, 'slat_type': slat_type, 'length_mp': length_mp, 'calc': calc}
        elif category == "3d":
            size = chat_data.get("3d_size")
            calc = calculate_3d(area, size)
            material = {'category': category, 'size': size, 'calc': calc, 'area': area}
        chat_data["materials"].append(material)
        custom_name = material.get('custom_name', material.get('ptype', material.get('slat_type', material.get('size', ''))).title())
        text = f"**Расчёт для {custom_name}:**\n- Площадь: {area:.2f} м²\n- Кол-во: {calc['quantity']} {material.get('unit', 'шт.')}\n- Отходы: {calc['waste_percent']}% ({calc['waste_m2']} м²)\n- Цена: {calc['total_price']} руб."
        await query.edit_message_text(escape_markdown(text), reply_markup=build_after_calc_keyboard(len(chat_data["materials"])), parse_mode='Markdown')
        chat_data["calc_phase"] = "after_calc"
        return

    elif prefix == "catalog":
        await query.edit_message_text(f"📋 Каталог {action.title()}: Скачайте PDF [здесь]({CATALOG_PDF_URL})", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="main|back")]]), parse_mode='Markdown', disable_web_page_preview=True)
        return

    elif prefix == "partner_role":
        chat_data["partner_role"] = action.replace('_', ' ').title()
        # Собрать данные и отправить админу
        partner_data = {
            'name': chat_data.get("partner_name", ""),
            'contact': chat_data.get("partner_contact", ""),
            'city': chat_data.get("partner_city", ""),
            'company': chat_data.get("partner_company", ""),
            'site': chat_data.get("partner_site", ""),
            'role': chat_data["partner_role"]
        }
        summary = f"Новый партнёр:\nИмя: {partner_data['name']}\nКонтакт: {partner_data['contact']}\nГород: {partner_data['city']}\nКомпания: {partner_data['company']}\nСайт: {partner_data['site']}\nРоль: {partner_data['role']}"
        await context.bot.send_message(ADMIN_ID, summary)
        await query.edit_message_text("Спасибо! Передал менеджеру. Позвоним в день. 📞", reply_markup=build_main_menu_keyboard(query.from_user.id))
        chat_data.clear()
        return

    elif prefix == "admin" and query.from_user.id == ADMIN_ID:
        if action == "stats":
            await query.edit_message_text("📊 Статистика: Расчётов: 50, пользователей: 150.", reply_markup=build_admin_keyboard())
        elif action == "broadcast":
            await query.edit_message_text("📢 Введите текст рассылки:", reply_markup=build_admin_keyboard())
            chat_data["admin_phase"] = "broadcast_text"
        elif action == "logs":
            await query.edit_message_text("📋 Последние логи: [пример логов].", reply_markup=build_admin_keyboard())
        return

    # Fallback
    await query.edit_message_text("❌ Не понял. Вернёмся в меню.", reply_markup=build_main_menu_keyboard(query.from_user.id))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip().lower()
    chat_data = context.chat_data

    if chat_data.get("calc_mode"):
        phase = chat_data.get("calc_phase")
        if phase == "input_width":
            nums = re.findall(r'\d+\.?\d*', text.replace('+', ' '))
            width = sum(float(n) for n in nums) if nums else 0
            chat_data["room_width"] = width
            await update.message.reply_text(f"Ширина {width} м. 📐 Высота помещения? (м)", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад к выбору", callback_data="calc_cat|back")]]))
            chat_data["calc_phase"] = "input_height"
        elif phase == "input_height":
            height_match = re.search(r'\d+\.?\d*', text)
            height = float(height_match.group()) if height_match else 0
            chat_data["room_height"] = height
            await update.message.reply_text("🪟 Добавить окно? (да/нет)", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад к выбору", callback_data="calc_cat|back")]]))
            chat_data["calc_phase"] = "add_window"
        elif phase == "add_window":
            if 'да' in text:
                await update.message.reply_text("Размер окна (шир. x выс., м, напр. 1.2x1.5)", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад к выбору", callback_data="calc_cat|back")]]))
                chat_data["calc_phase"] = "input_window_size"
                chat_data["current_cutout"] = "window"
            else:
                await update.message.reply_text("🚪 Добавить дверь? (да/нет)", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад к выбору", callback_data="calc_cat|back")]]))
                chat_data["calc_phase"] = "add_door"
        elif phase == "input_window_size" or phase == "input_door_size":
            match = re.match(r'(\d+\.?\d*)\s*[xх]\s*(\d+\.?\d*)', text, re.IGNORECASE)
            if match:
                w, h = float(match.group(1)), float(match.group(2))
                cutouts = chat_data.get("cutouts", [])
                cutouts.append((chat_data.get("current_cutout", "window"), w, h))
                chat_data["cutouts"] = cutouts
            next_phase = "add_window" if chat_data.get("current_cutout") == "window" else "add_door_again"
            await update.message.reply_text(f"🪟 Ещё {chat_data.get('current_cutout', 'окно')}? (да/нет)")
            chat_data["calc_phase"] = next_phase
        elif phase == "add_door":
            if 'да' in text:
                await update.message.reply_text("Размер двери (шир. x выс., м)", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад к выбору", callback_data="calc_cat|back")]]))
                chat_data["calc_phase"] = "input_door_size"
                chat_data["current_cutout"] = "door"
            else:
                await update.message.reply_text("Выберите тип расчёта:", reply_markup=build_calc_type_keyboard())
                chat_data["calc_phase"] = "select_calc_type"
        elif phase == "input_profile_qty":
            qty_match = re.search(r'\d+', text)
            qty = int(qty_match.group()) if qty_match else 0
            chat_data["profile_qty"] = qty
            await update.message.reply_text("Выберите тип расчёта:", reply_markup=build_calc_type_keyboard())
            chat_data["calc_phase"] = "select_calc_type"
        elif phase == "input_slat_length":
            length_match = re.search(r'\d+\.?\d*', text)
            length = float(length_match.group()) if length_match else 0
            chat_data["slat_length"] = length
            await update.message.reply_text("Выберите тип расчёта:", reply_markup=build_calc_type_keyboard())
            chat_data["calc_phase"] = "select_calc_type"
        elif phase == "input_name":
            chat_data["custom_name"] = text
            await update.message.reply_text("Переходим к расчёту ширины...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад к выбору", callback_data="calc_cat|back")]]))
            chat_data["calc_phase"] = "input_width"
        elif phase == "partner_name":
            chat_data["partner_name"] = text
            await update.message.reply_text("Оставьте номер телефона или контакт:", reply_markup=build_partner_keyboard())
            chat_data["partner_phase"] = "contact"
        elif phase == "partner_contact":
            chat_data["partner_contact"] = text
            await update.message.reply_text("В каком вы городе?", reply_markup=build_partner_keyboard())
            chat_data["partner_phase"] = "city"
        elif phase == "partner_city":
            chat_data["partner_city"] = text
            await update.message.reply_text("Как называется ваша компания (если есть)?", reply_markup=build_partner_keyboard())
            chat_data["partner_phase"] = "company"
        elif phase == "partner_company":
            chat_data["partner_company"] = text
            await update.message.reply_text("Есть ли у вас сайт или страница в соцсетях? Если да — отправьте ссылку, если нет — напишите «нет».")
            chat_data["partner_phase"] = "site"
        elif phase == "partner_site":
            chat_data["partner_site"] = text if text != "нет" else "Нет"
            await update.message.reply_text("Кем вы являетесь? Выберите вариант:", reply_markup=build_partner_role_keyboard())
            chat_data["partner_phase"] = "role"
        elif phase == "admin_phase" == "broadcast_text" and query.from_user.id == ADMIN_ID:
            # Отправка рассылки (упрощённо, для всех чатов — в реале используй storage)
            await update.message.reply_text("Рассылка отправлена! (симуляция)")
            chat_data.pop("admin_phase", None)
        return
    else:
        # GPT для общего текста
        if OPENAI_API_KEY:
            try:
                client = OpenAI(api_key=OPENAI_API_KEY)
                response = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": text}
                    ]
                )
                reply = response.choices[0].message.content
            except Exception as e:
                logger.error(f"GPT error: {e}")
                reply = "Извините, ошибка GPT. Выберите из меню: /menu"
        else:
            reply = "Отличный вопрос! Для WPC (от 10500 руб.) подойдёт... Давай рассчитаем? /menu"
        await update.message.reply_text(escape_markdown(reply), reply_markup=build_main_menu_keyboard(update.effective_user.id), parse_mode='Markdown')

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    bytes_io = BytesIO()
    await file.download_to_memory(bytes_io)
    img_base64 = base64.b64encode(bytes_io.getvalue()).decode()
    if OPENAI_API_KEY:
        try:
            client = OpenAI(api_key=OPENAI_API_KEY)
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Оцени фото стены: размеры, материал. Предложи расчёт ECO Стены с отходами 10%."},
                    {"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_base64}"}}]}
                ]
            )
            reply = response.choices[0].message.content
        except Exception as e:
            logger.error(f"GPT photo error: {e}")
            reply = "📸 Фото получено! Опиши размеры текстом для расчёта."
    else:
        reply = "📸 Вижу стену ~3x2.5 м. Подтверди для точного счёта (с отходами 10%)."
    await update.message.reply_text(escape_markdown(reply), reply_markup=build_main_menu_keyboard(update.effective_user.id), parse_mode='Markdown')

def main() -> None:
    if not TG_BOT_TOKEN:
        logger.error("TG_BOT_TOKEN not set!")
        return
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
