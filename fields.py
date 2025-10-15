# ===============================
# FIELDS — порядок вопросов
# ===============================

import re  # нужен для нескольких валидаторов из лямбд
from form_logic import (
    format_date,
    format_fio,
    to_upper,
    format_money,
    format_tenant_name_or_dash,
    preserve_numeric_string,
    format_keys_count,
)

FIELDS = [
    # 📄 Основные данные
    {"key": "contract_number", "question": "Введите номер договора (пример: А123):", "formatter": (lambda s: s.strip() if s and s.strip() else None)},
    {"key": "date", "question": "Введите дату договора (пример: 20.03.25):", "formatter": format_date},

    # 👤 Наниматель
    {"key": "naim_name", "question": "Введите ФИО нанимателя:", "formatter": format_fio},
    {"key": "naim_address", "question": "📬 Город регистрации нанимателя:", "formatter": "multi_address_naim"},
    {"key": "naim_passport_series", "question": "📄 Серия паспорта нанимателя (4 цифры):", "formatter": (lambda x: x if x.isdigit() and len(x) == 4 else None)},
    {"key": "naim_passport_number", "question": "📄 Номер паспорта нанимателя (6 цифр):", "formatter": (lambda x: x if x.isdigit() and len(x) == 6 else None)},
    {"key": "naim_passport_issued_by", "question": "📄 Кем выдан паспорт? (пример: ГУ МВД России)", "formatter": to_upper},
    {"key": "naim_passport_issued_date", "question": "📅 Когда выдан паспорт? (пример: 30.01.2020)", "formatter": format_date},

    # 👤 Наймодатель
    {"key": "ar_name", "question": "👤 ФИО наймодателя:", "formatter": format_fio},
    {"key": "ar_address", "question": "📬 Город регистрации наймодателя:", "formatter": "multi_address_ar"},
    {"key": "ar_passport_series", "question": "📄 Серия паспорта наймодателя (4 цифры):", "formatter": (lambda x: x if x.isdigit() and len(x) == 4 else None)},
    {"key": "ar_passport_number", "question": "📄 Номер паспорта наймодателя (6 цифр):", "formatter": (lambda x: x if x.isdigit() and len(x) == 6 else None)},
    {"key": "ar_passport_issued_by", "question": "📄 Кем выдан паспорт наймодателя?", "formatter": to_upper},
    {"key": "ar_passport_issued_date", "question": "📅 Когда выдан паспорт наймодателя?", "formatter": format_date},

    # 📍 Адрес объекта
    {"key": "obj_address", "question": "📍 Адрес объекта (Санкт-Петербург): укажите улицу (пример: Барочная)", "formatter": "multi_address_obj"},
    {"key": "obj_rooms", "question": "🚪 Количество комнат:", "formatter": (lambda x: x if x.isdigit() else None)},
    {"key": "obj_area", "question": "📐 Общая площадь (кв.м):", "formatter": (lambda x: x if re.fullmatch(r"\d+(?:[.,]\d+)?", x) else None)},

    # Выбор документа права
    {"key": "doc_choice", "question": "📄 Подтверждение права: выберите документ", "formatter": "inline_doc_choice"},

    # EГРН / свидетельство — данные
    {"key": "obj_kadastr", "question": "📄 Кадастровый номер (пример: 00:00:0000000:0000):", "formatter": (lambda x: x if re.fullmatch(r"\d{2}:\d{2}:\d{7}:\d{4}", x) else None)},
    {"key": "cert_series", "question": "📄 Серия свидетельства:", "formatter": (lambda s: s.strip() if s and s.strip() != "-" else "-")},
    {"key": "cert_number", "question": "📄 Номер свидетельства:", "formatter": (lambda s: s.strip() if s and s.strip() != "-" else "-")},


    {"key": "obj_tenants", "question": "🏡 Кто проживает с нанимателем? Введите ФИО или '-' если никого:", "formatter": "multi_tenants"},

    # Опции: кнопки «да/нет» с нормализацией в 'Разрешено'/'Запрещено'
    {"key": "obj_animals", "question": "🐕 Содержание животных разрешено?", "formatter": "inline_yes_no"},
    {"key": "obj_smoking", "question": "🚬 Курение в помещении разрешено?", "formatter": "inline_yes_no"},

    # 🗓 Сроки найма
    {"key": "rent_start", "question": "📅 Дата начала найма? (пример: 01.09.2025)", "formatter": format_date},
    {"key": "rent_end", "question": "📅 Дата окончания найма? (пример: 01.09.2026)", "formatter": format_date},

    # 💰 Оплата
    {"key": "monthly_payment", "question": "💸 Ежемесячная плата (в рублях, пример: 30000/30 000):", "formatter": format_money},
    {"key": "deposit_date", "question": "📆 Дата внесения обеспечительного платежа:", "formatter": format_date},
    {"key": "deposit_amount", "question": "💰 Сумма обеспечительного платежа (в рублях, пример: 30000/30 000):", "formatter": format_money},
    {"key": "monthly_due_day", "question": "📅 До какого числа каждого месяца должна быть произведена оплата? (пример: 15)", "formatter": (lambda x: x if x.isdigit() and 1 <= int(x) <= 31 else None)},
    {"key": "payment_utilities", "question": "🏠 Коммунальные услуги оплачивает:", "formatter": "inline_buttons"},
    {"key": "payment_internet", "question": "🌐 Интернет оплачивает:", "formatter": "inline_buttons"},
    {"key": "payment_electricity", "question": "⚡️ Электроэнергию оплачивает:", "formatter": "inline_buttons"},
    {"key": "payment_water", "question": "🚿 ХВС/ГВС оплачивает:", "formatter": "inline_buttons"},
    {"key": "payment_repair", "question": "🔧 Капитальный ремонт оплачивает:", "formatter": "inline_buttons"},

    # 📄 Доп. условия
    {"key": "additional_conditions", "question": "✍️ Дополнительные условия (напишите пункт или '-' если нет):", "formatter": "multi_conditions"},

    # 📋 Акт приёма-передачи
    {"key": "act_make", "question": "📝 Делаем акт приёма-передачи?", "formatter": "inline_make_act"},
    {"key": "act_date", "question": "📅 Дата акта приёма-передачи:", "formatter": format_date},
    {"key": "act_condition", "question": "🏡 Состояние помещения и оборудования?", "formatter": "inline_default_condition"},
    {"key": "act_keys", "question": "🔑 Количество комплектов ключей:", "formatter": format_keys_count},
    {"key": "act_electricity", "question": "⚡️ Показания электросчётчика:", "formatter": preserve_numeric_string},
    {"key": "act_hot_water", "question": "🌡️ Показания счётчика горячей воды:", "formatter": preserve_numeric_string},
    {"key": "act_cold_water", "question": "❄️ Показания счётчика холодной воды:", "formatter": preserve_numeric_string},
]