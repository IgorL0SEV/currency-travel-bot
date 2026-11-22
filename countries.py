"""Страны, их валюты и распознавание ввода пользователя."""

from __future__ import annotations

import re
from dataclasses import dataclass


def _normalize(text: str) -> str:
    text = (text or "").strip().lower().replace("ё", "е")
    text = re.sub(r"[^a-zа-я0-9\- ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True)
class Country:
    key: str
    name: str
    currency: str
    flag: str

    @property
    def button(self) -> str:
        return f"{self.flag} {self.name}"


# (название, код валюты, флаг, дополнительные написания через пробел)
_RAW: tuple[tuple[str, str, str, str], ...] = (
    ("Россия", "RUB", "🇷🇺", "рф russia рус"),
    ("Беларусь", "BYN", "🇧🇾", "белоруссия belarus"),
    ("Казахстан", "KZT", "🇰🇿", "kazakhstan"),
    ("Кыргызстан", "KGS", "🇰🇬", "киргизия киргизстан kyrgyzstan"),
    ("Узбекистан", "UZS", "🇺🇿", "uzbekistan"),
    ("Таджикистан", "TJS", "🇹🇯", "tajikistan"),
    ("Туркменистан", "TMT", "🇹🇲", "туркмения turkmenistan"),
    ("Армения", "AMD", "🇦🇲", "armenia"),
    ("Азербайджан", "AZN", "🇦🇿", "azerbaijan"),
    ("Грузия", "GEL", "🇬🇪", "georgia"),
    ("Молдова", "MDL", "🇲🇩", "молдавия moldova"),
    ("Украина", "UAH", "🇺🇦", "ukraine"),
    ("Турция", "TRY", "🇹🇷", "turkey turkiye"),
    ("Египет", "EGP", "🇪🇬", "egypt"),
    ("ОАЭ", "AED", "🇦🇪", "эмираты uae дубай dubai"),
    ("Саудовская Аравия", "SAR", "🇸🇦", "саудовская"),
    ("Катар", "QAR", "🇶🇦", "qatar"),
    ("Израиль", "ILS", "🇮🇱", "israel"),
    ("Иордания", "JOD", "🇯🇴", "jordan"),
    ("Китай", "CNY", "🇨🇳", "кнр china"),
    ("Япония", "JPY", "🇯🇵", "japan"),
    ("Южная Корея", "KRW", "🇰🇷", "корея korea"),
    ("Индия", "INR", "🇮🇳", "india"),
    ("Таиланд", "THB", "🇹🇭", "тайланд thailand пхукет"),
    ("Вьетнам", "VND", "🇻🇳", "vietnam"),
    ("Индонезия", "IDR", "🇮🇩", "indonesia бали bali"),
    ("Малайзия", "MYR", "🇲🇾", "malaysia"),
    ("Сингапур", "SGD", "🇸🇬", "singapore"),
    ("Филиппины", "PHP", "🇵🇭", "philippines"),
    ("Шри-Ланка", "LKR", "🇱🇰", "шри ланка sri lanka"),
    ("Италия", "EUR", "🇮🇹", "italy"),
    ("Испания", "EUR", "🇪🇸", "spain"),
    ("Германия", "EUR", "🇩🇪", "germany"),
    ("Франция", "EUR", "🇫🇷", "france"),
    ("Португалия", "EUR", "🇵🇹", "portugal"),
    ("Греция", "EUR", "🇬🇷", "greece"),
    ("Кипр", "EUR", "🇨🇾", "cyprus"),
    ("Мальта", "EUR", "🇲🇹", "malta"),
    ("Австрия", "EUR", "🇦🇹", "austria"),
    ("Бельгия", "EUR", "🇧🇪", "belgium"),
    ("Нидерланды", "EUR", "🇳🇱", "голландия netherlands"),
    ("Финляндия", "EUR", "🇫🇮", "finland"),
    ("Ирландия", "EUR", "🇮🇪", "ireland"),
    ("Словения", "EUR", "🇸🇮", "slovenia"),
    ("Словакия", "EUR", "🇸🇰", "slovakia"),
    ("Литва", "EUR", "🇱🇹", "lithuania"),
    ("Латвия", "EUR", "🇱🇻", "latvia"),
    ("Эстония", "EUR", "🇪🇪", "estonia"),
    ("Хорватия", "EUR", "🇭🇷", "croatia"),
    ("Черногория", "EUR", "🇲🇪", "montenegro"),
    ("Сербия", "RSD", "🇷🇸", "serbia"),
    ("Болгария", "BGN", "🇧🇬", "bulgaria"),
    ("Румыния", "RON", "🇷🇴", "romania"),
    ("Польша", "PLN", "🇵🇱", "poland"),
    ("Чехия", "CZK", "🇨🇿", "czechia"),
    ("Венгрия", "HUF", "🇭🇺", "hungary"),
    ("Швейцария", "CHF", "🇨🇭", "switzerland"),
    ("Великобритания", "GBP", "🇬🇧", "англия uk britain"),
    ("Швеция", "SEK", "🇸🇪", "sweden"),
    ("Норвегия", "NOK", "🇳🇴", "norway"),
    ("Дания", "DKK", "🇩🇰", "denmark"),
    ("Исландия", "ISK", "🇮🇸", "iceland"),
    ("США", "USD", "🇺🇸", "америка usa united states"),
    ("Канада", "CAD", "🇨🇦", "canada"),
    ("Мексика", "MXN", "🇲🇽", "mexico"),
    ("Бразилия", "BRL", "🇧🇷", "brazil"),
    ("Аргентина", "ARS", "🇦🇷", "argentina"),
    ("Чили", "CLP", "🇨🇱", "chile"),
    ("Перу", "PEN", "🇵🇪", "peru"),
    ("Австралия", "AUD", "🇦🇺", "australia"),
    ("Новая Зеландия", "NZD", "🇳🇿", "new zealand"),
    ("ЮАР", "ZAR", "🇿🇦", "южная африка south africa"),
    ("Марокко", "MAD", "🇲🇦", "morocco"),
    ("Тунис", "TND", "🇹🇳", "tunisia"),
    ("Алжир", "DZD", "🇩🇿", "algeria"),
    ("Кения", "KES", "🇰🇪", "kenya"),
    ("Танзания", "TZS", "🇹🇿", "tanzania"),
    ("Маврикий", "MUR", "🇲🇺", "mauritius"),
    ("Сейшелы", "SCR", "🇸🇨", "seychelles"),
    ("Мальдивы", "MVR", "🇲🇻", "maldives"),
    ("Гонконг", "HKD", "🇭🇰", "hong kong"),
    ("Тайвань", "TWD", "🇹🇼", "taiwan"),
    ("Монголия", "MNT", "🇲🇳", "mongolia"),
)

CURRENCY_NAMES: dict[str, str] = {
    "RUB": "российский рубль",
    "BYN": "белорусский рубль",
    "KZT": "тенге",
    "KGS": "сом",
    "UZS": "сум",
    "TJS": "сомони",
    "TMT": "манат",
    "AMD": "драм",
    "AZN": "азербайджанский манат",
    "GEL": "лари",
    "MDL": "молдавский лей",
    "UAH": "гривна",
    "TRY": "турецкая лира",
    "EGP": "египетский фунт",
    "AED": "дирхам ОАЭ",
    "SAR": "саудовский риял",
    "QAR": "катарский риал",
    "ILS": "шекель",
    "JOD": "иорданский динар",
    "CNY": "китайский юань",
    "JPY": "японская иена",
    "KRW": "южнокорейская вона",
    "INR": "индийская рупия",
    "THB": "тайский бат",
    "VND": "вьетнамский донг",
    "IDR": "индонезийская рупия",
    "MYR": "малайзийский ринггит",
    "SGD": "сингапурский доллар",
    "PHP": "филиппинское песо",
    "LKR": "шри-ланкийская рупия",
    "EUR": "евро",
    "RSD": "сербский динар",
    "BGN": "болгарский лев",
    "RON": "румынский лей",
    "PLN": "польский злотый",
    "CZK": "чешская крона",
    "HUF": "венгерский форинт",
    "CHF": "швейцарский франк",
    "GBP": "фунт стерлингов",
    "SEK": "шведская крона",
    "NOK": "норвежская крона",
    "DKK": "датская крона",
    "ISK": "исландская крона",
    "USD": "доллар США",
    "CAD": "канадский доллар",
    "MXN": "мексиканское песо",
    "BRL": "бразильский реал",
    "ARS": "аргентинское песо",
    "CLP": "чилийское песо",
    "PEN": "перуанский соль",
    "AUD": "австралийский доллар",
    "NZD": "новозеландский доллар",
    "ZAR": "южноафриканский рэнд",
    "MAD": "марокканский дирхам",
    "TND": "тунисский динар",
    "DZD": "алжирский динар",
    "KES": "кенийский шиллинг",
    "TZS": "танзанийский шиллинг",
    "MUR": "маврикийская рупия",
    "SCR": "сейшельская рупия",
    "MVR": "мальдивская руфия",
    "HKD": "гонконгский доллар",
    "TWD": "тайваньский доллар",
    "MNT": "монгольский тугрик",
}

CURRENCY_CODES = frozenset(CURRENCY_NAMES)

_BY_KEY: dict[str, Country] = {}
_ALIASES: dict[str, str] = {}

for _name, _currency, _flag, _aliases in _RAW:
    _key = _normalize(_name)
    _BY_KEY[_key] = Country(key=_key, name=_name, currency=_currency, flag=_flag)
    _ALIASES[_key] = _key
    for _alias in _aliases.split():
        _ALIASES[_normalize(_alias)] = _key

POPULAR_HOME: tuple[str, ...] = (
    "россия",
    "казахстан",
    "беларусь",
    "армения",
    "грузия",
    "узбекистан",
    "кыргызстан",
    "таджикистан",
    "молдова",
    "азербайджан",
)

POPULAR_DEST: tuple[str, ...] = (
    "турция",
    "египет",
    "оаэ",
    "таиланд",
    "китай",
    "италия",
    "испания",
    "германия",
    "франция",
    "грузия",
    "армения",
    "сербия",
    "черногория",
    "вьетнам",
    "казахстан",
    "беларусь",
)


def find_country(text: str) -> Country | None:
    """Ищет страну по названию или алиасу.

    Код валюты (например, ``PEN``) возвращает «страну» с названием кода —
    так можно работать со странами, которых нет в списке.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    code = raw.upper()
    if code in CURRENCY_CODES:
        return Country(key=code.lower(), name=code, currency=code, flag="🌐")
    key = _ALIASES.get(_normalize(raw))
    return _BY_KEY.get(key) if key else None


def get_country(key: str) -> Country | None:
    return _BY_KEY.get(_normalize(key)) or find_country(key)


def currency_name(code: str) -> str:
    return CURRENCY_NAMES.get(code.upper(), code.upper())


def popular_home() -> list[Country]:
    return [country for key in POPULAR_HOME if (country := get_country(key))]


def popular_dest() -> list[Country]:
    return [country for key in POPULAR_DEST if (country := get_country(key))]
