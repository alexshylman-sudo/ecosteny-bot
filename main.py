import asyncio
import base64
from io import BytesIO
import json
import os
import random
from datetime import datetime, timezone

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


import sys
from telegram import __version__ as TG_VER

print("### PYTHON VERSION ON RENDER:", sys.version)
print("### python-telegram-bot VERSION ON RENDER:", TG_VER)


# ============================
#   НАСТРОЙКИ (через .env)
# ============================

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
if not TG_BOT_TOKEN:
    raise ValueError("Установите TG_BOT_TOKEN в .env!")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

WELCOME_PHOTO_URL = "https://ecosteni.ru/wp-content/uploads/2025/11/qncccaze.jpg"
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

app = Flask(__name__)

# Создаём приложение Telegram
tg_application = Application.builder().token(TG_BOT_TOKEN).build()


# ============================
#   КЛАВИАТУРЫ
# ============================

def build_main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Рассчитать материалы", callback_data="main|calc")],
        [InlineKeyboardButton("Информация", callback_data="main|info")],
        [InlineKeyboardButton("Получить каталоги", callback_data="main|catalogs")],
        [InlineKeyboardButton("Получить презентацию", callback_data="main|presentation")],
        [InlineKeyboardButton("Контактная информация", callback_data="main|contacts")],
        [InlineKeyboardButton("Хочу стать партнёром", callback_data="main|partner")],
    ])


def build_back_row() -> list[list[InlineKeyboardButton]]:
    return [[InlineKeyboardButton("Назад в меню", callback_data="ui|back_main")]]


def build_calc_category_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("1. Стеновые панели", callback_data="calc_cat|walls")],
        [InlineKeyboardButton("2. Реечные панели", callback_data="calc_cat|slats")],
        [InlineKeyboardButton("3. 3D панели", callback_data="calc_cat|3d")],
    ]
    rows += build_back_row()
    return InlineKeyboardMarkup(rows)


def build_wall_product_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for code, title in PRODUCT_CODES.items():
        buttons.append([InlineKeyboardButton(text=title, callback_data=f"product|{code}")])
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
    # Одна кнопка "Я не знаю → ДАЛЬШЕ", без возвратов в меню и без выбора режима
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Я не знаю → ДАЛЬШЕ", callback_data="after_name|skip")]
    ])



def build_thickness_keyboard(product_code: str) -> InlineKeyboardMarkup:
    title = PRODUCT_CODES[product_code]
    thicknesses = WALL_PRODUCTS.get(title, {})
    rows = []
    row = []
    for thickness in sorted(thicknesses.keys()):
        row.append(InlineKeyboardButton(
            text=f"{thickness} мм",
            callback_data=f"thickness|{product_code}|{thickness}",
        ))
    if row:
        rows.append(row)
    rows += build_back_row()
    return InlineKeyboardMarkup(rows)


def build_height_keyboard(product_code: str, thickness: int) -> InlineKeyboardMarkup:
    title = PRODUCT_CODES[product_code]
    heights = sorted(WALL_PRODUCTS[title][thickness]["panels"].keys())
    rows = []
    row = []
    for h in heights:
        row.append(InlineKeyboardButton(
            text=f"{h} мм",
            callback_data=f"height|{product_code}|{thickness}|{h}",
        ))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows += build_back_row()
    return InlineKeyboardMarkup(rows)


def build_add_more_materials_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("Добавить ещё материалы", callback_data="calc_more|yes")],
        [InlineKeyboardButton("Перейти к расчёту", callback_data="calc_more|no")],
    ]
    rows += build_back_row()
    return InlineKeyboardMarkup(rows)


def build_slats_category_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("WPC реечная панель", callback_data="slats_type|wpc")],
        [InlineKeyboardButton("Деревянная панель", callback_data="slats_type|wood")],
    ]
    rows += build_back_row()
    return InlineKeyboardMarkup(rows)


def build_wpc_slats_name_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("Название 1", callback_data="slats_wpc_name|name1")],
        [InlineKeyboardButton("Название 2", callback_data="slats_wpc_name|name2")],
        [InlineKeyboardButton("Название 3", callback_data="slats_wpc_name|name3")],
    ]
    rows += build_back_row()
    return InlineKeyboardMarkup(rows)


def build_3d_variant_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("600 × 1200 мм", callback_data="3d_variant|var1")],
        [InlineKeyboardButton("1200 × 3000 мм", callback_data="3d_variant|var2")],
    ]
    rows += build_back_row()
    return InlineKeyboardMarkup(rows)


def build_height_mode_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("Рассчитать по высоте материала", callback_data="height_mode|material")],
        [InlineKeyboardButton("Расчёт по высоте помещения", callback_data="height_mode|room")],
    ]
    rows += build_back_row()
    return InlineKeyboardMarkup(rows)


def build_info_category_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("1. Стеновые панели", callback_data="info_cat|walls")],
        [InlineKeyboardButton("2. Реечные панели", callback_data="info_cat|slats")],
        [InlineKeyboardButton("3. 3D панели (скалы)", callback_data="info_cat|3d")],
        [InlineKeyboardButton("4. Гибкая керамика", callback_data="info_cat|flex")],
        [InlineKeyboardButton("5. Доставка и гарантия", callback_data="info_cat|delivery")],
    ]
    rows += build_back_row()
    return InlineKeyboardMarkup(rows)


def build_catalog_category_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("1. Стеновые панели", callback_data="cat_cat|walls")],
        [InlineKeyboardButton("2. 3D панели (скалы)", callback_data="cat_cat|3d")],
        [InlineKeyboardButton("3. Реечные панели", callback_data="cat_cat|slats")],
        [InlineKeyboardButton("4. Гибкая керамика", callback_data="cat_cat|flex")],
        [InlineKeyboardButton("5. Профили и сопутствующие материалы", callback_data="cat_cat|profiles")],
    ]
    rows += build_back_row()
    return InlineKeyboardMarkup(rows)


def build_partner_role_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("1. Дизайнер / Архитектор", callback_data="partner_role|designer")],
        [InlineKeyboardButton("2. Магазин / Салон", callback_data="partner_role|shop")],
        [InlineKeyboardButton("3. Застройщик", callback_data="partner_role|developer")],
        [InlineKeyboardButton("4. Прораб", callback_data="partner_role|foreman")],
    ]
    rows += build_back_row()
    return InlineKeyboardMarkup(rows)


def build_contacts_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("Сайт ECO Стены", url="https://ecosteni.ru/")],
        [InlineKeyboardButton("Telegram-канал", url="https://t.me/ecosteni")],
        [InlineKeyboardButton("Instagram", url="https://www.instagram.com/schulmann_alex/")],
        [InlineKeyboardButton("Pinterest", url="https://ru.pinterest.com/3designservice/")],
        [InlineKeyboardButton("YouTube", url="https://www.youtube.com/@GRAD_music_videos")],
    ]
    rows += build_back_row()
    return InlineKeyboardMarkup(rows)


def format_wall_catalog() -> str:
    lines = []
    for title, thicknesses in WALL_PRODUCTS.items():
        lines.append(f"{title}:")
        for thickness, info in thicknesses.items():
            lines.append(f"  Толщина {thickness} мм, ширина листа {info['width_mm']} мм:")
            for h, pdata in info["panels"].items():
                lines.append(
                    f"    Высота {h} мм — {pdata['area_m2']} м², ~{pdata['price_rub']} ₽ за панель"
                )
        lines.append("")
    return "\n".join(lines)


def get_calc_selection_block(context: ContextTypes.DEFAULT_TYPE) -> str:
    items = context.chat_data.get("calc_items", [])
    if not items:
        return ""
    lines = ["Клиент выбрал следующие материалы для расчёта:"]
    for idx, it in enumerate(items, start=1):
        cat = it.get("category")
        custom = it.get("custom_name")
        if cat == "walls":
            base_title = PRODUCT_CODES.get(it["product_code"], it["product_code"])
            title = base_title + (f" (название клиента: {custom})" if custom else "")
            lines.append(f"{idx}. Стеновые панели — {title}, {it['thickness']} мм, высота {it['height']} мм")
        elif cat == "slats":
            base = it.get("base_type")
            base_title = "WPC реечная панель" if base == "wpc" else "Деревянная панель"
            title = base_title + (f" (название клиента: {custom})" if custom else "")
            lines.append(f"{idx}. Реечные панели — {title}")
        elif cat == "3d":
            vcode = it.get("variant_code")
            size = "600×1200 мм" if vcode == "var1" else "1200×3000 мм"
            base_title = f"3D панели {size}"
            title = base_title + (f" (название клиента: {custom})" if custom else "")
            lines.append(f"{idx}. {title}")
        else:
            title = custom or (cat or "Неизвестный материал")
            lines.append(f"{idx}. {title}")
    lines.append("")
    return "\n".join(lines)

# ============================
#   ПРИВЕТСТВИЕ
# ============================

async def send_greeting_with_media(message_obj, context: ContextTypes.DEFAULT_TYPE):
    user = message_obj.from_user
    raw_name = (user.first_name or getattr(user, "full_name", None) or user.username or "друг")
    name = raw_name.lstrip("@").strip()
    context.chat_data["user_name"] = name

    greeting_text = random.choice(GREETING_PHRASES).format(name=name)
    if WELCOME_GIF_URL:
        try:
            await message_obj.reply_animation(animation=WELCOME_GIF_URL, caption=None)
        except Exception as e:
            print("Ошибка отправки GIF:", repr(e))
    try:
        await message_obj.reply_photo(photo=WELCOME_PHOTO_URL, caption=greeting_text)
    except Exception as e:
        print("Ошибка отправки фото:", repr(e))

# ============================
#   SMALLTALK
# ============================

async def handle_smalltalk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text or ""
    if not OPENAI_API_KEY:
        await update.message.reply_text(
            "Сейчас я не могу обратиться к модели, но могу подсказать по продукции ECO Стены. "
            "Спросите, например, про WPC панели, рейки или 3D панели."
        )
        return

    history = context.chat_data.get("chat_history", [])
    messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
    if history:
        messages.extend(history[-10:])
    messages.append({"role": "user", "content": user_text})

    payload = {
        "model": "gpt-4o-mini",  # Исправлено
        "messages": messages,
        "temperature": 0.5,
    }

    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        print("SMALLTALK RAW RESPONSE:", resp.text)
        resp.raise_for_status()
        data = resp.json()
        answer = data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print("SMALLTALK ERROR:", repr(e))
        answer = (
            "Сейчас у меня не получается обратиться к модели, "
            "но я всё равно могу подсказать по нашим материалам — задайте вопрос про панели или интерьер."
        )

    await update.message.reply_text(answer)
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": answer})
    context.chat_data["chat_history"] = history[-20:]

# ============================
#   КОМАНДЫ
# ============================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data.clear()
    context.chat_data["started"] = True
    context.chat_data["main_mode"] = None
    context.chat_data["calc_phase"] = None
    context.chat_data["materials_locked"] = False
    context.chat_data["await_custom_name_index"] = None

    await send_greeting_with_media(update.message, context)
    await update.message.reply_text("Чем могу помочь? 👇", reply_markup=build_main_menu_keyboard())


async def catalog_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Фрагмент каталога стеновых WPC панелей:\n\n" + format_wall_catalog())


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data["started"] = True
    context.chat_data["main_mode"] = None
    context.chat_data["calc_phase"] = None
    context.chat_data["materials_locked"] = False
    await update.message.reply_text("Чем могу помочь?", reply_markup=build_main_menu_keyboard())


async def reply_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("Эта команда доступна только администратору.")
        return
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Использование:\n/reply <ID_клиента> текст сообщения\n\n"
            "Например:\n/reply 123456789 Здравствуйте! Я менеджер ECO Стены 🙂"
        )
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID клиента должен быть числом. Пример: /reply 123456789 Ваш текст")
        return

    text = " ".join(context.args[1:]).strip()
    if not text:
        await update.message.reply_text("Нужно указать текст сообщения после ID клиента.")
        return

    try:
        await tg_application.bot.send_message(
            chat_id=target_id,
            text="Сообщение от менеджера ECO Стены:\n\n" + text
        )
        await update.message.reply_text("Сообщение отправлено клиенту ✅")
    except Exception as e:
        print("ERROR sending admin reply:", repr(e))
        await update.message.reply_text(
            "Не удалось отправить сообщение клиенту. Проверьте ID или попробуйте позже."
        )

# ============================
#   ПАРТНЁРКА
# ============================

async def handle_partner_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    state = context.chat_data.get("partner_state")

    if state == "ask_name":
        context.chat_data["partner_name"] = text.strip()
        context.chat_data["partner_state"] = "ask_phone"
        await update.message.reply_text("Оставьте, пожалуйста, номер телефона:")
        return

    if state == "ask_phone":
        context.chat_data["partner_phone"] = text.strip()
        context.chat_data["partner_state"] = "ask_city"
        await update.message.reply_text("В каком вы городе?")
        return

    if state == "ask_city":
        context.chat_data["partner_city"] = text.strip()
        context.chat_data["partner_state"] = "ask_company"
        await update.message.reply_text("Как называется ваша компания (если есть)?")
        return

    if state == "ask_company":
        context.chat_data["partner_company"] = text.strip()
        context.chat_data["partner_state"] = "ask_website"
        await update.message.reply_text(
            "Есть ли у вас сайт или страница в соцсетях? Если да — отправьте ссылку, "
            "если нет — напишите «нет»."
        )
        return

    if state == "ask_website":
        context.chat_data["partner_website"] = text.strip()
        context.chat_data["partner_state"] = "ask_role"
        await update.message.reply_text(
            "Кем вы являетесь?\nВыберите вариант:",
            reply_markup=build_partner_role_keyboard(),
        )
        return

    if state == "ask_projects":
        context.chat_data["partner_projects"] = text.strip()
        context.chat_data["partner_state"] = "ask_contact_pref"
        await update.message.reply_text(
            "Как удобнее с вами связаться? (например: звонок, WhatsApp, Telegram, e-mail)"
        )
        return

    if state == "ask_contact_pref":
        context.chat_data["partner_contact_pref"] = text.strip()
        context.chat_data["partner_state"] = "done"

        name = context.chat_data.get("partner_name", "-")
        phone = context.chat_data.get("partner_phone", "-")
        city = context.chat_data.get("partner_city", "-")
        company = context.chat_data.get("partner_company", "-")
        website = context.chat_data.get("partner_website", "-")
        role = context.chat_data.get("partner_role", "-")
        projects = context.chat_data.get("partner_projects", "-")
        contact_pref = context.chat_data.get("partner_contact_pref", "-")

        role_map = {
            "designer": "Дизайнер / Архитектор",
            "shop": "Магазин / Салон",
            "developer": "Застройщик",
            "foreman": "Прораб",
        }
        role_human = role_map.get(role, role)

        msg = (
            "Новая заявка партнёра:\n\n"
            f"Имя: {name}\n"
            f"Телефон: {phone}\n"
            f"Город: {city}\n"
            f"Компания: {company}\n"
            f"Сайт / соцсети: {website}\n"
            f"Роль: {role_human}\n"
            f"Объекты / формат работы: {projects}\n"
            f"Предпочитаемый способ связи: {contact_pref}\n"
            f"Telegram user: @{update.effective_user.username or 'нет'} (ID: {update.effective_user.id})"
        )

        if ADMIN_CHAT_ID:
            try:
                await tg_application.bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg)
            except Exception as e:
                print("ERROR sending partner info to admin:", repr(e))

        await update.message.reply_text(
            "Спасибо! Мы получили вашу заявку. Менеджер свяжется с вами в ближайшее время.\n\n"
            "Если хотите, можете сразу описать, какие материалы и по каким объектам вас интересуют."
        )
        await update.message.reply_text("Чем могу помочь дальше?", reply_markup=build_main_menu_keyboard())
        context.chat_data["main_mode"] = None
        context.chat_data["partner_state"] = None
        return

    # fallback
    await update.message.reply_text("Кажется, мы немного сбились. Давайте начнём анкету заново.")
    context.chat_data["main_mode"] = "partner"
    context.chat_data["partner_state"] = "ask_name"
    await update.message.reply_text("Давайте познакомимся. Как вас зовут?")

# ============================
#   РАСЧЁТ ПО ТЕКСТУ (OpenAI)
# ============================

async def perform_text_calc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not OPENAI_API_KEY:
        await update.effective_message.reply_text(
            "Сейчас расчёт через модель недоступен (нет ключа OpenAI). "
            "Попросите менеджера сделать расчёт вручную."
        )
        return

    items = context.chat_data.get("calc_items", [])
    if not items:
        await update.effective_message.reply_text(
            "Не вижу выбранных материалов. Попробуйте заново через «Рассчитать материалы»."
        )
        return

    catalog_json = json.dumps(WALL_PRODUCTS, ensure_ascii=False)
    selection_block = get_calc_selection_block(context)

    wa = context.chat_data.get("width_answers", {}) or {}
    room_height = context.chat_data.get("room_height") or "не указано"
    height_mode = context.chat_data.get("height_mode") or "material"


    cats = [it.get("category") for it in items]
    cats_text = ", ".join(sorted(set(cats)))

    width_lines = []
    if "walls" in wa:
        width_lines.append(f"• Стеновые панели: {wa['walls']}")
    if "slats" in wa:
        width_lines.append(f"• Реечные панели: {wa['slats']}")
    if "3d" in wa:
        width_lines.append(f"• 3D панели: {wa['3d']}")
    width_block = "Клиент указал ширину зон отделки:\n" + "\n".join(width_lines) + "\n\n" if width_lines else ""

    extra_sizes = (
        "Дополнительные данные по материалам для расчёта:\n"
        f"• Реечные панели: размер 168 × 2900 × 18 мм. Цены: WPC — {SLAT_PRICES['wpc']} ₽/шт, дерево — {SLAT_PRICES['wood']} ₽/шт.\n"
        f"• 3D панели 600×1200 мм — {PANELS_3D['var1']['price_rub']} ₽/шт.\n"
        f"• 3D панели 1200×3000 мм — {PANELS_3D['var2']['price_rub']} ₽/шт.\n\n"
    )

    height_mode_text = (
        "Режим расчёта по высоте: "
        + ("ПО ВЫСОТЕ МАТЕРИАЛА — докладывать панели только до высоты панели, остаток стены не считать."
           if height_mode == "material"
           else "ПО ВЫСОТЕ ПОМЕЩЕНИЯ — докладывать панели, чтобы покрыть всю высоту помещения.")
    )

    style_block = (
        "Формат ответа:\n"
        "— НЕ используй таблицы и символы `|`.\n"
        "— Для КАЖДОЙ категории делай отдельный блок:\n"
        "   • первая строка: ________________________________ (строка из подчёркиваний, не меньше 30 символов);\n"
        "   • вторая строка — заголовок категории в формате: «***🧱 Стеновые панели***», «***🎋 Реечные панели***», «***🪨 3D панели***».\n"
        "— Далее внутри блока используй списки и эмодзи для структурирования.\n\n"
        "Интерпретация размеров от клиента:\n"
        "— если указано число с единицами (м, метр, метра, метры, мм, миллиметр и т.п.) — использовать их буквально;\n"
        "— если пользователь написал просто число < 1000 без единиц — считать, что это метры;\n"
        "— если написал число ≥ 1000 без единиц — считать, что это миллиметры.\n\n"
    )

    items_descriptions = []
    for it in items:
        cat = it.get("category")
        custom = it.get("custom_name")
        if cat == "walls":
            base_title = PRODUCT_CODES.get(it["product_code"], it["product_code"])
            title = base_title + (f" (название клиента: {custom})" if custom else "")
            items_descriptions.append(
                f"Стеновые панели: {title}, толщина {it['thickness']} мм, высота листа {it['height']} мм."
            )
        elif cat == "slats":
            base = it.get("base_type")
            base_title = "WPC реечная панель" if base == "wpc" else "Деревянная панель"
            title = base_title + (f" (название клиента: {custom})" if custom else "")
            items_descriptions.append(f"Реечные панели: {title}, размер 168×2900×18 мм.")
        elif cat == "3d":
            vcode = it.get("variant_code")
            size = "600×1200 мм" if vcode == "var1" else "1200×3000 мм"
            base_title = f"3D панели {size}"
            title = base_title + (f" (название клиента: {custom})" if custom else "")
            items_descriptions.append(f"{title}.")
        else:
            if custom:
                items_descriptions.append(f"Материал: {custom}.")


    items_block = "Подробно по выбранным материалам:\n" + "\n".join("• " + d for d in items_descriptions) + "\n\n"

    user_payload = (
        f"{style_block}"
        f"Клиент выбрал материалы для расчёта (категории: {cats_text}).\n\n"
        f"{selection_block}"
        f"{items_block}"
        f"{width_block}"
        f"Высота помещения (по ответу клиента): {room_height}\n"
        f"{height_mode_text}\n\n"
        "Ниже передан JSON с каталогом стеновых WPC панелей (размеры и цены):\n"
        f"{catalog_json}\n\n"
        f"{extra_sizes}"
    "Задача:\n"
    "0) Считай ТОЛЬКО те категории, которые реально выбрал клиент. "
    "Если какая-то категория (стеновые, реечные, 3D) не указана в списке выбранных категорий и не описана в списке материалов, "
    "НЕ упоминай её и НЕ считай вообще.\n"
    "1) Для КАЖДОЙ выбранной категории материалов рассчитать примерное количество панелей и ориентировочную стоимость.\n"
    "2) Обязательно выводи категории отдельно, как отдельные блоки (с разделителем и заголовком, как описано в формате ответа).\n"
    "3) Учитывай выбранный режим по высоте (по высоте материала ИЛИ по высоте помещения).\n"
    "4) ОБЯЗАТЕЛЬНО для каждой категории покажи ОТХОДЫ: сколько панели идёт в подрезку/резерв и какой процент отходов.\n"
    "   • оцени площадь зоны, покрываемую материалом;\n"
    "   • оцени суммарную площадь закупаемых панелей;\n"
    "   • покажи разницу как отходы и процент отходов.\n"
    "5) Если каких-то данных не хватает — сделай разумные допущения и явно их озвучь.\n"
    "6) Ответ дай структурно и понятно для клиента, без таблиц — только текст, списки и эмодзи.\n"
)


    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "system",
            "content": "Каталог стеновых WPC панелей ECO Стены передаётся ниже в JSON и актуален. "
                       "Не проси у пользователя прайс или JSON.",
        },
        {"role": "user", "content": user_payload},
    ]

    payload = {"model": "gpt-4o-mini", "messages": messages, "temperature": 0.3}  # Исправлено

    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
        print("TEXT CALC RAW RESPONSE:", resp.text)
        resp.raise_for_status()
        data = resp.json()
        answer = data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print("TEXT CALC ERROR:", repr(e))
        answer = "Извините, сейчас не могу выполнить расчёт. Попробуйте чуть позже."

    warning = (
        "<b>Внимание: расчёт, выполненный ботом-калькулятором, не является окончательным.\n"
        "Для точного подбора материалов и окончательного просчёта обязательно свяжитесь с менеджером ECO Стены.</b>\n\n"
    )
    full_answer = warning + answer

    # отправляем расчёт клиенту
    await update.effective_message.reply_text(full_answer, parse_mode="HTML")

    # сохраняем результат для возможной отправки админу
    context.chat_data["last_calc_result"] = full_answer

    # предлагаем варианты действий
    await update.effective_message.reply_text(
        "Что сделать дальше? 👇",
        reply_markup=build_after_calc_keyboard(),
    )

    # Сброс состояния расчёта (кроме last_calc_result)
    context.chat_data["main_mode"] = None
    context.chat_data["calc_phase"] = None
    context.chat_data["calc_items"] = []
    context.chat_data["materials_locked"] = False
    context.chat_data["width_questions_queue"] = []
    context.chat_data["width_answers"] = {}
    context.chat_data["current_width_cat"] = None
    context.chat_data["await_room_height"] = False
    context.chat_data["room_height"] = None
    context.chat_data["height_mode"] = None
    context.chat_data["await_custom_name_index"] = None


# ============================
#   CALLBACK HANDLER
# ============================

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    parts = data.split("|")
    if not parts:
        return
    action = parts[0]

    # Назад в меню
    # ДЕЙСТВИЯ ПОСЛЕ РАСЧЁТА
    if action == "after_calc" and len(parts) >= 2:
        sub = parts[1]

        # ➕ добавить материалы — по сути новый расчёт
        if sub == "add":
            context.chat_data["main_mode"] = "calc"
            context.chat_data["calc_items"] = []
            context.chat_data["calc_phase"] = "select_materials"
            context.chat_data["materials_locked"] = False
            context.chat_data["width_questions_queue"] = []
            context.chat_data["width_answers"] = {}
            context.chat_data["current_width_cat"] = None
            context.chat_data["await_room_height"] = False
            context.chat_data["room_height"] = None
            context.chat_data["height_mode"] = None
            context.chat_data["await_custom_name_index"] = None

            await query.edit_message_text(
                "Давайте добавим материалы.\n\nВыберите категорию:",
                reply_markup=build_calc_category_keyboard(),
            )
            return

    
       # ПРОПУСТИТЬ ВВОД НАЗВАНИЯ ПОСЛЕ РАЗМЕРОВ
    if action == "after_name" and len(parts) >= 2:
        sub = parts[1]
        if sub == "skip":
            # не ждём больше названия/артикула
            context.chat_data["await_custom_name_index"] = None
            context.chat_data["calc_phase"] = "height_mode"

            await query.edit_message_text(
                "Теперь выберите, как считать по высоте:",
                reply_markup=build_height_mode_keyboard(),
            )
            return


        # 📤 отправить расчёт админу
        if sub == "send":
            result = context.chat_data.get("last_calc_result")
            if not ADMIN_CHAT_ID:
                await query.answer("Админ не настроен.", show_alert=True)
                return
            if not result:
                await query.answer("Нет сохранённого расчёта для отправки.", show_alert=True)
                return

            user = query.from_user
            username = f"@{user.username}" if user.username else "ник не указан"
            full_name = user.full_name or ""
            client_info_lines = [
                f"Ник в Telegram: {username}",
            ]
            if full_name:
                client_info_lines.append(f"Имя в профиле: {full_name}")
            client_info_lines.append(f"ID пользователя: {user.id}")
            client_info = "\n".join(client_info_lines)

            text = (
                "Новый расчёт от бота-калькулятора ECO Стены:\n\n"
                f"{result}\n\n"
                f"{client_info}"
            )

            try:
                await tg_application.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=text,
                    parse_mode="HTML",
                )
                await query.answer("Расчёт отправлен админу ✅", show_alert=True)
            except Exception as e:
                print("ERROR sending calc to admin:", repr(e))
                await query.answer("Не удалось отправить расчёт админу 😔", show_alert=True)
            return

        # 🏠 вернуться в главное меню
        if sub == "menu":
            context.chat_data["main_mode"] = None
            await query.edit_message_text(
                "Чем могу помочь? 👇",
                reply_markup=build_main_menu_keyboard(),
            )
            return


    main_mode = context.chat_data.get("main_mode")
    materials_locked = context.chat_data.get("materials_locked", False)

    # Главное меню
    if action == "main" and len(parts) >= 2:
        mode = parts[1]
        context.chat_data["main_mode"] = mode

        if mode == "calc":
            context.chat_data["calc_items"] = []
            context.chat_data["calc_phase"] = "select_materials"
            context.chat_data["materials_locked"] = False
            context.chat_data["width_questions_queue"] = []
            context.chat_data["width_answers"] = {}
            context.chat_data["current_width_cat"] = None
            context.chat_data["await_room_height"] = False
            context.chat_data["room_height"] = None
            context.chat_data["height_mode"] = None
            context.chat_data["await_custom_name_index"] = None

            text = (
                "🧮 Рассчитать материалы.\n\n"
                "Я могу посчитать:\n"
                "• стеновые WPC панели;\n"
                "• реечные панели (WPC и деревянные);\n"
                "• 3D панели.\n\n"
                "Выберите, с каких материалов начать:"
            )
            await query.edit_message_text(text=text, reply_markup=build_calc_category_keyboard())
            return

        if mode == "info":
            await query.edit_message_text(
                "Информация.\n\nВыберите раздел:",
                reply_markup=build_info_category_keyboard(),
            )
            return

        if mode == "catalogs":
            await query.edit_message_text(
                "📂 Получить каталоги.\n\nСейчас есть каталог по стеновым WPC панелям.\nВыберите категорию:",
                reply_markup=build_catalog_category_keyboard(),
            )
            return

        if mode == "presentation":
            try:
                await query.message.reply_document(
                    document="https://ecosteni.ru/wp-content/uploads/2025/11/ecosteny_prezentacziya.pdf",
                    caption="Презентационный каталог ECO Стены (PDF)",
                )
            except Exception as e:
                print("ERROR sending presentation:", repr(e))
                await query.message.reply_text(
                    "Не получилось отправить файл. Вот ссылка:\n"
                    "https://ecosteni.ru/wp-content/uploads/2025/11/ecosteny_prezentacziya.pdf"
                )
            await query.message.reply_text("Чем могу помочь дальше?", reply_markup=build_main_menu_keyboard())
            context.chat_data["main_mode"] = None
            return

        if mode == "contacts":
            text = (
                "📇 Контактная информация ECO Стены\n\n"
                "Адрес:\nРФ, Республика Крым, г. Симферополь\n\n"
                "Телефон:\n+7 (978) 022-32-22\n+7 (978) 706-48-97\n\n"
                "Наши площадки:"
            )
            await query.edit_message_text(
                text,
                reply_markup=build_contacts_keyboard(),
                disable_web_page_preview=True,
            )
            return

        if mode == "partner":
            context.chat_data["partner_state"] = "ask_name"
            await query.edit_message_text(
                "🤝 Хочу стать партнёром.\n\n"
                "Отлично! Давайте познакомимся.\n\nКак вас зовут?"
            )
            return

    # Если материалы   афиксированы, а человек пытается вернуться к выбору — блокируем
    if materials_locked and action in {"calc_cat", "slats_type", "slats_wpc_name", "3d_variant", "product", "thickness", "height"}:
        await query.edit_message_text(
            "Мы уже перешли к этапу расчёта.\n\n"
            "Чтобы начать новый расчёт с другим набором материалов — вернитесь в меню и снова выберите «Рассчитать материалы».",
            reply_markup=build_main_menu_keyboard(),
        )
        return

    # РАСЧЁТ: выбор категории
    if action == "calc_cat" and len(parts) >= 2:
        cat = parts[1]
        context.chat_data["selected_category"] = cat
        context.chat_data["calc_phase"] = "select_materials"

        if cat == "walls":
            await query.edit_message_text(
                "Категория: Стеновые панели.\n\nШаг 1. Выберите тип панели:",
                reply_markup=build_wall_product_keyboard(),
            )
        elif cat == "slats":
            await query.edit_message_text(
                "Категория: Реечные панели.\n\nВыберите тип:",
                reply_markup=build_slats_category_keyboard(),
            )
        elif cat == "3d":
            await query.edit_message_text(
                "Категория: 3D панели.\n\nВыберите размер панели:",
                reply_markup=build_3d_variant_keyboard(),
            )
        else:
            await query.edit_message_text(
                "Эта категория пока в разработке. Сейчас могу посчитать только стеновые, реечные и 3D панели."
            )
        return

    # РЕЕЧНЫЕ: выбор типа
    if action == "slats_type" and len(parts) >= 2:
        base_type = parts[1]
        if base_type == "wpc":
            context.chat_data["slats_base_type"] = "wpc"
            await query.edit_message_text(
                "Тип: WPC реечная панель.\n\nВыберите вариант:",
                reply_markup=build_wpc_slats_name_keyboard(),
            )
        elif base_type == "wood":
            context.chat_data["slats_base_type"] = "wood"
            items = context.chat_data.get("calc_items", [])
            items.append({"category": "slats", "base_type": "wood"})
            context.chat_data["calc_items"] = items
            context.chat_data["await_custom_name_index"] = len(items) - 1
            await query.edit_message_text(
                "Реечные панели — Деревянная панель добавлена в расчёт.\n\n"
                f"Ориентировочная цена: {SLAT_PRICES['wood']} ₽ за панель.\n\n"
                "Если вы знаете точное название или артикул этой позиции — напишите его следующим сообщением.\n"
                "После этого можете добавить ещё материалы или перейти к расчёту.",
                reply_markup=build_add_more_materials_keyboard(),
            )
        else:
            await query.edit_message_text("Не удалось определить тип реечной панели. Попробуйте ещё раз.")
        return

    # РЕЕЧНЫЕ WPC: выбор варианта
    if action == "slats_wpc_name" and len(parts) >= 2:
        name_code = parts[1]
        name_map = {"name1": "Название 1", "name2": "Название 2", "name3": "Название 3"}
        name_human = name_map.get(name_code, "Название 1")
        items = context.chat_data.get("calc_items", [])
        items.append({"category": "slats", "base_type": "wpc", "name_code": name_code, "name_human": name_human})
        context.chat_data["calc_items"] = items
        context.chat_data["await_custom_name_index"] = len(items) - 1
        await query.edit_message_text(
            f"Реечные панели — WPC, {name_human} добавлены в расчёт.\n\n"
            f"Ориентировочная цена: {SLAT_PRICES['wpc']} ₽ за панель.\n\n"
            "Если вы знаете точное название или артикул этой позиции — напишите его следующим сообщением.\n"
            "После этого можете добавить ещё материалы или перейти к расчёту.",
            reply_markup=build_add_more_materials_keyboard(),
        )
        return

    # 3D панели: выбор варианта
    if action == "3d_variant" and len(parts) >= 2:
        vcode = parts[1]
        if vcode not in PANELS_3D:
            await query.edit_message_text("Такого варианта 3D панели нет. Попробуйте ещё раз.")
            return
        items = context.chat_data.get("calc_items", [])
        items.append({"category": "3d", "variant_code": vcode})
        context.chat_data["calc_items"] = items
        context.chat_data["await_custom_name_index"] = len(items) - 1
        label = "600 × 1200 мм" if vcode == "var1" else "1200 × 3000 мм"
        price = PANELS_3D[vcode]["price_rub"]
        await query.edit_message_text(
            f"3D панели ({label}) добавлены в расчёт.\n\n"
            f"Ориентировочная цена: {price} ₽ за панель.\n\n"
            "Если у вас есть конкретное название коллекции или артикул — напишите его следующим сообщением.\n"
            "После этого можете добавить ещё материалы или перейти к расчёту.",
            reply_markup=build_add_more_materials_keyboard(),
        )
        return

    # Стеновые панели: выбор типа
    if action == "product" and len(parts) >= 2:
        product_code = parts[1]
        if product_code not in PRODUCT_CODES:
            await query.edit_message_text("Не удалось найти такой тип панели. Попробуйте ещё раз.")
            return
        context.chat_data["selected_category"] = "walls"
        context.chat_data["selected_product_code"] = product_code
        context.chat_data["selected_thickness_mm"] = None
        context.chat_data["selected_height_mm"] = None
        title = PRODUCT_CODES[product_code]
        await query.edit_message_text(
            "Категория: Стеновые панели\n"
            f"Шаг 1. Тип панели: {title}\n\n"
            "Шаг 2. Выберите толщину панели:",
            reply_markup=build_thickness_keyboard(product_code),
        )
        return

    # Стеновые: выбор толщины
    if action == "thickness" and len(parts) >= 3:
        product_code = parts[1]
        try:
            thickness = int(parts[2])
        except ValueError:
            await query.edit_message_text("Некорректная толщина. Попробуйте ещё раз.")
            return
        if product_code not in PRODUCT_CODES:
            await query.edit_message_text("Такого типа панели нет. Попробуйте ещё раз.")
            return
        title = PRODUCT_CODES[product_code]
        if title not in WALL_PRODUCTS or thickness not in WALL_PRODUCTS[title]:
            await query.edit_message_text("Такой комбинации панели и толщины нет. Попробуйте ещё раз.")
            return
        context.chat_data["selected_product_code"] = product_code
        context.chat_data["selected_thickness_mm"] = thickness
        context.chat_data["selected_height_mm"] = None
        await query.edit_message_text(
            "Категория: Стеновые панели\n"
            f"Шаг 1. Тип панели: {title}\n"
            f"Шаг 2. Толщина: {thickness} мм\n\n"
            "Шаг 3. Выберите высоту панели:",
            reply_markup=build_height_keyboard(product_code, thickness),
        )
        return

    # Стеновые: выбор высоты
    if action == "height" and len(parts) >= 4:
        product_code = parts[1]
        try:
            thickness = int(parts[2])
            height = int(parts[3])
        except ValueError:
            await query.edit_message_text("Некорректные параметры панели. Попробуйте ещё раз.")
            return
        if product_code not in PRODUCT_CODES:
            await query.edit_message_text("Такого типа панели нет. Попробуйте ещё раз.")
            return
        title = PRODUCT_CODES[product_code]
        if (
            title not in WALL_PRODUCTS
            or thickness not in WALL_PRODUCTS[title]
            or height not in WALL_PRODUCTS[title][thickness]["panels"]
        ):
            await query.edit_message_text("Такой панели нет в каталоге. Попробуйте ещё раз.")
            return
        items = context.chat_data.get("calc_items", [])
        items.append({"category": "walls", "product_code": product_code, "thickness": thickness, "height": height})
        context.chat_data["calc_items"] = items
        context.chat_data["await_custom_name_index"] = len(items) - 1
        await query.edit_message_text(
            "Стеновые панели добавлены в расчёт.\n\n"
            "Если вы знаете точное название/артикул этой панели (коллекция, текстура) — напишите его следующим сообщением.\n"
            "После этого можете добавить ещё материалы или перейти к расчёту.",
            reply_markup=build_add_more_materials_keyboard(),
        )
        return

    # ДОБАВИТЬ ЕЩЁ / ПЕРЕЙТИ К РАСЧЁТУ
    if action == "calc_more" and len(parts) >= 2:
        answer = parts[1]

        if answer == "yes":
            context.chat_data["selected_category"] = None
            context.chat_data["selected_product_code"] = None
            context.chat_data["selected_thickness_mm"] = None
            context.chat_data["selected_height_mm"] = None
            context.chat_data["await_custom_name_index"] = None
            context.chat_data["calc_phase"] = "select_materials"
            await query.edit_message_text(
                "Хорошо, добавим ещё материалы.\n\n"
                "Сейчас к расчёту могу добавить стеновые, реечные и 3D панели.\n"
                "Выберите категорию:",
                reply_markup=build_calc_category_keyboard(),
            )
            return
        else:
            # фиксируем набор материалов и переходим к вопросам по размерам
            context.chat_data["materials_locked"] = True
            context.chat_data["await_custom_name_index"] = None
            items = context.chat_data.get("calc_items", [])
            cats = [it.get("category") for it in items]
            order = []
            if "walls" in cats:
                order.append("walls")
            if "slats" in cats:
                order.append("slats")
            if "3d" in cats:
                order.append("3d")
            context.chat_data["width_questions_queue"] = order
            context.chat_data["width_answers"] = {}

            if order:
                first = order[0]
                context.chat_data["current_width_cat"] = first
                context.chat_data["calc_phase"] = "widths"  # Исправлено: явно устанавливаем phase
                context.chat_data["await_room_height"] = False
                context.chat_data["room_height"] = None
                context.chat_data["height_mode"] = None

                if first == "walls":
                    qtext = (
                        "Перед расчётом уточните:\n\n"
                        "❓ Сколько по ширине займут стеновые панели на стене?\n"
                        "Например: 3 м, 2.5 метра, 2500 мм и т.п."
                    )
                elif first == "slats":
                    qtext = (
                        "Перед расчётом уточните:\n\n"
                        "❓ Сколько по ширине стены займут реечные панели?\n"
                        "Например: 1.5 м, 1200 мм и т.п."
                    )
                else:  # 3d
                    qtext = (
                        "Перед расчётом уточните:\n\n"
                        "❓ Сколько по ширине стены займут 3D панели?\n"
                        "Например: 2 м, 1800 мм и т.п."
                    )
                await query.edit_message_text(qtext)
            else:
                await query.edit_message_text(
                    "Сначала выберите хотя бы один материал, а затем вернитесь к расчёту.",
                    reply_markup=build_calc_category_keyboard(),
                )
            return

    # ВЫБОР РЕЖИМА ПО ВЫСОТЕ
    if action == "height_mode" and len(parts) >= 2:
        mode = parts[1]
        context.chat_data["height_mode"] = mode
        await perform_text_calc(update, context)
        return

    # ИНФОРМАЦИЯ: разделы
    if action == "info_cat" and len(parts) >= 2:
        cat = parts[1]

        if cat == "walls":
            text = (
                "🧱 <b>Стеновые WPC панели</b>\n\n"
                "• Толщина: 5 и 8 мм\n"
                "• Ширина листа: 1220 мм\n"
                "• Высоты (мм): 2440 / 2600 / 2800 / 3000 / 3200\n\n"
                "💰 Цены зависят от серии и высоты панели — уточняются по прайсу.\n"
                "⚖ Вес: ориентировочно 9–15 кг за лист.\n\n"
                "📦 Применение: стены, ниши, ТВ-зоны, коридоры, коммерческие помещения."
            )
            await query.edit_message_text(text, parse_mode="HTML")
            context.chat_data["main_mode"] = None
            await query.message.reply_text(
                "Чем могу помочь дальше? 👇",
                reply_markup=build_main_menu_keyboard(),
            )
            return

        elif cat == "slats":
            text = (
                "🎋 <b>Реечные панели</b>\n\n"
                "• Типы: WPC и деревянные\n"
                "• Размер: 168 × 2900 × 18 мм\n\n"
                f"💰 Ориентировочные цены:\n"
                f"• WPC рейка — ~{SLAT_PRICES['wpc']} ₽/шт\n"
                f"• Деревянная рейка — ~{SLAT_PRICES['wood']} ₽/шт\n\n"
                "📏 Применение: акцентные стены, ТВ-зоны, коридоры, зонирование."
            )
            await query.edit_message_text(text, parse_mode="HTML")
            context.chat_data["main_mode"] = None
            await query.message.reply_text(
                "Чем могу помочь дальше? 👇",
                reply_markup=build_main_menu_keyboard(),
            )
            return

        elif cat == "3d":
            text = (
                "🪨 <b>3D панели (скалы)</b>\n\n"
                "• Форматы:\n"
                f"  — 600 × 1200 мм — ~{PANELS_3D['var1']['price_rub']} ₽/шт\n"
                f"  — 1200 × 3000 мм — ~{PANELS_3D['var2']['price_rub']} ₽/шт\n\n"
                "📏 Применение: ТВ-зоны, акцентные стены, лестничные марши, зоны каминов."
            )
            await query.edit_message_text(text, parse_mode="HTML")
            context.chat_data["main_mode"] = None
            await query.message.reply_text(
                "Чем могу помочь дальше? 👇",
                reply_markup=build_main_menu_keyboard(),
            )
            return

        elif cat == "flex":
            text = (
                "🧱 <b>Гибкая керамика</b>\n\n"
                "• Формат: тонкий гибкий материал под кирпич/камень.\n"
                "• Применение: фасады, кухни, коридоры, колонны, радиусы.\n\n"
                "Прайс и точный состав можно подключить отдельным блоком.\n"
                "Напишите, где планируете использовать — подскажу, подойдёт ли гибкая керамика."
            )
            await query.edit_message_text(text, parse_mode="HTML")
            context.chat_data["main_mode"] = None
            await query.message.reply_text(
                "Чем могу помочь дальше? 👇",
                reply_markup=build_main_menu_keyboard(),
            )
            return

        elif cat == "delivery":
            text = (
                "🚚 <b>Доставка и гарантия</b>\n\n"
                "• Доставка по РФ и Крыму — условия зависят от объёма и региона.\n"
                "• Возможен самовывоз со склада (по договорённости).\n\n"
                "🛡 Гарантия: при правильном монтаже панели служат много лет.\n"
                "Детальный гарантийный талон и сертификаты можно оформить отдельным блоком."
            )
            await query.edit_message_text(text, parse_mode="HTML")
            context.chat_data["main_mode"] = None
            await query.message.reply_text(
                "Чем могу помочь дальше? 👇",
                reply_markup=build_main_menu_keyboard(),
            )
            return

        else:
            await query.edit_message_text("По этой категории пока нет подробного описания.")
            context.chat_data["main_mode"] = None
            await query.message.reply_text(
                "Чем могу помочь дальше? 👇",
                reply_markup=build_main_menu_keyboard(),
            )
            return



    # PARTNER ROLE
    if action == "partner_role" and len(parts) >= 2:
        role = parts[1]
        context.chat_data["partner_role"] = role
        context.chat_data["partner_state"] = "ask_projects"
        role_map = {
            "designer": "Дизайнер / Архитектор",
            "shop": "Магазин / Салон",
            "developer": "Застройщик",
            "foreman": "Прораб",
        }
        role_human = role_map.get(role, role)
        await query.edit_message_text(
            f"Отлично! Вы: {role_human}.\n\n"
            "Расскажите пару слов о ваших объектах и формате работы (квартиры, коттеджи, коммерция и т.п.)."
        )
        return

# ============================
#   ОБРАБОТКА ТЕКСТА
# ============================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text or ""
    text_l = user_text.lower()

    if not context.chat_data.get("started"):
        await update.message.reply_text(
            "Чтобы начать, отправьте команду /start."
        )
        return

    main_mode = context.chat_data.get("main_mode")
    calc_phase = context.chat_data.get("calc_phase")

    # 0. Название/артикул для последнего материала
    custom_index = context.chat_data.get("await_custom_name_index")
    if custom_index is not None and main_mode == "calc":
        items = context.chat_data.get("calc_items", [])
        if 0 <= custom_index < len(items):
            items[custom_index]["custom_name"] = user_text.strip()
            context.chat_data["calc_items"] = items
            context.chat_data["await_custom_name_index"] = None

            # Имя/артикул во время выбора материалов
            if calc_phase == "select_materials":
                await update.message.reply_text(
                    f"Зафиксировал название/артикул: <b>{user_text.strip()}</b>.\n"
                    "Теперь можете добавить ещё материалы или перейти к расчёту.",
                    parse_mode="HTML",
                )
                return

            # Имя/артикул после ввода ширины и высоты стены
            if calc_phase == "await_custom_name_after_size":
                await update.message.reply_text(
                    f"Зафиксировал название/артикул: <b>{user_text.strip()}</b>.\n"
                    "Теперь выберите, как считать по высоте:",
                    parse_mode="HTML",
                )
                context.chat_data["calc_phase"] = "height_mode"
                await update.message.reply_text(
                    "Как считать по высоте?",
                    reply_markup=build_height_mode_keyboard(),
                )
                return


        # Вопрос про высоту помещения
        if calc_phase == "height" and context.chat_data.get("await_room_height"):
            # сохраняем высоту помещения
            context.chat_data["room_height"] = user_text.strip()
            context.chat_data["await_room_height"] = False

            items = context.chat_data.get("calc_items", [])
            if items:
                # ждём название/артикул для последнего материала
                context.chat_data["await_custom_name_index"] = len(items) - 1

            # переходим в фазу ожидания названия/артикула после размеров
            context.chat_data["calc_phase"] = "await_custom_name_after_size"

            text = (
                "Высоту зафиксировал.\n\n"
                "Если хотите, можете сейчас указать название или артикул для последнего выбранного материала "
                "(например, конкретная коллекция или текстура). Просто отправьте текст следующим сообщением.\n\n"
                "Если не знаете название — нажмите кнопку ниже."
            )
            await update.message.reply_text(
                text,
                reply_markup=build_skip_name_keyboard(),  # Только одна кнопка "Я не знаю → ДАЛЬШЕ"
            )
            return


        # Вопросы про ширину материалов
        current_cat = context.chat_data.get("current_width_cat")
        queue = context.chat_data.get("width_questions_queue") or []
        if calc_phase == "widths" and current_cat:
            ...
            # остальной код ширины без изменений

            wa = context.chat_data.get("width_answers", {})
            wa[current_cat] = user_text.strip()
            context.chat_data["width_answers"] = wa

            if queue and queue[0] == current_cat:
                queue = queue[1:]
            context.chat_data["width_questions_queue"] = queue

            if queue:
                next_cat = queue[0]
                context.chat_data["current_width_cat"] = next_cat
                if next_cat == "walls":
                    qtext = (
                        "Спасибо! Теперь:\n\n"
                        "❓ Сколько по ширине займут стеновые панели на стене?\n"
                        "Например: 3 м, 2500 мм и т.п."
                    )
                elif next_cat == "slats":
                    qtext = (
                        "Спасибо! Теперь:\n\n"
                        "❓ Сколько по ширине стены займут реечные панели?\n"
                        "Например: 1.5 м, 1200 мм и т.п."
                    )
                else:  # 3d
                    qtext = (
                        "Спасибо! Теперь:\n\n"
                        "❓ Сколько по ширине стены займут 3D панели?\n"
                        "Например: 2 м, 1800 мм и т.п."
                    )
                await update.message.reply_text(qtext)
                return
            else:
                # Все ширины получены — спрашиваем высоту помещения
                context.chat_data["current_width_cat"] = None
                context.chat_data["calc_phase"] = "height"
                context.chat_data["await_room_height"] = True
                await update.message.reply_text(
                    "Отлично! Теперь укажите высоту помещения.\n\n"
                    "Например: 2.7 м, 2700 мм и т.п."
                )
                return

        # fallback на случай рассинхрона
        await update.message.reply_text(
            "Кажется, мы немного запутались с расчётом. Давайте начнём расчёт заново через /menu."
        )
        context.chat_data["main_mode"] = None
        context.chat_data["calc_phase"] = None
        context.chat_data["calc_items"] = []
        context.chat_data["materials_locked"] = False
        context.chat_data["width_questions_queue"] = []
        context.chat_data["width_answers"] = {}
        context.chat_data["current_width_cat"] = None
        context.chat_data["await_room_height"] = False
        context.chat_data["room_height"] = None
        context.chat_data["height_mode"] = None
        context.chat_data["await_custom_name_index"] = None
        return

    # Партнёрка
    if main_mode == "partner":
        await handle_partner_text(update, context)
        return

    # Если режим не выбран — маршрутизация
    if not main_mode:
        DRAW_KEYWORDS = ["чертеж", "чертёж", "чертежом", "чертежу", "план", "планировк", "схема", "схемк"]
        if any(k in text_l for k in DRAW_KEYWORDS):
            await update.message.reply_text(
                "Да, могу подсказать по чертежу 🙂\n\n"
                "Пришлите, пожалуйста, фото или скан планировки/развертки как изображение.\n"
                "Если есть важные детали (окна, двери, ниши) — можете указать их в подписи к фото."
            )
            return
        KEYWORDS = [
            "панел", "wpc", "каталог", "расчет", "расчёт", "рассчит", "материал",
            "3d", "гибкая", "керамик", "реечн", "стенов", "профил",
            "стена", "размер", "высота", "длина"
        ]
        if any(k in text_l for k in KEYWORDS):
            await update.message.reply_text("Чем могу помочь?", reply_markup=build_main_menu_keyboard())
        else:
            await handle_smalltalk(update, context)
        return

    # Информация
    if main_mode == "info":
        await update.message.reply_text(
            "Чтобы получить информацию, выберите раздел через кнопки:",
            reply_markup=build_info_category_keyboard(),
        )
        return

    # Каталоги
    if main_mode == "catalogs":
        await update.message.reply_text(
            "Выберите категорию через кнопки, и я отправлю доступный каталог.\n"
            "Сейчас в боте есть только каталог по стеновым панелям.",
            reply_markup=build_catalog_category_keyboard(),
        )
        return

    await update.message.reply_text(
        "Если хотите начать заново — нажмите /menu, и я покажу главное меню."
    )

# ============================
#   ОБРАБОТКА ФОТО
# ============================

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.chat_data.get("started"):
        await update.message.reply_text(
            "Чтобы начать, отправьте /start."
        )
        return

    if not OPENAI_API_KEY:
        await update.message.reply_text(
            "Сейчас я не могу обработать чертёж через модель (нет ключа OpenAI), "
            "но вы можете прислать размеры текстом, и я помогу с расчётом."
        )
        return

    photos = update.message.photo
    caption = update.message.caption or ""

    if not photos:
        await update.message.reply_text("Не получилось получить изображение. Пришлите, пожалуйста, фото ещё раз.")
        return

    photo = photos[-1]
    file = await photo.get_file()
    bio = BytesIO()
    await file.download_to_memory(out=bio)
    img_bytes = bio.getvalue()
    img_b64 = base64.b64encode(img_bytes).decode("utf-8")

    catalog_json = json.dumps(WALL_PRODUCTS, ensure_ascii=False)
    selection_block = get_calc_selection_block(context)

    style_block = (
        "Формат ответа:\n"
        "— НЕ используй таблицы и символы `|`.\n"
        "— Оформи ответ блоками с заголовками, списками и эмодзи.\n\n"
    )

    header = (
        "Пользователь прислал фото планировки или развертки (чертёж/схема) помещения.\n"
        "Нужно считать видимые размеры стен и оценить площадь.\n\n"
    )

    extra_sizes = (
        "Дополнительно о материалах:\n"
        f"• Реечные панели: 168 × 2900 × 18 мм. WPC — {SLAT_PRICES['wpc']} ₽, дерево — {SLAT_PRICES['wood']} ₽.\n"
        f"• 3D панели 600×1200 мм — {PANELS_3D['var1']['price_rub']} ₽/шт.\n"
        f"• 3D панели 1200×3000 мм — {PANELS_3D['var2']['price_rub']} ₽/шт.\n\n"
    )

    user_instruction = (
        style_block
        + header
        + f"{selection_block}"
        + "Ниже передан JSON с каталогом стеновых панелей WPC (размеры и цены). "
          "Используй ТОЛЬКО его для расчётов по стеновым панелям и не проси у пользователя прайс или JSON.\n\n"
        f"{catalog_json}\n\n"
        f"{extra_sizes}"
        "Задача:\n"
        "1) Считать размеры по изображению и оценить площадь стен.\n"
        "2) Если клиент уже выбрал материалы (по списку выше), посчитать примерный расход и стоимость.\n"
        "3) ОБЯЗАТЕЛЬНО для каждой категории показать ОТХОДЫ: сколько панели идёт в подрезку/резерв и какой процент отходов.\n"
        "4) Если данных не хватает — сделай разумные допущения и явно их озвучь.\n"
        f"Подпись к изображению (если есть): {caption}"
    )

    payload = {
        "model": "gpt-4o",  # Исправлено
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": user_instruction},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
            ]},
        ],
        "temperature": 0.2,
    }

    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
        print("PHOTO RAW RESPONSE:", resp.text)
        resp.raise_for_status()
        data = resp.json()
        answer = data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print("PHOTO ERROR:", repr(e))
        answer = (
            "Сейчас не получается автоматически обработать фото планировки/развертки. "
            "Пришлите, пожалуйста, размеры стен текстом, и я помогу с расчётом."
        )

    warning = (
        "<b>Внимание: расчёт, выполненный ботом-калькулятором, не является окончательным.\n"
        "Для точного подбора материалов и окончательного просчёта обязательно свяжитесь с менеджером ECO Стены.</b>\n\n"
    )
    full_answer = warning + answer

    await update.message.reply_text(full_answer, parse_mode="HTML")
    context.chat_data["plan_description"] = answer

# ============================
#   ОБЁРТКА ДЛЯ ТЕКСТА
# ============================

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_message(update, context)

# ============================
#   РЕГИСТРАЦИЯ ХЕНДЛЕРОВ
# ============================

tg_application.add_handler(CommandHandler("start", start_command))
tg_application.add_handler(CommandHandler("catalog", catalog_command))
tg_application.add_handler(CommandHandler("menu", menu_command))
if ADMIN_CHAT_ID:
    tg_application.add_handler(CommandHandler("reply", reply_command))

tg_application.add_handler(CallbackQueryHandler(handle_callback))

tg_application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
tg_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

# ============================
#   WEBHOOK ROUTE
# ============================

@app.route("/")
def index():
    return "ECO Стены бот работает!"

@app.route(f"/{TG_BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    try:
        update_json = request.get_json(force=True)
        if update_json:
            update = Update.de_json(update_json, tg_application.bot)
            asyncio.create_task(tg_application.process_update(update))
        return jsonify({"status": "ok"})
    except Exception as e:
        print("WEBHOOK ERROR:", repr(e))
        return jsonify({"status": "error"}), 500

# ============================
#   ЗАПУСК БОТА
# ============================

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8443"))
    webhook_url = os.getenv("WEBHOOK_URL")

    # Прод: работаем через webhook (Render)
    if webhook_url:
        print(f"Запускаю webhook-сервер на порту {port}...")
        print(f"Webhook URL: {webhook_url}/{TG_BOT_TOKEN}")

        tg_application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=TG_BOT_TOKEN,
            webhook_url=f"{webhook_url}/{TG_BOT_TOKEN}",
        )

    # Локально (без WEBHOOK_URL) — polling
    else:
        print("WEBHOOK_URL не задан. Запускаю бота в режиме polling (локальный режим)...")
        tg_application.run_polling()

