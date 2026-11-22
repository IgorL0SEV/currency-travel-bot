# Справка по API курсов

Документ фиксирует то, что реально используется в `current_api.py`.
Ответы сервисов могут меняться — перед изменением кода сверяйтесь с
официальной документацией.

## Направление курса

Во всём проекте:

```text
rate = сколько единиц quote стоит 1 единица base
```

`get_rate("CNY", "RUB")` → сколько RUB за 1 CNY.
`get_rate("RUB", "CNY")` → сколько CNY за 1 RUB (обратное число).

В интерфейсе бот показывает `1 <валюта поездки> = rate <домашняя валюта>`.

## exchangerate.host

### Получение курсов

```http
GET https://api.exchangerate.host/live
```

| Параметр | Обязателен | Смысл |
|---|---|---|
| `access_key` | да | ключ доступа |
| `source` | нет | базовая валюта, по умолчанию USD |
| `currencies` | нет | целевые валюты строкой через запятую |

Пример:

```text
https://api.exchangerate.host/live?access_key=КЛЮЧ&source=RUB&currencies=CNY,EUR
```

Успешный ответ:

```json
{
  "success": true,
  "terms": "https://exchangerate.host/terms",
  "privacy": "https://exchangerate.host/privacy",
  "timestamp": 1795305752,
  "source": "RUB",
  "quotes": {
    "RUBCNY": 0.0816,
    "RUBEUR": 0.0102
  }
}
```

Ключ пары — `source + quote`, значение — сколько quote за 1 source.

Ответ с ошибкой:

```json
{
  "success": false,
  "error": {
    "code": 101,
    "type": "missing_access_key",
    "info": "You have not supplied an API Access Key."
  }
}
```

Коды, которые разбирает проект:

| Код | Что значит | Реакция бота |
|---|---|---|
| 101 | нет ключа | предложить ключ или резерв |
| 102 | ключ неактивен | сообщение, ручной курс |
| 103 | ключ неверный | сообщение, ручной курс |
| 104 | исчерпан лимит | сообщение о лимите, резерв |
| 105 | тариф не разрешает запрос | сообщение |
| 106 | нет результата для пары | сообщение |
| 201 | неподдерживаемая базовая валюта | сообщение |
| 202 | неподдерживаемая валюта | сообщение |

### Конвертация

```http
GET https://api.exchangerate.host/convert
```

| Параметр | Смысл |
|---|---|
| `access_key` | ключ |
| `from` | исходная валюта |
| `to` | целевая валюта |
| `amount` | сумма |

Ответ содержит `result` — готовую сумму, и `info.rate` — курс.
В проекте конвертация выполняется локально по уже полученному курсу:
это экономит запросы и не создаёт второй источник истины.

### Ограничения

- Бесплатный тариф: 100 запросов в месяц.
- Курсы обновляются примерно раз в сутки, время — UTC.
- Это ориентировочный рыночный курс, не курс покупки/продажи банка.

## open.er-api.com

### Получение курсов

```http
GET https://open.er-api.com/v6/latest/{base}
```

Ключ не требуется. Ответ:

```json
{
  "result": "success",
  "provider": "https://www.exchangerate-api.com",
  "base_code": "RUB",
  "time_last_update_utc": "Sun, 22 Nov 2026 00:02:32 +0000",
  "time_next_update_utc": "Mon, 23 Nov 2026 00:02:32 +0000",
  "rates": {
    "CNY": 0.0816,
    "EUR": 0.0102,
    "RUB": 1
  }
}
```

`rates[quote]` — сколько quote за 1 base. Один запрос отдаёт все валюты.

Ошибка:

```json
{
  "result": "error",
  "error-type": "unsupported-code"
}
```

| `error-type` | Смысл |
|---|---|
| `unsupported-code` | валюта не поддерживается |
| `malformed-request` | запрос не принят |
| `invalid-key` | ключ отклонён (для бесплатного тарифа не нужен) |
| `quota-reached` | исчерпан лимит |

## Сравнение

| Свойство | exchangerate.host | open.er-api.com |
|---|---|---|
| Ключ | нужен | не нужен |
| Бесплатный лимит | 100 запросов/мес | ограниченный, но без ключа |
| Формат курсов | `quotes` с ключом пары | `rates` по коду валюты |
| Несколько валют за запрос | да, через `currencies` | да, весь набор сразу |
| Роль в проекте | основной | резервный |

## Как это встроено в проект

- `ExchangerateHostProvider` — `/live`, проверка `success`, `quotes`.
- `OpenErApiProvider` — `/latest/{base}`, проверка `result`, `rates`.
- `CurrencyAPI.get_rate()` — единая точка: кэш → провайдер → резерв.
- `UsageCounter` — считает фактические запросы по месяцам.
- `RateCache` — хранит курс `TTL` часов в `data/rate_cache.json`.
- Ошибки провайдеров превращаются в `CurrencyAPIError` с понятным текстом.

## Проверка вручную

```powershell
curl "https://api.exchangerate.host/live?access_key=КЛЮЧ&source=CNY&currencies=RUB"
curl "https://open.er-api.com/v6/latest/CNY"
```

Сравните `quotes.CNYRUB` и `rates.RUB` — числа должны совпадать в пределах
округления. Если нет, смотрите на время обновления: сервисы могут
обновляться в разное время суток.
