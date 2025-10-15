# ===============================
# form_logic.py — форматирование и валидации
# ===============================

# === Импорты и константы ===
import re
from datetime import datetime, date

from babel.dates import format_date as babel_format_date
from num2words import num2words
from docxtpl import DocxTemplate



# Шаблон для акта приёма-передачи
DEFAULT_ACT_CONDITION = (
    "Оборудование, мебель, техника и инженерные системы проверены, дефектов не выявлено."
)

# Регулярные выражения
KADASTR_RE = re.compile(r"^\d{2}:\d{2}:\d{7}:\d{4}$")  # Кадастровый номер
HAS_LETTER_RE = re.compile(r"[A-Za-zА-Яа-яЁё]")        # Проверка букв
HAS_DIGIT_RE = re.compile(r"\d")                       # Проверка цифр
MONTHS_GEN = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]

# === Блок 1. Денежные суммы и числа ===
def _to_int_amount(s: str) -> int | None:
    """Преобразует строку в целое число (например '47 000' -> 47000)."""
    if s is None:
        return None
    s = str(s).strip().replace(" ", "")
    if not s.isdigit():
        return None
    return int(s)


def format_money(raw: str) -> str | None:
    """
    Возвращает строку вида: 47 000 (сорок семь тысяч).
    В скобках — сумма прописью без слова 'рублей'.
    """
    amount = _to_int_amount(raw)
    if amount is None:
        return None
    words = num2words(amount, lang="ru")
    num_grouped = f"{amount:,}".replace(",", " ")
    return f"{num_grouped} ({words})"


# === Блок 2. Даты ===
def parse_date(raw: str) -> date | None:
    """Парсинг даты в формате ДД.ММ.ГГГГ или ДД.ММ.ГГ."""
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            dt = datetime.strptime(raw, fmt).date()
            if fmt == "%d.%m.%y" and dt.year < 2000:
                dt = date(dt.year + 2000, dt.month, dt.day)
            return dt
        except ValueError:
            continue
    return None


def format_date(raw: str) -> str | None:
    """Форматирует дату для документа: «02» февраля 2002 г. (без зависимости от Babel)."""
    d = parse_date(raw)
    if d is None:
        return None
    day_quoted = f"«{d:%d}»"
    month_gen = MONTHS_GEN[d.month - 1]
    return f"{day_quoted} {month_gen} {d.year} г."



def format_date_iso(raw: str) -> str | None:
    """Форматирует дату в ISO-вид (2002-02-02)."""
    d = parse_date(raw)
    return d.isoformat() if d else None


# === Блок 3. ФИО и тексты ===
def _titlecase_preserve_hyphen(s: str) -> str:
    """Приводит к виду с заглавной буквой, сохраняя дефисы."""
    parts = str(s).strip().split("-")
    tc = " - ".join(p.strip().capitalize() for p in parts)
    return tc.replace(" - ", "-")


def format_fio(raw: str) -> str | None:
    """Форматирует ФИО: каждое слово с заглавной буквы, дефисы сохраняются."""
    if not raw or not raw.strip():
        return None
    parts = [p for p in raw.strip().split() if p]
    if not parts:
        return None
    return " ".join(_titlecase_preserve_hyphen(p) for p in parts)


def to_upper(raw: str) -> str | None:
    """Переводит строку в ВЕРХНИЙ РЕГИСТР (например, 'кем выдан')."""
    if raw is None:
        return None
    return str(raw).strip().upper()


# === Блок 4. Адреса ===
def format_location(raw: str) -> str | None:
    """Форматирует элемент адреса (город, улица, дом)."""
    if not raw or not raw.strip():
        return None
    return _titlecase_preserve_hyphen(raw)


def validate_street_and_house(street: str, house: str) -> tuple[str, str] | None:
    """
    Проверяет правильность улицы и дома.
    Улица должна содержать буквы, дом — цифры. Нормализует значения.
    """
    if not street or not house:
        return None
    s = street.strip()
    h = house.strip().replace(" ", "")

    if not HAS_LETTER_RE.search(s):
        return None
    if not HAS_DIGIT_RE.search(h):
        return None

    s_norm = _titlecase_preserve_hyphen(s)
    h_norm = (
        h.replace("корп.", "к").replace("корп", "к")
         .replace("стр.", "с").replace("стр", "с")
    )
    return (s_norm, h_norm)

# === Блок 5. Кадастр, да/нет, жильцы, плательщики, счётчики ===
def format_kadastr(raw: str) -> str | None:
    """Проверка и формат для кадастрового номера вида 00:00:0000000:0000."""
    if not raw:
        return None
    s = str(raw).strip()
    return s if KADASTR_RE.match(s) else None


def format_yes_no(raw: str) -> str | None:
    """
    Нормализует да/нет для опций в договоре.
    Возвращает 'Разрешено' или 'Запрещено'.
    """
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in ("да", "yes", "y", "разрешено", "1", "true", "истина"):
        return "Разрешено"
    if s in ("нет", "no", "n", "запрещено", "0", "false", "ложь"):
        return "Запрещено"
    return None


def format_payer_choice(raw: str) -> str | None:
    """
    Нормализует выбор плательщика.
    Возвращает 'Наниматель' или 'Наймодатель'.
    """
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in ("наниматель", "tenant", "1", "нан"):
        return "Наниматель"
    if s in ("наймодатель", "landlord", "2", "найм"):
        return "Наймодатель"
    return None


def format_tenant_name_or_dash(raw: str) -> str | None:
    """ФИО совместно проживающего или '-' для пропуска."""
    if raw is None:
        return None
    s = str(raw).strip()
    if s == "-":
        return "-"
    return format_fio(s)


def preserve_numeric_string(raw: str) -> str | None:
    """
    Сохраняет числовую строку как есть, включая ведущие нули.
    Подходит для показаний счётчиков и похожих полей.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    # Разрешаем формат '00123', '001.7', '0001,5'
    if re.fullmatch(r"[0-9]+([.,][0-9]+)?", s):
        return s
    return None


def format_keys_count(raw: str) -> str | None:
    """Количество ключей: целое число > 0. Возвращает строку исходного числа."""
    if raw is None:
        return None
    s = str(raw).strip()
    if s.isdigit() and int(s) > 0:
        return s
    return None

# === Блок 6. Сборка адреса и утилиты ===
def compose_full_address(city: str | None,
                         street: str | None,
                         house: str | None,
                         building: str | None = None,
                         flat: str | None = None) -> str | None:
    """
    Склеивает полный адрес в формате:
    'г. Город ул. Улица д. Дом к. Корпус кв. Квартира'
    Пустые части пропускаются.
    """
    if not city or not street or not house:
        return None
    parts = [
        f"г. {format_location(city)}",
        f"ул. {format_location(street)}",
        f"д. {house}",
    ]
    if building and building.strip() != "-":
        parts.append(f"к. {str(building).strip()}")
    if flat and flat.strip() != "-":
        parts.append(f"кв. {str(flat).strip()}")
    return " ".join(parts)


def ensure_not_empty(value: str | None) -> str:
    """Заменяет None/пустое на '-' для безопасной подстановки в шаблон."""
    if value is None:
        return "-"
    s = str(value).strip()
    return s if s else "-"


def normalize_money_no_rubles(raw: str) -> str | None:
    """
    Обёртка для денег: возвращает '47 000 (сорок семь тысяч)'.
    Полезна, если нужна явная нормализация перед шаблоном.
    """
    return format_money(raw)


# === Блок 7. Рендер DOCX-шаблона ===
def fill_template(context: dict, template_path: str, output_path: str) -> str:
    """
    Подставляет context в DOCX-шаблон и сохраняет результат.
    Все отсутствующие ключи добиваются '-' для устойчивости.
    Возвращает путь к сохранённому файлу.
    """
    # Безопасная подстановка: добиваем '-' для отсутствующих переменных
    # (docxtpl терпит лишние ключи, но упадёт при отсутствии ожидаемых)
    doc = DocxTemplate(template_path)

    # Получаем список переменных из шаблона и заполняем пропуски '-'
    # doc.get_undeclared_template_variables() есть не всегда, поэтому
    # используем атрибуты jinja2:
    try:
        expected = set(doc.get_undeclared_template_variables())
    except Exception:
        # Фолбэк: если метод недоступен, подставляем контекст как есть
        expected = set()

    ctx = dict(context) if context else {}
    if expected:
        for k in expected:
            if k not in ctx:
                ctx[k] = "-"

    # Рендер
    doc.render(ctx)
    doc.save(output_path)
    return output_path