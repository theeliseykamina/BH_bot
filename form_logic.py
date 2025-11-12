import re
from datetime import datetime, date
from typing import List
import os, subprocess, shlex, platform
from shutil import which
from babel.dates import format_date as babel_format_date
from num2words import num2words
from docxtpl import DocxTemplate




DEFAULT_ACT_CONDITION = (
    "Оборудование, мебель, техника и инженерные системы проверены, дефектов не выявлено."
)


KADASTR_RE = re.compile(r"^\d{2}:\d{2}:\d{7}:\d{4}$")
HAS_LETTER_RE = re.compile(r"[A-Za-zА-Яа-яЁё]")
HAS_DIGIT_RE = re.compile(r"\d")
MONTHS_GEN = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def _to_int_amount(s: str) -> int | None:
    if s is None:
        return None
    s = str(s).strip().replace(" ", "")
    if not s.isdigit():
        return None
    return int(s)


def format_money(raw: str) -> str | None:
    amount = _to_int_amount(raw)
    if amount is None:
        return None
    return f"{amount:,}".replace(",", " ")


def money_words_only(raw: str) -> str:
    amount = _to_int_amount(raw)
    if amount is None:
        return ""
    return num2words(amount, lang="ru")

def split_money_parts(raw: str) -> tuple[str, str]:
    num = format_money(raw)
    words = money_words_only(raw) if num else ""
    return num or "", words



def parse_date(raw: str) -> date | None:
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





def _find_soffice_com() -> str | None:
    # 1) если в PATH
    p = which("soffice.com")
    if p:
        return p
    candidates = [
        r"C:\Program Files\LibreOffice\program\soffice.com",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.com",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None



def format_date(raw: str) -> str | None:
    d = parse_date(raw)
    if d is None:
        return None
    day_quoted = f"«{d:%d}»"
    month_gen = MONTHS_GEN[d.month - 1]
    return f"{day_quoted} {month_gen} {d.year} г."


def format_date_iso(raw: str) -> str | None:
    d = parse_date(raw)
    return d.isoformat() if d else None



def _titlecase_preserve_hyphen(s: str) -> str:
    parts = str(s).strip().split("-")
    tc = " - ".join(p.strip().capitalize() for p in parts)
    return tc.replace(" - ", "-")


def format_fio(raw: str) -> str | None:
    if not raw or not raw.strip():
        return None
    parts = [p for p in raw.strip().split() if p]
    if not parts:
        return None
    return " ".join(_titlecase_preserve_hyphen(p) for p in parts)


def to_upper(raw: str) -> str | None:
    if raw is None:
        return None
    return str(raw).strip().upper()



def format_location(raw: str) -> str | None:
    if not raw or not raw.strip():
        return None
    return _titlecase_preserve_hyphen(raw)


def validate_street_and_house(street: str, house: str) -> tuple[str, str] | None:
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


def format_kadastr(raw: str) -> str | None:
    if not raw:
        return None
    s = str(raw).strip()
    return s if KADASTR_RE.match(s) else None


def format_yes_no(raw: str) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in ("да", "yes", "y", "разрешено", "1", "true", "истина"):
        return "Разрешено"
    if s in ("нет", "no", "n", "запрещено", "0", "false", "ложь"):
        return "Запрещено"
    return None


def format_payer_choice(raw: str) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in ("наниматель", "tenant", "1", "нан"):
        return "Наниматель"
    if s in ("наймодатель", "landlord", "2", "найм"):
        return "Наймодатель"
    return None


def format_tenant_name_or_dash(raw: str) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if s == "-":
        return "-"
    return format_fio(s)


def preserve_numeric_string(raw: str) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    # Разрешаем формат '00123', '001.7', '0001,5'
    if re.fullmatch(r"[0-9]+([.,][0-9]+)?", s):
        return s
    return None


def format_keys_count(raw: str) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if s.isdigit() and int(s) > 0:
        return s
    return None


def compose_full_address(city: str | None,
                         street: str | None,
                         house: str | None,
                         building: str | None = None,
                         flat: str | None = None) -> str | None:

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
    if value is None:
        return "-"
    s = str(value).strip()
    return s if s else "-"


def normalize_money_no_rubles(raw: str) -> str | None:
    return format_money(raw)


def fill_template(context: dict, template_path: str, output_path: str) -> str:
    doc = DocxTemplate(template_path)

    try:
        expected = set(doc.get_undeclared_template_variables())
    except Exception:
        expected = set()

    ctx = dict(context) if context else {}
    if expected:
        for k in expected:
            if k not in ctx:
                ctx[k] = ""
    doc.render(ctx)
    doc.save(output_path)
    return output_path

def wrap_conditions_to_rows(
    items: List[str],
    rows: int = 10,
    budget_chars: int = 80,
    with_numbers: bool = True,
) -> List[str]:
    out = []
    row_idx = 0

    def flush_line(buf: List[str], prefix: str = ""):
        nonlocal out, row_idx
        if row_idx >= rows:
            return
        line = " ".join(buf).strip()
        out.append((prefix + line).strip())
        row_idx += 1
        buf.clear()

    for i, raw in enumerate(items, start=1):
        text = re.sub(r"\s+", " ", raw or "").strip()
        prefix_first = (f"{i}. " if with_numbers else "")
        current_budget = budget_chars - len(prefix_first)
        buf = []

        if not text:
            flush_line([], prefix_first)
            continue

        for w in text.split(" "):
            if not buf and len(w) > current_budget:
                flush_line([w], prefix_first)
                prefix_first = ""
                current_budget = budget_chars
                continue

            proposed_len = (len(" ".join(buf)) + (1 if buf else 0) + len(w)) if buf else len(w)
            if proposed_len <= current_budget:
                buf.append(w)
            else:
                flush_line(buf, prefix_first)
                prefix_first = ""
                current_budget = budget_chars
                if len(w) > current_budget:
                    flush_line([w], "")
                else:
                    buf.append(w)

        if buf and row_idx < rows:
            flush_line(buf, prefix_first)

        if row_idx >= rows:
            break

    while len(out) < rows:
        out.append("")

    return out[:rows]
