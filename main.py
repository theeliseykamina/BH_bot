import os
import logging
import re
from datetime import datetime

from docxtpl import DocxTemplate
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
from dotenv import load_dotenv

from form_logic import (
    format_date as custom_format_date,
    format_money,
    format_fio,
    format_location,
    to_upper,
    validate_street_and_house,
    fill_template,
    wrap_conditions_to_rows,
    split_money_parts,
)

from fields import FIELDS


ASK_FIELD = 1
user_data: dict[int, dict] = {}

DEFAULT_KEYBOARD = ReplyKeyboardMarkup(
    [["↩️ Назад", "-"], ["Скачать файл", "/start"]],
    resize_keyboard=True,
    one_time_keyboard=False,
    selective=True,
)

CB_INSTRUCTION = "instruction"
CB_HELP = "help"
CB_ABOUT = "about"
CB_BACK_TO_MENU = "back_to_menu"
CB_START_RENT = "start_rent"
CB_CONFIRM_RESTART = "confirm_restart"
CB_CONTINUE = "continue"
CB_PAYER_TENANT = "наниматель"
CB_PAYER_LANDLORD = "наймодатель"
CB_YES = "разрешено"
CB_NO = "запрещено"
CB_DEFAULT_CONDITION = "default_condition"
CB_DOC_EGRN = "doc_egrn"
CB_DOC_CERT = "doc_cert"
CB_DOC_COMM_TENANT = "doc_comm_tenant"
CB_DOC_COMM_SOB = "doc_comm_sob"
CB_SKIP_DOC = "skip_doc"
CB_SKIP_ADDR = "skip_addr"
CB_SKIP_COMM = "skip_comm"
CB_GO_BACK = "go_back"

CTX_STEP = "step"
CTX_SKIP_INLINE_SENT = "skip_inline_sent"
CTX_SHOW_KEYBOARD_ONCE = "show_keyboard_once"
CTX_MAIN_SENT = "main_contract_sent"

TEMPLATE_PATH = "template 3.docx"
TEMPLATE_OKAZ_PATH = "template_okaz.docx"
TEMPLATE_SOB_PATH = "template_sob.docx"
OUTPUT_DIR = "out"

def wrap_to_lines(text: str, max_len: int, lines: int) -> list[str]:
    words = re.findall(r'\S+', (text or "").strip())
    out = [''] * lines
    if not words:
        return out

    li = 0
    cur = []
    cur_len = 0

    for w in words:
        add = (1 if cur else 0) + len(w)
        if cur_len + add <= max_len:
            cur.append(w)
            cur_len += add
        else:
            out[li] = ' '.join(cur)
            li += 1
            if li >= lines:
                return out
            cur = [w]
            cur_len = len(w)

    if li < lines:
        out[li] = ' '.join(cur)

    return out

def get_token() -> str:
    load_dotenv()
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN не найден в окружении/.env")
    return token


def ensure_outdir() -> None:
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)


def check_templates_on_startup() -> None:
    templates = [
        ("Основной договор", TEMPLATE_PATH),
        ("Комиссия наниматель", TEMPLATE_OKAZ_PATH),
        ("Комиссия собственник", TEMPLATE_SOB_PATH),
    ]

    print("\n" + "=" * 50)
    print("🔍 Проверка шаблонов...")
    print("=" * 50)

    all_ok = True

    for name, path in templates:
        if not os.path.exists(path):
            print(f"⚠️  WARNING: Шаблон не найден: {name}")
            print(f"   Путь: {path}")
            all_ok = False
            continue

        try:
            doc = DocxTemplate(path)
            vars_in_template = doc.get_undeclared_template_variables()
            print(f"✅ {name}: {len(vars_in_template)} переменных")
        except Exception as e:
            print(f"❌ ERROR: Не удалось прочитать {name}")
            print(f"   Ошибка: {e}")
            all_ok = False

    print("=" * 50)
    if all_ok:
        print("✅ Все шаблоны проверены успешно\n")
    else:
        print("⚠️  Обнаружены проблемы с шаблонами")
        print("   Бот запустится, но могут быть ошибки генерации\n")


def uid_from(update: Update) -> int:
    if update.effective_user:
        return update.effective_user.id
    if update.message and update.message.from_user:
        return update.message.from_user.id
    raise RuntimeError("Не удалось определить user_id")


def reset_to_start(context: ContextTypes.DEFAULT_TYPE, uid: int) -> None:
    context.user_data[CTX_STEP] = None
    context.user_data[CTX_SKIP_INLINE_SENT] = False
    context.user_data.pop(CTX_MAIN_SENT, None)
    user_data.pop(uid, None)

async def send_start_menu(target: Message) -> None:
    text = (
        "🤖 **BHBot | Автозаполнение договоров аренды**\n\n"
        "Привет! Я помогу составить договор найма жилья.\n\n"
        "✨ **Что умею:**\n"
        "• Автоматическое форматирование данных\n"
        "• Проверка корректности ввода\n"
        "• Генерация договора, актов, комиссий"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(text="помощь", callback_data=CB_HELP),
            InlineKeyboardButton(text="о проекте", callback_data=CB_ABOUT),
        ],
        [
            InlineKeyboardButton(text="новый договор аренды", callback_data=CB_START_RENT),
        ]
    ])
    await target.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")


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

    await send_start_menu(update.effective_message)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "📘 **Помощь и инструкция**\n\n"
        "**Основные команды:**\n"
        "/start — вернуться в главное меню\n"
        "/help — показать эту справку\n\n"
        "**Как пользоваться ботом:**\n"
        "1️⃣ Отвечайте на вопросы последовательно\n"
        "2️⃣ Используйте «-» для пропуска любого поля\n"
        "3️⃣ Кнопка «↩️ Назад» вернёт на предыдущий шаг\n"
        "4️⃣ «Скачать файл» — досрочная генерация договора\n\n"
        "✨ **Что умеет бот:**\n"
        "• Автоматическое форматирование данных (ФИО, даты, суммы, адреса)\n"
        "• Проверка корректности ввода\n"
        "• Генерация договора аренды + акты + комиссии\n\n"
        "💡 **Нашли баг или есть предложения?**\n"
        "Пишите в канал: t.me/theeliseykamina"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Назад в меню", callback_data=CB_BACK_TO_MENU)]
    ])
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")

async def go_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = uid_from(update)
    step = context.user_data.get(CTX_STEP, 0)

    if step == 0:
        await update.effective_message.reply_text(
            "⚠️ Вы уже на первом вопросе.",
            reply_markup=DEFAULT_KEYBOARD
        )
        return ASK_FIELD

    if context.user_data.get(CTX_MAIN_SENT):
        await update.effective_message.reply_text(
            "⚠️ Форма уже завершена. Нажмите /start для создания нового договора.",
            reply_markup=DEFAULT_KEYBOARD
        )
        return ASK_FIELD

    current_field = FIELDS[step]
    key = current_field["key"]
    formatter = current_field.get("formatter")

    if formatter in ("multi_address_naim", "multi_address_ar"):
        phase_key = f"{key}_phase"
        temp_key = f"{key}_temp"
        phase = context.user_data.get(phase_key)

        if phase is None:
            await go_back_to_previous_field(update, context, uid, step, key)
            return ASK_FIELD

        phases = ["city", "street", "house", "building", "flat"]
        current_idx = phases.index(phase) if phase in phases else 0

        if current_idx == 0:
            context.user_data.pop(phase_key, None)
            context.user_data.pop(temp_key, None)
            user_data.get(uid, {}).pop(key, None)
            await go_back_to_previous_field(update, context, uid, step, key)
            return ASK_FIELD
        else:
            prev_phase = phases[current_idx - 1]
            context.user_data[phase_key] = prev_phase
            temp = context.user_data.get(temp_key, {})
            temp.pop(phase, None)

            prompts = {
                "city": "Город регистрации (пример: Москва):",
                "street": "Улица регистрации (пример: Барочная):",
                "house": "Дом (например: 10, 10А, 10/2):",
                "building": "Корпус (если нет — напишите «-»):",
                "flat": "Квартира (Пример: 777):"
            }
            await update.effective_message.reply_text(
                prompts.get(prev_phase, "Введите данные:"),
                reply_markup=DEFAULT_KEYBOARD
            )
            return ASK_FIELD

    if formatter == "multi_address_obj":
        phase_key = f"{key}_phase"
        temp_key = f"{key}_temp"
        phase = context.user_data.get(phase_key)

        if phase is None:
            await go_back_to_previous_field(update, context, uid, step, key)
            return ASK_FIELD

        phases = ["street", "house", "building", "flat"]
        current_idx = phases.index(phase) if phase in phases else 0

        if current_idx == 0:
            context.user_data.pop(phase_key, None)
            context.user_data.pop(temp_key, None)
            ud = user_data.get(uid, {})
            for k in ["obj_address", "obj_street", "obj_house", "obj_building", "obj_flat"]:
                ud.pop(k, None)
            await go_back_to_previous_field(update, context, uid, step, key)
            return ASK_FIELD
        else:
            prev_phase = phases[current_idx - 1]
            context.user_data[phase_key] = prev_phase
            temp = context.user_data.get(temp_key, {})
            temp.pop(phase, None)

            prompts = {
                "street": "Улица (пример: Тверская):",
                "house": "Дом (например: 10, 10к2, 10/2):",
                "building": "Корпус (если нет — напишите «-»):",
                "flat": "Квартира (число или «-»):"
            }
            await update.effective_message.reply_text(
                prompts.get(prev_phase, "Введите данные:"),
                reply_markup=DEFAULT_KEYBOARD
            )
            return ASK_FIELD

    if formatter == "multi_tenants":
        buf_key = f"{key}_buf"
        buf = context.user_data.get(buf_key, [])

        if not buf:
            user_data.get(uid, {}).pop("obj_tenants_list", None)
            context.user_data.pop(buf_key, None)
            await go_back_to_previous_field(update, context, uid, step, key)
            return ASK_FIELD
        else:
            buf.pop()
            context.user_data[buf_key] = buf
            await update.effective_message.reply_text(
                f"↩️ Последнее ФИО удалено. Осталось: {len(buf)}\n"
                "Введите следующее ФИО или «-» для завершения.",
                reply_markup=DEFAULT_KEYBOARD
            )
            return ASK_FIELD

    if formatter == "multi_conditions":
        buf_key = f"{key}_buf"
        buf = context.user_data.get(buf_key, [])

        if not buf:
            user_data.get(uid, {}).pop(key, None)
            context.user_data.pop(buf_key, None)
            await go_back_to_previous_field(update, context, uid, step, key)
            return ASK_FIELD
        else:
            buf.pop()
            context.user_data[buf_key] = buf
            await update.effective_message.reply_text(
                f"↩️ Последний пункт удалён. Осталось: {len(buf)}\n"
                "Введите следующий пункт или «-» для завершения.",
                reply_markup=DEFAULT_KEYBOARD
            )
            return ASK_FIELD

    await go_back_to_previous_field(update, context, uid, step, key)
    return ASK_FIELD


async def go_back_to_previous_field(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        uid: int,
        current_step: int,
        current_key: str
) -> None:
    user_data.get(uid, {}).pop(current_key, None)
    prev_step = current_step - 1

    choice = user_data.get(uid, {}).get("doc_choice")
    skip_fields = set()

    if choice == "skip":
        skip_fields.update(["obj_kadastr", "cert_series", "cert_number"])
    elif choice == "egrn":
        skip_fields.update(["cert_series", "cert_number"])
    elif choice == "cert":
        skip_fields.add("obj_kadastr")

    while prev_step >= 0:
        prev_key = FIELDS[prev_step]["key"]
        if prev_key not in skip_fields:
            break
        prev_step -= 1

    if prev_step < 0:
        prev_step = 0

    prev_key = FIELDS[prev_step]["key"]
    user_data.get(uid, {}).pop(prev_key, None)

    context.user_data[CTX_SKIP_INLINE_SENT] = False
    context.user_data[CTX_STEP] = prev_step
    context.user_data[CTX_SHOW_KEYBOARD_ONCE] = True

    await update.effective_message.reply_text("↩️ Возвращаемся к предыдущему вопросу...")
    await ask_next_field(update, context)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    data = query.data
    await query.answer()

    if data == CB_HELP:
        text = (
            "📘 **Помощь и инструкция**\n\n"
            "**Основные команды:**\n"
            "/start — вернуться в главное меню\n"
            "/help — показать эту справку\n\n"
            "**Как пользоваться ботом:**\n"
            "1️⃣ Отвечайте на вопросы последовательно\n"
            "2️⃣ Используйте «-» для пропуска любого поля\n"
            "3️⃣ Кнопка «↩️ Назад» вернёт на предыдущий шаг\n"
            "4️⃣ «Скачать файл» — досрочная генерация договора\n\n"
            "✨ **Что умеет бот:**\n"
            "• Автоматическое форматирование данных (ФИО, даты, суммы, адреса)\n"
            "• Проверка корректности ввода\n"
            "• Генерация договора аренды + акты + комиссии\n\n"
            "💡 **Нашли баг или есть предложения?**\n"
            "Пишите в канал: t.me/theeliseykamina"
        )
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ назад", callback_data=CB_BACK_TO_MENU)]
            ]),
            parse_mode="Markdown"
        )
        return

    if data == CB_ABOUT:
        text = (
            "👨‍💻 **О проекте**\n\n"
            "Привет! Меня зовут **Елисей**, я Python-разработчик.\n\n"
            "Этот бот — часть моего портфолио. Я создаю автоматизированные решения для бизнеса: "
            "боты, веб-приложения, интеграции.\n\n"
            "📢 **Мой Telegram-канал:**\n"
            "t.me/theeliseykamina\n\n"
            "Там я делюсь обновлениями проектов, кейсами и полезными инструментами для автоматизации бизнеса.\n\n"
            "💼 **По вопросам сотрудничества пишите в канал!**"
        )
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ назад", callback_data=CB_BACK_TO_MENU)]
            ]),
            parse_mode="Markdown"
        )
        return

    if data == CB_BACK_TO_MENU:
        text = (
            "🤖 **BHBot | Автозаполнение договоров аренды**\n\n"
            "Привет! Я помогу составить договор найма жилья.\n\n"
            "✨ **Что умею:**\n"
            "• Автоматическое форматирование данных\n"
            "• Проверка корректности ввода\n"
            "• Генерация договора, актов, комиссий"
        )
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(text="помощь", callback_data=CB_HELP),
                    InlineKeyboardButton(text="о проекте", callback_data=CB_ABOUT),
                ],
                [
                    InlineKeyboardButton(text="новый договор аренды", callback_data=CB_START_RENT),
                ]
            ]),
            parse_mode="Markdown"
        )
        return

    if data == CB_INSTRUCTION:
        text = (
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

    if data == CB_DOC_COMM_TENANT:
        uid = uid_from(update)
        data_map = user_data.get(uid, {})
        ctx = {k: (v if v not in (None, "") else "") for k, v in data_map.items()}

        doc_choice = data_map.get("doc_choice")
        if doc_choice == "egrn":
            ctx["name_of_document"] = "Выписка из ЕГРН,"
            ctx["document_value"] = data_map.get("obj_kadastr", "")
        elif doc_choice == "cert":
            ctx["name_of_document"] = "Свидетельство о государственной регистрации права,"
            series = data_map.get("cert_series", "")
            number = data_map.get("cert_number", "")
            ctx["document_value"] = f"серия {series} № {number}".strip()
        else:
            ctx["name_of_document"] = ""
            ctx["document_value"] = ""

        ensure_outdir()
        filename = "договор_комиссия_наниматель.docx"
        out_path = os.path.join(OUTPUT_DIR, filename)

        try:
            fill_template(ctx, TEMPLATE_OKAZ_PATH, out_path)
            with open(out_path, "rb") as fh:
                await query.message.chat.send_document(document=fh, filename=filename)
            await query.edit_message_text("✅ Отправлен договор: комиссия от нанимателя.")
        except Exception as e:
            logging.error(f"Failed to generate commission tenant doc for user {uid}", exc_info=True)
            await query.edit_message_text("⚠️ Ошибка при формировании документа. Сообщите разработчику.")
            return ConversationHandler.END
        finally:
            try:
                if os.path.exists(out_path):
                    os.remove(out_path)
            except OSError:
                pass

        reset_to_start(context, uid)
        await send_start_menu(query.message)
        return ConversationHandler.END

    if data == CB_DOC_COMM_SOB:
        uid = uid_from(update)
        data_map = user_data.get(uid, {})
        ctx = {k: (v if v not in (None, "") else "") for k, v in data_map.items()}

        doc_choice = data_map.get("doc_choice")
        if doc_choice == "egrn":
            ctx["name_of_document"] = "Выписка из ЕГРН,"
            ctx["document_value"] = data_map.get("obj_kadastr", "")
        elif doc_choice == "cert":
            ctx["name_of_document"] = "Свидетельство о государственной регистрации права,"
            series = data_map.get("cert_series", "")
            number = data_map.get("cert_number", "")
            ctx["document_value"] = f"серия {series} № {number}".strip()
        else:
            ctx["name_of_document"] = ""
            ctx["document_value"] = ""

        ensure_outdir()
        filename = "договор_комиссия_собственник.docx"
        out_path = os.path.join(OUTPUT_DIR, filename)

        try:
            fill_template(ctx, TEMPLATE_SOB_PATH, out_path)
            with open(out_path, "rb") as fh:
                await query.message.chat.send_document(document=fh, filename=filename)
            await query.edit_message_text("✅ Отправлен договор: комиссия от наймодателя.")
        except Exception as e:
            logging.error(f"Failed to generate commission landlord doc for user {uid}", exc_info=True)
            await query.edit_message_text("⚠️ Ошибка при формировании документа. Сообщите разработчику.")
            return ConversationHandler.END
        finally:
            try:
                if os.path.exists(out_path):
                    os.remove(out_path)
            except OSError:
                pass

        reset_to_start(context, uid)
        await send_start_menu(query.message)
        return ConversationHandler.END

    if data == CB_SKIP_COMM:
        uid = uid_from(update)
        await query.edit_message_text("Дополнительные договоры пропущены.")
        reset_to_start(context, uid)
        await send_start_menu(query.message)
        return ConversationHandler.END

async def ask_next_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    step = context.user_data.get(CTX_STEP, 0)
    if step >= len(FIELDS):
        if context.user_data.get(CTX_MAIN_SENT):
            return
        context.user_data[CTX_MAIN_SENT] = True
        await send_preview(update, context)
        return

    uid = uid_from(update)
    current = FIELDS[step]
    key = current["key"]
    choice = user_data.get(uid, {}).get("doc_choice")

    if choice == "skip":
        skip_fields = ("obj_kadastr", "cert_series", "cert_number")
        if key in skip_fields:
            user_data.setdefault(uid, {})[key] = ""
            context.user_data[CTX_STEP] = step + 1
            await ask_next_field(update, context)
            return

    if choice == "egrn" and key in ("cert_series", "cert_number"):
        user_data.setdefault(uid, {})[key] = ""
        context.user_data[CTX_STEP] = step + 1
        await ask_next_field(update, context)
        return

    if choice == "cert" and key == "obj_kadastr":
        user_data.setdefault(uid, {})[key] = ""
        context.user_data[CTX_STEP] = step + 1
        await ask_next_field(update, context)
        return

    field = FIELDS[step]
    question = field["question"]
    formatter = field.get("formatter")
    show_reply = context.user_data.pop(CTX_SHOW_KEYBOARD_ONCE, False)
    reply_kwargs = {"reply_markup": DEFAULT_KEYBOARD} if show_reply else {}

    if formatter == "inline_buttons":
        if not context.user_data.get(CTX_SKIP_INLINE_SENT):
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Наниматель", callback_data=CB_PAYER_TENANT),
                    InlineKeyboardButton("Наймодатель", callback_data=CB_PAYER_LANDLORD),
                ],
                [InlineKeyboardButton("↩️ Назад", callback_data=CB_GO_BACK)]
            ])
            context.user_data[CTX_SKIP_INLINE_SENT] = True
            await update.effective_message.reply_text(question, reply_markup=kb)
        return

    if formatter == "inline_yes_no":
        if not context.user_data.get(CTX_SKIP_INLINE_SENT):
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Разрешено", callback_data=CB_YES),
                    InlineKeyboardButton("Запрещено", callback_data=CB_NO),
                ],
                [InlineKeyboardButton("↩️ Назад", callback_data=CB_GO_BACK)]
            ])
            context.user_data[CTX_SKIP_INLINE_SENT] = True
            await update.effective_message.reply_text(question, reply_markup=kb)
        return

    if formatter == "inline_default_condition":
        if not context.user_data.get(CTX_SKIP_INLINE_SENT):
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🟢 Всё исправно…", callback_data=CB_DEFAULT_CONDITION)],
                [InlineKeyboardButton("↩️ Назад", callback_data=CB_GO_BACK)]
            ])
            context.user_data[CTX_SKIP_INLINE_SENT] = True
            await update.effective_message.reply_text(
                question + "\nМожно ввести текст вручную или нажать кнопку.",
                reply_markup=kb
            )
            return
        return

    if formatter in ("multi_address_naim", "multi_address_ar"):
        if not context.user_data.get(CTX_SKIP_INLINE_SENT):
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("Пропустить адрес", callback_data=CB_SKIP_ADDR)],
                [InlineKeyboardButton("↩️ Назад", callback_data=CB_GO_BACK)]
            ])
            context.user_data[CTX_SKIP_INLINE_SENT] = True
            await update.effective_message.reply_text(
                question + " (пример: Москва)",
                reply_markup=kb
            )
        return

    if formatter == "multi_address_obj":
        if not context.user_data.get(CTX_SKIP_INLINE_SENT):
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("Пропустить адрес", callback_data=CB_SKIP_ADDR)],
                [InlineKeyboardButton("↩️ Назад", callback_data=CB_GO_BACK)]
            ])
            context.user_data[CTX_SKIP_INLINE_SENT] = True
            await update.effective_message.reply_text(question, reply_markup=kb)
        return

    if formatter == "inline_doc_choice":
        if not context.user_data.get(CTX_SKIP_INLINE_SENT):
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("ЕГРН", callback_data=CB_DOC_EGRN),
                    InlineKeyboardButton("Свидетельство", callback_data=CB_DOC_CERT),
                ],
                [InlineKeyboardButton("Пропустить", callback_data=CB_SKIP_DOC)],
                [InlineKeyboardButton("↩️ Назад", callback_data=CB_GO_BACK)]
            ])
            context.user_data[CTX_SKIP_INLINE_SENT] = True
            await update.effective_message.reply_text(question, reply_markup=kb)
        return

    if formatter == "inline_make_act":
        if not context.user_data.get(CTX_SKIP_INLINE_SENT):
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Да", callback_data=CB_YES),
                    InlineKeyboardButton("Нет", callback_data=CB_NO),
                ],
                [InlineKeyboardButton("↩️ Назад", callback_data=CB_GO_BACK)]
            ])
            context.user_data[CTX_SKIP_INLINE_SENT] = True
            await update.effective_message.reply_text(question, reply_markup=kb)
            return
        return

    await update.effective_message.reply_text(question, **reply_kwargs)



async def on_user_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.effective_message
    uid = uid_from(update)
    step = context.user_data.get(CTX_STEP, 0)

    if step is None:
        await send_start_menu(msg)
        return ASK_FIELD

    is_cb = update.callback_query is not None

    if is_cb and update.callback_query.data == CB_GO_BACK:
        await update.callback_query.answer()
        await go_back(update, context)
        return ASK_FIELD

    if msg and msg.text and msg.text.strip() == "↩️ Назад":
        await go_back(update, context)
        return ASK_FIELD

    if msg and msg.text and msg.text.strip().lower() == "скачать файл":
        await msg.reply_text("⏳ Формирую документ...")
        await download_file(update, context)
        reset_to_start(context, uid)
        await send_start_menu(msg)
        return ConversationHandler.END

    if step >= len(FIELDS):
        return ASK_FIELD

    field = FIELDS[step]
    key = field["key"]
    formatter = field.get("formatter")

    cb_data = update.callback_query.data if is_cb else None
    text = None if is_cb else (msg.text or "").strip()

    if formatter == "inline_buttons":
        if not is_cb and text == "-":
            user_data.setdefault(uid, {})[key] = ""
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

    if formatter == "inline_yes_no":
        if not is_cb and text == "-":
            user_data.setdefault(uid, {})[key] = ""
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

        if not is_cb and text:
            user_data.setdefault(uid, {})[key] = text
            context.user_data[CTX_STEP] = step + 1
            context.user_data[CTX_SKIP_INLINE_SENT] = False
            await ask_next_field(update, context)
            return ASK_FIELD
        return ASK_FIELD

    if formatter == "inline_doc_choice":
        if not is_cb and text == "-":
            user_data.setdefault(uid, {})["doc_choice"] = "skip"
            context.user_data[CTX_STEP] = step + 1
            context.user_data[CTX_SKIP_INLINE_SENT] = False
            await msg.reply_text("Пропущено.")
            await ask_next_field(update, context)
            return ASK_FIELD

        if not is_cb:
            return ASK_FIELD

        if cb_data == CB_DOC_EGRN:
            user_data.setdefault(uid, {})["doc_choice"] = "egrn"
            picked = "ЕГРН"
        elif cb_data == CB_DOC_CERT:
            user_data.setdefault(uid, {})["doc_choice"] = "cert"
            picked = "Свидетельство"
        elif cb_data == CB_SKIP_DOC:
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

    if formatter == "inline_make_act":
        if not is_cb:
            return ASK_FIELD

        if cb_data == CB_YES:
            user_data.setdefault(uid, {})[key] = "Да"
            context.user_data[CTX_STEP] = step + 1
            context.user_data[CTX_SKIP_INLINE_SENT] = False
            await update.callback_query.edit_message_text("✅ Акт приёма-передачи будет оформлен.")
            await ask_next_field(update, context)
            return ASK_FIELD

        if cb_data == CB_NO:
            user_data.setdefault(uid, {})[key] = "Нет"

            act_fields = ["act_date", "act_condition", "act_keys", "act_electricity", "act_hot_water", "act_cold_water"]
            for act_field in act_fields:
                user_data.setdefault(uid, {})[act_field] = ""

            context.user_data[CTX_STEP] = len(FIELDS)
            context.user_data[CTX_SKIP_INLINE_SENT] = False
            await update.callback_query.edit_message_text("🚫 Акт приёма-передачи не оформляется.")
            if not context.user_data.get(CTX_MAIN_SENT):
                context.user_data[CTX_MAIN_SENT] = True
                await send_preview(update, context)

            return ASK_FIELD

        return ASK_FIELD

    if formatter in ("multi_address_naim", "multi_address_ar"):
        phase_key = f"{key}_phase"
        temp_key = f"{key}_temp"
        phase = context.user_data.get(phase_key, "city")
        temp = context.user_data.setdefault(temp_key, {})

        if is_cb and cb_data == CB_SKIP_ADDR:
            user_data.setdefault(uid, {})[key] = ""
            context.user_data.pop(phase_key, None)
            context.user_data.pop(temp_key, None)
            context.user_data[CTX_STEP] = step + 1
            context.user_data[CTX_SKIP_INLINE_SENT] = False
            await update.callback_query.edit_message_text("Адрес пропущен.")
            await ask_next_field(update, context)
            return ASK_FIELD

        if is_cb:
            return ASK_FIELD

        if not text:
            await msg.reply_text("Введите текст. Для пропуска используйте «-».")
            return ASK_FIELD

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
            ud = user_data.setdefault(uid, {})
            ud["obj_address"] = ""
            ud["obj_street"] = ""
            ud["obj_house"] = ""
            ud["obj_building"] = ""
            ud["obj_flat"] = ""
            context.user_data.pop(phase_key, None)
            context.user_data.pop(temp_key, None)
            context.user_data[CTX_STEP] = step + 1
            context.user_data[CTX_SKIP_INLINE_SENT] = False
            await update.callback_query.edit_message_text("Адрес объекта пропущен.")
            await ask_next_field(update, context)
            return ASK_FIELD

        phase = context.user_data.get(phase_key, "street")
        temp = context.user_data.setdefault(temp_key, {})

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

            ud = user_data.setdefault(uid, {})
            ud["obj_address"] = full_addr
            ud["obj_street"] = temp.get("street", "")
            ud["obj_house"] = temp.get("house", "")
            ud["obj_building"] = (temp.get("building") if temp.get("building") != "-" else "")
            ud["obj_flat"] = (temp.get("flat") if temp.get("flat") != "-" else "")

            context.user_data.pop(phase_key, None)
            context.user_data.pop(temp_key, None)

            context.user_data[CTX_STEP] = step + 1
            context.user_data[CTX_SKIP_INLINE_SENT] = False
            await ask_next_field(update, context)
            return ASK_FIELD

    if formatter == "multi_conditions":
        buf_key = f"{key}_buf"
        buf = context.user_data.get(buf_key, [])
        if not is_cb and text == "-":
            if not buf:
                user_data.setdefault(uid, {})[key] = ""
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

    if formatter == "multi_tenants":
        buf_key = f"{key}_buf"
        buf = context.user_data.get(buf_key, [])
        if is_cb:
            return ASK_FIELD

        if text == "-":
            user_data.setdefault(uid, {})["obj_tenants_list"] = buf if buf else []
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
            "Добавлено. Введите следующее ФИО или «-», если больше никого.",
            reply_markup=DEFAULT_KEYBOARD
        )
        return ASK_FIELD

    if not is_cb:
        if text == "-":
            user_data.setdefault(uid, {})[key] = ""
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

async def send_preview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = uid_from(update)
    data = user_data.get(uid, {}) or {}
    lines = ["📄 **Предпросмотр договора:**\n"]

    naim_name = data.get("naim_name")
    if naim_name and naim_name not in ("", "-"):
        lines.append(f"**Наниматель:** {naim_name}")

    ar_name = data.get("ar_name")
    if ar_name and ar_name not in ("", "-"):
        lines.append(f"**Наймодатель:** {ar_name}")

    obj_address = data.get("obj_address")
    if obj_address and obj_address not in ("", "-"):
        lines.append(f"**Адрес:** {obj_address}")

    rent_start = data.get("rent_start")
    rent_end = data.get("rent_end")
    if rent_start and rent_end and rent_start not in ("", "-") and rent_end not in ("", "-"):
        lines.append(f"**Срок найма:** {rent_start} — {rent_end}")

    monthly_payment = data.get("monthly_payment")
    monthly_due_day = data.get("monthly_due_day")
    if monthly_payment and monthly_payment not in ("", "-"):
        mc_num, _ = split_money_parts(monthly_payment)
        payment_line = f"**Оплата:** {mc_num} руб/мес" if mc_num else f"**Оплата:** {monthly_payment} руб/мес"
        if monthly_due_day and monthly_due_day not in ("", "-"):
            payment_line += f" (до {monthly_due_day} числа)"
        lines.append(payment_line)

    add_cond = data.get("additional_conditions")
    if add_cond and add_cond not in ("", "-"):
        count = len([line for line in add_cond.splitlines() if line.strip()])
        if count > 0:
            lines.append(f"**Доп. условия:** {count} пункт(ов)")

    text = "\n".join(lines)
    if len(text) > 1000:
        text = text[:997] + "..."

    await update.effective_message.reply_text(text, parse_mode="Markdown")
    await update.effective_message.reply_text("⏳ Формирую документ...")
    await download_file(update, context)


async def download_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = uid_from(update)

    try:
        ensure_outdir()
        data = user_data.get(uid, {}) or {}

        ctx = {}
        for k, v in data.items():
            if v in (None, "", "-"):
                ctx[k] = ""
            else:
                ctx[k] = v

        mc_num, mc_words = split_money_parts(data.get("monthly_payment"))
        ctx["mcnum"] = mc_num or ""
        ctx["monthly_payment"] = mc_words or ""

        dep_num, dep_words = split_money_parts(data.get("deposit_amount"))
        ctx["deposum"] = dep_num or ""
        ctx["deposit_amount"] = dep_words or ""

        act_text = (data.get("act_condition") or "").strip()
        if act_text:
            act_lines = wrap_to_lines(act_text, max_len=75, lines=5)
        else:
            act_lines = [""] * 5
        for i, line in enumerate(act_lines, start=1):
            ctx[f"act{i}"] = line

        raw_add = (data.get("additional_conditions") or "").strip()
        items: list[str] = []
        if raw_add and raw_add != "-":
            for line in raw_add.splitlines():
                s = re.sub(r"^\s*\d+\.\s*", "", line.strip())
                if s and s != "-":
                    items.append(s)
        rows = wrap_conditions_to_rows(items, rows=10, budget_chars=80, with_numbers=True)
        for i in range(10):
            ctx[f"stroka{i + 1}"] = rows[i]

        def pack_two_lines(names: list[str], max1: int = 80, max2: int = 80) -> tuple[str, str]:
            if not names:
                return "", ""
            first, used = [], 0
            cutoff = 0
            for i, name in enumerate(names):
                token = (", " if first else "") + name
                if used + len(token) <= max1:
                    first.append(name);
                    used += len(token)
                else:
                    cutoff = i;
                    break
            else:
                cutoff = len(names)
            rest = names[cutoff:]
            line1 = ", ".join(first)
            if not rest:
                return line1, ""
            second, used2 = [], 0
            for name in rest:
                token = (", " if second else "") + name
                if used2 + len(token) <= max2:
                    second.append(name);
                    used2 += len(token)
                else:
                    if second and (used2 + len(", и др.") <= max2):
                        second.append("и др.")
                    elif not second:
                        second = [name[:max2 - 1] + "…"]
                    break
            return line1, ", ".join(second)

        names = data.get("obj_tenants_list", []) or []
        line1, line2 = pack_two_lines(names, max1=80, max2=80)
        ctx["obj_tenants1"] = line1
        ctx["obj_tenants2"] = line2

        doc_choice = data.get("doc_choice")
        if doc_choice == "egrn":
            ctx["name_of_document"] = "Выписка из ЕГРН,"
            ctx["document_value"] = ctx.get("obj_kadastr", "")
        elif doc_choice == "cert":
            ctx["name_of_document"] = "Свидетельство о государственной регистрации права,"
            series = ctx.get("cert_series", "")
            number = ctx.get("cert_number", "")
            ctx["document_value"] = f"серия {series} № {number}".strip()
        else:
            ctx["name_of_document"] = ""
            ctx["document_value"] = ""

        must_have = {
            "act_date": "", "act_keys": "", "act_electricity": "", "act_hot_water": "", "act_cold_water": "",
            **{f"act{i}": "" for i in range(1, 6)},
            "obj_tenants1": "", "obj_tenants2": "",
            "name_of_document": "", "document_value": "",
            "mcnum": "", "monthly_payment": "", "deposum": "", "deposit_amount": "",
            **{f"stroka{i}": "" for i in range(1, 11)},
        }
        for k, v in must_have.items():
            ctx.setdefault(k, v)

        def surname(fullname: str | None) -> str:
            if not fullname or fullname.strip() in ("", "-"):
                return "unknown"
            return fullname.split()[0]

        ar_surname = surname(data.get("ar_name"))
        naim_surname = surname(data.get("naim_name"))
        filename = f"договор_{ar_surname}_{naim_surname}.docx"
        out_path = os.path.join(OUTPUT_DIR, filename)

        try:
            fill_template(ctx, TEMPLATE_PATH, out_path)
            logging.info(f"Document generated successfully: {filename}")
        except Exception as e:
            logging.error(f"fill_template failed for user {uid}", exc_info=True)
            await update.effective_message.reply_text(
                "⚠️ Ошибка при формировании документа. Сообщите разработчику."
            )
            return

        if not os.path.exists(out_path):
            logging.error(f"Generated file not found: {out_path}")
            await update.effective_message.reply_text(
                "⚠️ Не удалось создать файл договора. Сообщите разработчику."
            )
            return

        try:
            with open(out_path, "rb") as fh:
                await update.effective_message.reply_document(document=fh, filename=filename)
            logging.info(f"Document sent successfully to user {uid}")
        except Exception as e:
            logging.error(f"send_document failed for user {uid}", exc_info=True)
            await update.effective_message.reply_text(
                "⚠️ Ошибка при отправке файла. Повторите команду «Скачать файл»."
            )
            return
        finally:
            try:
                if os.path.exists(out_path):
                    os.remove(out_path)
                    logging.debug(f"Temporary file removed: {out_path}")
            except OSError as e:
                logging.warning(f"Failed to remove temporary file {out_path}: {e}")

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

    except Exception as e:
        logging.error(f"Unexpected error in download_file for user {uid}", exc_info=True)
        await update.effective_message.reply_text(
            "⚠️ Произошла непредвиденная ошибка. Сообщите разработчику."
        )

def build_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(
                button_handler,
                pattern=f"^({CB_HELP}|{CB_ABOUT}|{CB_BACK_TO_MENU}|{CB_INSTRUCTION}|{CB_START_RENT}|{CB_CONFIRM_RESTART}|{CB_CONTINUE}|{CB_DOC_COMM_TENANT}|{CB_DOC_COMM_SOB}|{CB_SKIP_COMM})$"
            ),
        ],
        states={
            ASK_FIELD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, on_user_input),
                CallbackQueryHandler(
                    on_user_input,
                    pattern=f"^({CB_PAYER_TENANT}|{CB_PAYER_LANDLORD}|{CB_YES}|{CB_NO}|{CB_DEFAULT_CONDITION}|{CB_DOC_EGRN}|{CB_DOC_CERT}|{CB_SKIP_ADDR}|{CB_SKIP_DOC}|{CB_GO_BACK})$"
                ),
            ]
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True,
    )

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    check_templates_on_startup()
    ensure_outdir()
    token = get_token()
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))

    app.add_handler(CallbackQueryHandler(
        button_handler,
        pattern=f"^({CB_HELP}|{CB_ABOUT}|{CB_BACK_TO_MENU}|{CB_INSTRUCTION})$"
    ))

    conv = build_conversation()
    app.add_handler(conv)

    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()