import os
import logging
import re
from datetime import datetime

# tg imports
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from telegram import Message


# docx
from docxtpl import DocxTemplate

# format
from num2words import num2words      # числа прописью
from babel.dates import format_date  # даты в формате ru-RU

# .env
from dotenv import load_dotenv


# logic
from form_logic import (
    format_date as custom_format_date,  # наша обёртка поверх babel
    format_money,
    format_fio,
    format_location,
    to_upper,
    validate_street_and_house,
    fill_template,
)

# === Импорт FIELDS ===
from fields import FIELDS


# ===============================
# Глобальные константы и состояния
# ===============================

# Состояния ConversationHandler
ASK_FIELD = 1  # единственное рабочее состояние опроса

# Хранилище ответов: uid -> dict ответов (ключи как в шаблоне)
user_data: dict[int, dict] = {}

# Постоянная Reply-клавиатура
DEFAULT_KEYBOARD = ReplyKeyboardMarkup(
    [["-", "Скачать файл", "/start"]],
    resize_keyboard=True,
    one_time_keyboard=False,
    selective=True,
)

# CallbackData для инлайн-кнопок
CB_INSTRUCTION = "instruction"
CB_START_RENT = "start_rent"
CB_CONFIRM_RESTART = "confirm_restart"
CB_CONTINUE = "continue"

# Инлайн-выборы по проекту
CB_PAYER_TENANT = "наниматель"
CB_PAYER_LANDLORD = "наймодатель"
CB_YES = "разрешено"
CB_NO = "запрещено"
CB_DEFAULT_CONDITION = "default_condition"

# Ключи во внутреннем контексте (ContextTypes)
CTX_STEP = "step"                   # текущий шаг в FIELDS
CTX_SKIP_INLINE_SENT = "skip_inline_sent"  # чтобы не дублировать пост с инлайн-кнопками

# Пути к шаблону и временным файлам
TEMPLATE_PATH = "template.docx"  # положи шаблон рядом с main.py
OUTPUT_DIR = ".venv/out"  # папка для временных .docx (будем удалять после отправки)
CTX_SHOW_KEYBOARD_ONCE = "show_keyboard_once"

CB_DOC_EGRN = "doc_egrn"
CB_DOC_CERT = "doc_cert"

# Новые колбэки для доп. договоров
CB_DOC_COMM_TENANT = "doc_comm_tenant"   # комиссия от нанимателя
CB_DOC_COMM_SOB    = "doc_comm_sob"      # комиссия от наймодателя

TEMPLATE_OKAZ_PATH = "template_okaz.docx"  # наниматель
TEMPLATE_SOB_PATH  = "template_sob.docx"  # наймодатель

CTX_MAIN_SENT = "main_contract_sent"

CB_SKIP_DOC = "skip_doc"


# сколько подчёркиваний подставлять вместо "-"
UNDERSCORE_WIDTHS = {
    # ===== Основные данные =====
    "contract_number": 5,

    "naim_name": 98,
    "naim_address": 101,
    "naim_passport_series": 6,
    "naim_passport_number": 9,
    "naim_passport_issued_by": 93,
    "naim_passport_issued_date": 17,

    "ar_name": 94,
    "ar_address": 100,
    "ar_passport_series": 6,
    "ar_passport_number": 9,
    "ar_passport_issued_by": 93,
    "ar_passport_issued_date": 17,

    # ===== Объект найма =====
    "obj_street": 27,
    "obj_house": 6,
    "obj_building": 6,
    "obj_flat": 6,
    "obj_rooms": 7,
    "obj_area": 16,
    "obj_kadastr": 50,

    # ===== Совместно проживающие =====
    "obj_tenants": 97,

    # ===== Опции =====
    "obj_animals": 62,
    "obj_smoking": 60,

    # ===== Сроки найма =====
    "rent_start": 34,   # обе даты в одной строке

    # ===== Оплаты =====
    "monthly_payment": 18,
    "deposit_date": 17,
    "deposit_amount": 18,
    "monthly_due_day": 4,
    "payment_utilities": 93,
    "payment_internet": 64,
    "payment_electricity": 64,
    "payment_water": 63,
    "payment_repair": 63,

    # ===== Дополнительные условия =====
    "additional_conditions": 540,  # три строки по 90

    # ===== Акт приёма-передачи =====
    "act_date": 17,
    "act_condition": 115,
    "act_keys": 9,
    "act_electricity": 25,
    "act_hot_water": 23,
    "act_cold_water": 23,

    # ===== Прочее =====
    "name_of_document": 50,
    "document_value": 50,
    "obj_address": 80,   # на случай пропуска адреса целиком
}


# поля, где при "-" надо НЕ подставлять подчёркивания, а оставить пусто
EMPTY_IF_DASH = {
    "obj_building",  # в адресе корпус пропускаем совсем
    "obj_flat",      # если нужно — по желанию
}

CB_SKIP_ADDR = "skip_addr"
CB_SKIP_COMM = "skip_comm"






# ===============================
# Утилиты: токен, папка, user_id
# ===============================

def get_token() -> str:
    load_dotenv()
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN не найден в окружении/.env")
    return token


def ensure_outdir() -> None:
    if not os.path.isdir(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR, exist_ok=True)


def uid_from(update: Update) -> int:
    """Возвращает целочисленный user_id из апдейта."""
    if update.effective_user:
        return update.effective_user.id
    # Фолбэк на случай редких типов апдейтов
    if update.message and update.message.from_user:
        return update.message.from_user.id
    raise RuntimeError("Не удалось определить user_id")


# ===============================
# Главное меню
# ===============================

async def send_start_menu(target: Message) -> None:
    text = (
        "привет!\n\n"
        "Можно прочитать инструкцию или начать заполнять договор."
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(text="📘 Инструкция", callback_data=CB_INSTRUCTION),
            InlineKeyboardButton(text="📄 Договор аренды", callback_data=CB_START_RENT),
        ]
    ])
    await target.reply_text(text, reply_markup=keyboard)




# ===============================
# Команда /start
# ===============================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    step = context.user_data.get(CTX_STEP)
    if step is not None:
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔁 Начать заново", callback_data=CB_CONFIRM_RESTART),
                InlineKeyboardButton("➡️ Продолжить", callback_data=CB_CONTINUE),
            ]
        ])
        await update.effective_message.reply_text(
            "Обнаружена незавершённая сессия. Что делаем?",
            reply_markup=keyboard
        )
        return

    # одно приветственное сообщение с двумя кнопками
    await send_start_menu(update.effective_message)



def reset_to_start(context: ContextTypes.DEFAULT_TYPE, uid: int) -> None:
    context.user_data[CTX_STEP] = None
    context.user_data[CTX_SKIP_INLINE_SENT] = False
    context.user_data.pop(CTX_MAIN_SENT, None)
    user_data.pop(uid, None)  # очищаем ответы текущей сессии




# ===============================
# Обработчик инлайн-кнопок меню
# ===============================

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    data = query.data
    await query.answer()

    if data == CB_INSTRUCTION:
        text = text = (
        "📘 **Как пользоваться ботом:**\n"
        "1️⃣ Отвечайте на вопросы последовательно — бот сам соберёт договор.\n"
        "2️⃣ Для пропуска любого пункта введите «-» или нажмите кнопку «Пропустить».\n"
        "3️⃣ В любой момент можно написать «Скачать файл» — чтобы получить договор.\n"
        "4️⃣ Всё сохраняется до конца, можно вернуться и продолжить.\n\n"
        "✨ **Почему это удобно:**\n"
        "• Бот автоматически форматирует все данные (ФИО, даты, суммы, адреса).\n"
        "• Подставляет подчёркивания, если что-то пропущено.\n"
        "• Проверяет корректность ввода — чтобы документ выглядел идеально.\n"
        "• После заполнения можно сразу получить доп. договоры (комиссии и акт).\n\n"
        "Начните с кнопки ниже 👇"
    )
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("📘 Инструкция", callback_data=CB_INSTRUCTION),
                    InlineKeyboardButton("📄 Договор аренды", callback_data=CB_START_RENT),
                ]
            ])
        )
        return

    uid = uid_from(update)

    if data == CB_START_RENT:
        user_data[uid] = {}
        context.user_data[CTX_STEP] = 0
        context.user_data[CTX_SKIP_INLINE_SENT] = False
        context.user_data[CTX_SHOW_KEYBOARD_ONCE] = True
        await query.edit_message_text("Начинаем заполнение договора.")
        await ask_next_field(update, context)
        return ASK_FIELD

    if data == CB_CONFIRM_RESTART:
        user_data[uid] = {}
        context.user_data[CTX_STEP] = 0
        context.user_data[CTX_SKIP_INLINE_SENT] = False
        context.user_data[CTX_SHOW_KEYBOARD_ONCE] = True
        await query.edit_message_text("Начинаем заново.")
        await ask_next_field(update, context)
        return ASK_FIELD

    if data == CB_CONTINUE:
        context.user_data[CTX_SHOW_KEYBOARD_ONCE] = True
        await query.edit_message_text("Продолжаем с текущего шага.")
        await ask_next_field(update, context)
        return ASK_FIELD

    # Доп. договор: комиссия от нанимателя
    if data == CB_DOC_COMM_TENANT:
        uid = uid_from(update)
        data_map = user_data.get(uid, {})
        ctx = {k: (v if v not in (None, "") else "-") for k, v in data_map.items()}

        doc_choice = data_map.get("doc_choice")
        if doc_choice == "egrn":
            ctx["name_of_document"] = "Выписка из ЕГРН,"
            ctx["document_value"] = data_map.get("obj_kadastr", "-")
        elif doc_choice == "cert":
            ctx["name_of_document"] = "Свидетельство о государственной регистрации права,"
            series = data_map.get("cert_series", "-")
            number = data_map.get("cert_number", "-")
            ctx["document_value"] = f"серия {series} № {number}"
        else:
            ctx["name_of_document"] = "-"
            ctx["document_value"] = "-"

        ensure_outdir()
        filename = "договор_комиссия_наниматель.docx"
        out_path = os.path.join(OUTPUT_DIR, filename)
        try:
            fill_template(ctx, TEMPLATE_OKAZ_PATH, out_path)
        except Exception as e:
            await query.edit_message_text(f"Ошибка генерации: {e}")
            return ConversationHandler.END

        try:
            await query.message.chat.send_document(document=open(out_path, "rb"), filename=filename)
        finally:
            try:
                os.remove(out_path)
            except OSError:
                pass

        await query.edit_message_text("Отправлен договор: комиссия от нанимателя.")
        reset_to_start(context, uid)
        await send_start_menu(query.message)
        return ConversationHandler.END

    # Доп. договор: комиссия от наймодателя
    if data == CB_DOC_COMM_SOB:
        uid = uid_from(update)
        data_map = user_data.get(uid, {})
        ctx = {k: (v if v not in (None, "") else "-") for k, v in data_map.items()}

        doc_choice = data_map.get("doc_choice")
        if doc_choice == "egrn":
            ctx["name_of_document"] = "Выписка из ЕГРН,"
            ctx["document_value"] = data_map.get("obj_kadastr", "-")
        elif doc_choice == "cert":
            ctx["name_of_document"] = "Свидетельство о государственной регистрации права,"
            series = data_map.get("cert_series", "-")
            number = data_map.get("cert_number", "-")
            ctx["document_value"] = f"серия {series} № {number}"
        else:
            ctx["name_of_document"] = "-"
            ctx["document_value"] = "-"

        ensure_outdir()
        filename = "договор_комиссия_собственник.docx"
        out_path = os.path.join(OUTPUT_DIR, filename)
        try:
            fill_template(ctx, TEMPLATE_SOB_PATH, out_path)
        except Exception as e:
            await query.edit_message_text(f"Ошибка генерации: {e}")
            return ConversationHandler.END

        try:
            await query.message.chat.send_document(document=open(out_path, "rb"), filename=filename)
        finally:
            try:
                os.remove(out_path)
            except OSError:
                pass

        await query.edit_message_text("Отправлен договор: комиссия от наймодателя.")
        reset_to_start(context, uid)
        await send_start_menu(query.message)
        return ConversationHandler.END

    # Пропуск доп. договоров
    if data == CB_SKIP_COMM:
        uid = uid_from(update)
        await query.edit_message_text("Дополнительные договоры пропущены.")
        reset_to_start(context, uid)
        await send_start_menu(query.message)
        return ConversationHandler.END


# ===============================
# Ядро опроса: задать следующий вопрос
# (этап 1: только вывод вопроса, без обработки ответов)
# ===============================

async def ask_next_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    step = context.user_data.get(CTX_STEP, 0)
    if step >= len(FIELDS):
        if context.user_data.get(CTX_MAIN_SENT):
            return  # уже спрашивали про доп. договоры, ждём клика
        context.user_data[CTX_MAIN_SENT] = True
        await update.effective_message.reply_text("✅ Все данные собраны. Формирую файл...")
        await download_file(update, context)
        # остаёмся в диалоге и ждём нажатие на «Комиссия …»
        context.user_data[CTX_STEP] = len(FIELDS) + 1  # сторожевое значение
        return


    # Автопропуски: если выбран ЕГРН — пропускаем поля свидетельства; если свидетельство — пропускаем кадастр
    uid = uid_from(update)
    current = FIELDS[step]
    key = current["key"]
    choice = user_data.get(uid, {}).get("doc_choice")

    # Если документ пропущен вручную
    if choice == "skip":
        # список всех полей, относящихся к документу
        skip_fields = ("obj_kadastr", "cert_series", "cert_number")
        if key in skip_fields:
            width = UNDERSCORE_WIDTHS.get(key, 40)
            user_data.setdefault(uid, {})[key] = "_" * width
            context.user_data[CTX_STEP] = step + 1
            await ask_next_field(update, context)
            return

    # Если выбран ЕГРН — не спрашиваем свидетельство
    if choice == "egrn" and key in ("cert_series", "cert_number"):
        width = UNDERSCORE_WIDTHS.get(key, 40)
        user_data.setdefault(uid, {})[key] = "_" * width
        context.user_data[CTX_STEP] = step + 1
        await ask_next_field(update, context)
        return

    # Если выбран документ "Свидетельство" — пропускаем кадастр
    if choice == "cert" and key == "obj_kadastr":
        width = UNDERSCORE_WIDTHS.get(key, 40)
        user_data.setdefault(uid, {})[key] = "_" * width
        context.user_data[CTX_STEP] = step + 1
        await ask_next_field(update, context)
        return


    field = FIELDS[step]
    question = field["question"]
    formatter = field.get("formatter")

    # решаем, показывать ли Reply-клавиатуру один раз
    show_reply = context.user_data.pop(CTX_SHOW_KEYBOARD_ONCE, False)
    reply_kwargs = {"reply_markup": DEFAULT_KEYBOARD} if show_reply else {}

    # Инлайн-варианты
    # ===== Инлайн-кнопки: плательщик =====
    if formatter == "inline_buttons":
        if not context.user_data.get(CTX_SKIP_INLINE_SENT):
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("Наниматель", callback_data=CB_PAYER_TENANT),
                InlineKeyboardButton("Наймодатель", callback_data=CB_PAYER_LANDLORD),
            ]])
            context.user_data[CTX_SKIP_INLINE_SENT] = True
            await update.effective_message.reply_text(question, reply_markup=kb)
        return

    # ===== Инлайн-кнопки: да/нет =====
    if formatter == "inline_yes_no":
        if not context.user_data.get(CTX_SKIP_INLINE_SENT):
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("Разрешено", callback_data=CB_YES),
                InlineKeyboardButton("Запрещено", callback_data=CB_NO),
            ]])
            context.user_data[CTX_SKIP_INLINE_SENT] = True
            await update.effective_message.reply_text(question, reply_markup=kb)
        return

    if formatter == "inline_default_condition":
        if not context.user_data.get(CTX_SKIP_INLINE_SENT):
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🟢 Всё исправно…", callback_data=CB_DEFAULT_CONDITION)]
            ])
            context.user_data[CTX_SKIP_INLINE_SENT] = True
            await update.effective_message.reply_text(
                question + "\nМожно ввести текст вручную или нажать кнопку.",
                reply_markup=kb
            )
            return
        return

    # Мульти-адрес

    if formatter in ("multi_address_naim", "multi_address_ar"):
        if not context.user_data.get(CTX_SKIP_INLINE_SENT):
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("Пропустить адрес", callback_data=CB_SKIP_ADDR)]])
            context.user_data[CTX_SKIP_INLINE_SENT] = True
            await update.effective_message.reply_text(
                question + " (пример: Москва)",
                reply_markup=kb
            )
        return

    if formatter == "multi_address_obj":
        if not context.user_data.get(CTX_SKIP_INLINE_SENT):
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("Пропустить адрес", callback_data=CB_SKIP_ADDR)]])
            context.user_data[CTX_SKIP_INLINE_SENT] = True
            await update.effective_message.reply_text(question, reply_markup=kb)
        return

    # Выбор документа права (ЕГРН/Свидетельство)
    if formatter == "inline_doc_choice":
        if not context.user_data.get(CTX_SKIP_INLINE_SENT):
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("ЕГРН", callback_data=CB_DOC_EGRN),
                InlineKeyboardButton("Свидетельство", callback_data=CB_DOC_CERT),
                InlineKeyboardButton("Пропустить", callback_data=CB_SKIP_DOC),
            ]])
            context.user_data[CTX_SKIP_INLINE_SENT] = True
            await update.effective_message.reply_text(question, reply_markup=kb)
        return

    # Делать ли акт приёма-передачи? (Да/Нет)
    if formatter == "inline_make_act":
        if not context.user_data.get(CTX_SKIP_INLINE_SENT):
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Да", callback_data=CB_YES),
                    InlineKeyboardButton("Нет", callback_data=CB_NO),
                ]
            ])
            context.user_data[CTX_SKIP_INLINE_SENT] = True
            await update.effective_message.reply_text(question, reply_markup=kb)
            return
        return


    # Обычный вопрос
    await update.effective_message.reply_text(question, **reply_kwargs)




# ===============================
# Обработка ответов и переход шага
# ===============================

async def on_user_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.effective_message
    uid = uid_from(update)
    step = context.user_data.get(CTX_STEP, 0)

    if step is None:
        await send_start_menu(msg)
        return ASK_FIELD

    # Перехват «Скачать файл» (остаётся как запасной вариант)
    if msg and msg.text and msg.text.strip().lower() == "скачать файл":
        await download_file(update, context)
        reset_to_start(context, uid)
        await send_start_menu(msg)
        return ConversationHandler.END

    if step >= len(FIELDS):
        # файл уже отправили из ask_next_field
        return ASK_FIELD

    field = FIELDS[step]
    key = field["key"]
    formatter = field.get("formatter")

    # ===== Входные данные из колбэка или текста =====
    is_cb = update.callback_query is not None
    cb_data = update.callback_query.data if is_cb else None
    text = None if is_cb else (msg.text or "").strip()

    # ===== Инлайн-кнопки: плательщик =====
    if formatter == "inline_buttons":
        # пропуск через "-"
        if not is_cb and text == "-":
            user_data.setdefault(uid, {})[key] = "-"
            context.user_data[CTX_STEP] = step + 1
            context.user_data[CTX_SKIP_INLINE_SENT] = False
            await msg.reply_text("Пропущено.")
            await ask_next_field(update, context)
            return ASK_FIELD

        if not is_cb:
            return ASK_FIELD
        if cb_data == CB_PAYER_TENANT:
            value = "Наниматель"
        elif cb_data == CB_PAYER_LANDLORD:
            value = "Наймодатель"
        else:
            return ASK_FIELD
        user_data.setdefault(uid, {})[key] = value
        context.user_data[CTX_STEP] = step + 1
        context.user_data[CTX_SKIP_INLINE_SENT] = False
        await update.callback_query.edit_message_text(f"✅ Вы выбрали: {value}")
        await ask_next_field(update, context)
        return ASK_FIELD

    # ===== Инлайн-кнопки: да/нет =====
    if formatter == "inline_yes_no":
        # пропуск через "-"
        if not is_cb and text == "-":
            user_data.setdefault(uid, {})[key] = "-"
            context.user_data[CTX_STEP] = step + 1
            context.user_data[CTX_SKIP_INLINE_SENT] = False
            await msg.reply_text("Пропущено.")
            await ask_next_field(update, context)
            return ASK_FIELD

        if not is_cb:
            return ASK_FIELD
        if cb_data == CB_YES:
            value = "Разрешено"
        elif cb_data == CB_NO:
            value = "Запрещено"
        else:
            return ASK_FIELD
        user_data.setdefault(uid, {})[key] = value
        context.user_data[CTX_STEP] = step + 1
        context.user_data[CTX_SKIP_INLINE_SENT] = False
        await update.callback_query.edit_message_text(f"✅ Вы выбрали: {value}")
        await ask_next_field(update, context)
        return ASK_FIELD

    # ===== Инлайн-кнопка: дефолтное состояние акта =====
    if formatter == "inline_default_condition":
        if is_cb and cb_data == CB_DEFAULT_CONDITION:
            user_data.setdefault(uid, {})[key] = (
                "Всё оборудование, мебель, техника и системы исправны и находятся в хорошем и рабочем состоянии."
            )
            context.user_data[CTX_STEP] = step + 1
            context.user_data[CTX_SKIP_INLINE_SENT] = False
            await update.callback_query.edit_message_text("✅ Состояние заполнено по шаблону.")
            await ask_next_field(update, context)
            return ASK_FIELD
        # Если пользователь ввёл текст вручную
        if not is_cb and text:
            user_data.setdefault(uid, {})[key] = text
            context.user_data[CTX_STEP] = step + 1
            context.user_data[CTX_SKIP_INLINE_SENT] = False
            await ask_next_field(update, context)
            return ASK_FIELD
        return ASK_FIELD

    # ===== Инлайн-кнопки: выбор документа права =====
    if formatter == "inline_doc_choice":
        # пропуск текстом "-"
        if not is_cb and text == "-":
            # ничего не выбираем; download_file уже умеет подставлять дефолт ("-")
            context.user_data[CTX_STEP] = step + 1
            context.user_data[CTX_SKIP_INLINE_SENT] = False
            await msg.reply_text("Пропущено.")
            await ask_next_field(update, context)
            return ASK_FIELD

        # обработка колбэков
        if not is_cb:
            return ASK_FIELD

        if cb_data == CB_DOC_EGRN:
            user_data.setdefault(uid, {})["doc_choice"] = "egrn"
            picked = "ЕГРН"
        elif cb_data == CB_DOC_CERT:
            user_data.setdefault(uid, {})["doc_choice"] = "cert"
            picked = "Свидетельство"
        elif cb_data == CB_SKIP_DOC:
            # пропуск выборa
            user_data.setdefault(uid, {})["doc_choice"] = "skip"
            context.user_data[CTX_STEP] = step + 1
            context.user_data[CTX_SKIP_INLINE_SENT] = False
            await update.callback_query.edit_message_text("Документ права пропущен.")
            await ask_next_field(update, context)
            return ASK_FIELD
        else:
            return ASK_FIELD

        context.user_data[CTX_STEP] = step + 1
        context.user_data[CTX_SKIP_INLINE_SENT] = False
        await update.callback_query.edit_message_text(f"✅ Документ: {picked}")
        await ask_next_field(update, context)
        return ASK_FIELD

    # ===== Инлайн-кнопки: делать ли акт приёма-передачи =====
    if formatter == "inline_make_act":
        if not is_cb:
            return ASK_FIELD

        if cb_data == CB_YES:
            # Да: продолжаем сбор полей акта
            user_data.setdefault(uid, {})[key] = "Да"
            context.user_data[CTX_STEP] = step + 1
            context.user_data[CTX_SKIP_INLINE_SENT] = False
            await update.callback_query.edit_message_text("✅ Акт приёма-передачи будет оформлен.")
            await ask_next_field(update, context)
            return ASK_FIELD

        if cb_data == CB_NO:
            # Нет: акт пропускаем, основной договор формируем один раз
            user_data.setdefault(uid, {})[key] = "Нет"
            context.user_data[CTX_STEP] = len(FIELDS) + 1  # выходим за пределы опроса, но диалог не закрываем
            if not context.user_data.get(CTX_MAIN_SENT):
                context.user_data[CTX_MAIN_SENT] = True
                await update.callback_query.edit_message_text("🚫 Акт приёма-передачи не оформляется.\nФормирую файл…")
                await download_file(update, context)
            return ASK_FIELD

        return ASK_FIELD

    # ===== Мульти-адрес: регистрация (наим/наймодатель) =====
    if formatter in ("multi_address_naim", "multi_address_ar"):
        phase_key = f"{key}_phase"
        temp_key = f"{key}_temp"
        phase = context.user_data.get(phase_key, "city")
        temp = context.user_data.setdefault(temp_key, {})

        # 1) Кнопка "Пропустить адрес"
        if is_cb and cb_data == CB_SKIP_ADDR:
            width = UNDERSCORE_WIDTHS.get(key, 40)
            user_data.setdefault(uid, {})[key] = "_" * width
            # очистка временных
            context.user_data.pop(phase_key, None)
            context.user_data.pop(temp_key, None)
            context.user_data[CTX_STEP] = step + 1
            context.user_data[CTX_SKIP_INLINE_SENT] = False
            await update.callback_query.edit_message_text("Адрес пропущен.")
            await ask_next_field(update, context)
            return ASK_FIELD

        # 2) Любые другие callback-и игнорируем здесь
        if is_cb:
            return ASK_FIELD

        # 3) Требуем текст
        if not text:
            await msg.reply_text("Введите текст. Для пропуска используйте «-».")
            return ASK_FIELD

        # 4) Фазы ввода
        if phase == "city":
            temp["city"] = format_location(text)
            if temp["city"] is None:
                await msg.reply_text("Неверный формат города. Пример: Москва")
                return ASK_FIELD
            context.user_data[phase_key] = "street"
            await msg.reply_text("Улица регистрации (пример: Барочная):")
            return ASK_FIELD

        if phase == "street":
            temp["street"] = format_location(text)
            if temp["street"] is None:
                await msg.reply_text("Неверный формат улицы. Пример: Тверская")
                return ASK_FIELD
            context.user_data[phase_key] = "house"
            await msg.reply_text("Дом (например: 10, 10А, 10/2):")
            return ASK_FIELD

        if phase == "house":
            if text.strip() == "-":
                temp["house"] = "-"
                context.user_data[phase_key] = "building"
                await msg.reply_text("Корпус (если нет — напишите «-»):")
                return ASK_FIELD
            ok = validate_street_and_house(temp["street"], text)
            if not ok:
                await msg.reply_text("Неверный дом. Пример: 10, 10к2, 10/2")
                return ASK_FIELD
            _, house_norm = ok
            temp["house"] = house_norm
            context.user_data[phase_key] = "building"
            await msg.reply_text("Корпус (если нет — напишите «-»):")
            return ASK_FIELD

        if phase == "building":
            temp["building"] = text.strip()
            context.user_data[phase_key] = "flat"
            await msg.reply_text("Квартира (Пример: 777):")
            return ASK_FIELD

        if phase == "flat":
            temp["flat"] = text.strip()

            parts = [f"г. {temp['city']}", f"ул. {temp['street']}", f"д. {temp['house']}"]
            if temp.get("building") and temp["building"] != "-":
                parts.append(f"к. {temp['building']}")
            if temp.get("flat") and temp["flat"] != "-":
                parts.append(f"кв. {temp['flat']}")
            full_addr = ", ".join(parts) + ","

            user_data.setdefault(uid, {})[key] = full_addr
            context.user_data.pop(phase_key, None)
            context.user_data.pop(temp_key, None)

            context.user_data[CTX_STEP] = step + 1
            context.user_data[CTX_SKIP_INLINE_SENT] = False
            await ask_next_field(update, context)
            return ASK_FIELD

    if formatter == "multi_address_obj":
        phase_key = f"{key}_phase"
        temp_key = f"{key}_temp"

        if is_cb and cb_data == CB_SKIP_ADDR:
            width = UNDERSCORE_WIDTHS.get("obj_address", 40)
            ud = user_data.setdefault(uid, {})
            ud["obj_address"] = "_" * width
            ud["obj_street"] = "-"
            ud["obj_house"] = "-"
            ud["obj_building"] = ""
            ud["obj_flat"] = ""
            context.user_data.pop(phase_key, None)
            context.user_data.pop(temp_key, None)
            context.user_data[CTX_STEP] = step + 1
            context.user_data[CTX_SKIP_INLINE_SENT] = False
            await update.callback_query.edit_message_text("Адрес объекта пропущен.")
            await ask_next_field(update, context)
            return ASK_FIELD

    # ===== Мульти-адрес: объект найма (город фиксирован: Санкт-Петербург) =====
    if formatter == "multi_address_obj":
        phase_key = f"{key}_phase"  # street -> house -> building -> flat
        phase = context.user_data.get(phase_key, "street")
        temp = context.user_data.setdefault(f"{key}_temp", {})

        # Требуем текст
        if is_cb or not text:
            await msg.reply_text("Введите текст. Для пропуска используйте «-».")
            return ASK_FIELD

        if phase == "street":
            temp["street"] = format_location(text)
            if temp["street"] is None:
                await msg.reply_text("Неверная улица. Пример: Тверская")
                return ASK_FIELD
            context.user_data[phase_key] = "house"
            await msg.reply_text("Дом (например: 10, 10к2, 10/2):")
            return ASK_FIELD

        if phase == "house":
            if text.strip() == "-":
                temp["house"] = "-"
            else:
                ok = validate_street_and_house(temp["street"], text)
                if not ok:
                    await msg.reply_text("Неверный дом. Пример: 10, 10к2, 10/2")
                    return ASK_FIELD
                _, house_norm = ok
                temp["house"] = house_norm
            context.user_data[phase_key] = "building"
            await msg.reply_text("Корпус (если нет — напишите «-»):")
            return ASK_FIELD

        if phase == "building":
            temp["building"] = text.strip()
            context.user_data[phase_key] = "flat"
            await msg.reply_text("Квартира (число или «-»):")
            return ASK_FIELD

        if phase == "flat":
            temp["flat"] = text.strip()

            # Склейка адреса: город фиксирован
            parts = [
                "г. Санкт-Петербург",
                f"ул. {temp['street']}",
            ]
            if temp.get("house") and temp["house"] != "-":
                parts.append(f"д. {temp['house']}")
            if temp.get("building") and temp["building"] != "-":
                parts.append(f"к. {temp['building']}")
            if temp.get("flat") and temp["flat"] != "-":
                parts.append(f"кв. {temp['flat']}")

            full_addr = ", ".join(parts) + ","

            # Сохраняем итог и, для совместимости с другими шаблонами, части тоже
            ud = user_data.setdefault(uid, {})
            ud["obj_address"] = full_addr
            ud["obj_street"] = temp.get("street", "-")
            ud["obj_house"] = temp.get("house", "-")
            ud["obj_building"] = (temp.get("building") if temp.get("building") != "-" else "")
            ud["obj_flat"] = (temp.get("flat") if temp.get("flat") != "-" else "")

            # очистка временных
            context.user_data.pop(phase_key, None)
            context.user_data.pop(f"{key}_temp", None)

            context.user_data[CTX_STEP] = step + 1
            context.user_data[CTX_SKIP_INLINE_SENT] = False
            await ask_next_field(update, context)
            return ASK_FIELD

    # ===== Многострочные доп. условия =====
    if formatter == "multi_conditions":
        buf_key = f"{key}_buf"
        buf = context.user_data.get(buf_key, [])
        if not is_cb and text == "-":
            if not buf:
                user_data.setdefault(uid, {})[key] = "-"
            else:
                numbered = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(buf))
                user_data.setdefault(uid, {})[key] = numbered
            context.user_data.pop(buf_key, None)
            context.user_data[CTX_STEP] = step + 1
            context.user_data[CTX_SKIP_INLINE_SENT] = False
            await ask_next_field(update, context)
            return ASK_FIELD
        if not is_cb and text:
            buf.append(text)
            context.user_data[buf_key] = buf
            await msg.reply_text(
                "Добавлено. Следующий пункт или «-» для завершения:",
                reply_markup=DEFAULT_KEYBOARD
            )
            return ASK_FIELD
        return ASK_FIELD

    # ===== Несколько совместно проживающих (списком) =====
    if formatter == "multi_tenants":
        buf_key = f"{key}_buf"
        buf = context.user_data.get(buf_key, [])
        if is_cb:
            return ASK_FIELD

        if text == "-":
            if not buf:
                user_data.setdefault(uid, {})[key] = "-"
            else:
                numbered = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(buf))
                user_data.setdefault(uid, {})[key] = numbered
            context.user_data.pop(buf_key, None)
            context.user_data[CTX_STEP] = step + 1
            context.user_data[CTX_SKIP_INLINE_SENT] = False
            await ask_next_field(update, context)
            return ASK_FIELD

        fio = format_fio(text)
        if fio is None:
            await msg.reply_text(
                "❌ Неверный формат ФИО. Пример: Иванов Иван Иванович",
                reply_markup=DEFAULT_KEYBOARD
            )
            return ASK_FIELD

        buf.append(fio)
        context.user_data[buf_key] = buf
        await msg.reply_text(
            "Добавлено. Введите следующее ФИО или «-» если больше никого.",
            reply_markup=DEFAULT_KEYBOARD
        )
        return ASK_FIELD

    # ===== Универсальные поля =====
    if not is_cb:
        if text == "-":
            user_data.setdefault(uid, {})[key] = "-"
        else:
            value = None
            if callable(formatter):
                try:
                    value = formatter(text)
                except Exception:
                    value = None
            elif formatter in (None,):
                value = text

            if value is None:
                await msg.reply_text("❌ Неверный формат. Попробуйте снова.", reply_markup=DEFAULT_KEYBOARD)
                return ASK_FIELD

            user_data.setdefault(uid, {})[key] = value

        if key == "naim_name":
            await msg.reply_text("📍 Теперь регистрация нанимателя.")
        if key == "ar_name":
            await msg.reply_text("📍 Теперь регистрация наймодателя.")

        context.user_data[CTX_STEP] = step + 1
        context.user_data[CTX_SKIP_INLINE_SENT] = False
        await ask_next_field(update, context)
        return ASK_FIELD


# ===============================
# Генерация и отправка файла
# ===============================

async def download_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Собирает контекст, генерирует .docx, отправляет и удаляет файл. Затем предлагает доп. договоры."""
    ensure_outdir()
    uid = uid_from(update)
    data = user_data.get(uid, {})

    # Безопасные дефолты
    ctx = {k: (v if v not in (None, "") else "-") for k, v in data.items()}

    def _dash_to_underscores(ctx: dict) -> dict:
        out = dict(ctx)
        for k, v in list(out.items()):
            if v == "-":
                if k in EMPTY_IF_DASH:
                    out[k] = ""  # полностью убираем
                else:
                    width = UNDERSCORE_WIDTHS.get(k, 20)  # дефолтная длина
                    out[k] = "_" * width
        return out

    ctx = _dash_to_underscores(ctx)

    # 3) документ права — строим ИЗ ctx
    doc_choice = data.get("doc_choice")
    if doc_choice == "egrn":
        ctx["name_of_document"] = "Выписка из ЕГРН,"
        ctx["document_value"] = ctx.get("obj_kadastr", "-")
    elif doc_choice == "cert":
        ctx["name_of_document"] = "Свидетельство о государственной регистрации права,"
        series = ctx.get("cert_series", "-")
        number = ctx.get("cert_number", "-")
        ctx["document_value"] = f"серия {series} № {number}"
    else:  # skip
        ctx["name_of_document"] = "-"
        ctx["document_value"] = "-"

    # 4) финальный проход — чтобы name_of_document/document_value тоже стали подчёркнутыми
    ctx = _dash_to_underscores(ctx)

    # Имя файла
    def surname(fullname: str | None) -> str:
        if not fullname or fullname == "-":
            return "unknown"
        return fullname.split()[0]

    ar_surname = surname(data.get("ar_name"))
    naim_surname = surname(data.get("naim_name"))
    filename = f"договор_{ar_surname}_{naim_surname}.docx"
    out_path = os.path.join(OUTPUT_DIR, filename)

    # Рендер и отправка
    try:
        fill_template(ctx, TEMPLATE_PATH, out_path)
    except Exception as e:
        await update.effective_message.reply_text(f"Ошибка генерации файла: {e}")
        return

    try:
        await update.effective_message.reply_document(document=open(out_path, "rb"), filename=filename)
    finally:
        try:
            os.remove(out_path)
        except OSError:
            pass

    # Предложить заполнить доп. договоры
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Комиссия наниматель", callback_data=CB_DOC_COMM_TENANT),
            InlineKeyboardButton("Комиссия соб", callback_data=CB_DOC_COMM_SOB),
        ],
        [InlineKeyboardButton("Пропустить", callback_data=CB_SKIP_COMM)]
    ])
    await update.effective_message.reply_text(
        "Заполнить ли данные в дополнительных договорах?",
        reply_markup=kb
    )





# ===============================
# Точка входа: main()
# ===============================

def build_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(
                button_handler,
                pattern=f"^({CB_INSTRUCTION}|{CB_START_RENT}|{CB_CONFIRM_RESTART}|{CB_CONTINUE}|{CB_DOC_COMM_TENANT}|{CB_DOC_COMM_SOB}|{CB_SKIP_COMM})$"

            ),
        ],
        states={
            ASK_FIELD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, on_user_input),
                CallbackQueryHandler(
                    on_user_input,
                    pattern=f"^({CB_PAYER_TENANT}|{CB_PAYER_LANDLORD}|{CB_YES}|{CB_NO}|{CB_DEFAULT_CONDITION}|{CB_DOC_EGRN}|{CB_DOC_CERT}|{CB_SKIP_ADDR}|{CB_SKIP_DOC})$"

                ),
            ]
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True,
    )


def main() -> None:
    """Инициализация и запуск бота."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ensure_outdir()
    token = get_token()

    app = Application.builder().token(token).build()

    conv = build_conversation()
    app.add_handler(conv)

    # Доп. обработчик на всякий случай: кнопки меню вне ConversationHandler
    app.add_handler(CallbackQueryHandler(button_handler, pattern=f"^({CB_INSTRUCTION}|{CB_START_RENT}|{CB_CONFIRM_RESTART}|{CB_CONTINUE})$"))

    app.run_polling(close_loop=False)

# Для запуска напрямую: python main.py
if __name__ == "__main__":
    main()
