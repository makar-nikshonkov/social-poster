# IMPACTLAUNCH — API создания новостей (инструкция для интеграции)

Документ для внешнего разработчика. Позволяет автоматически создавать новости
на сайте impactlaunch.ru. Принятые новости публикуются в разделе «Новости».

## 1. Доступ

- **Эндпоинт:** `POST https://impactlaunch.ru/api/news`
- **Авторизация:** заголовок `X-Api-Key: <ВАШ_КЛЮЧ>`
- **API-ключ** выдаёт владелец сайта отдельно (безопасным каналом). Храните его в секрете,
  не публикуйте в открытом коде/репозиториях. Запросы шлите только со своего сервера.
- Формат: `Content-Type: application/json`, тело — JSON в кодировке UTF-8.

## 2. Поля запроса

| поле | тип | обяз. | описание |
|---|---|:---:|---|
| `title` | string | **да** | заголовок новости |
| `body` | string (HTML) | **да** | текст. Разрешённые теги: `p, a, b, i, u, s, h1–h4, blockquote, ul, ol, li, figure, figcaption, img, video`. Опасные теги/скрипты вырезаются автоматически |
| `summary` | string | нет | короткий анонс (для карточки и ленты) |
| `cover` | string (URL) | нет | ссылка на обложку. Будет скачана и сохранена. Требования: **ширина ≥ 700px**, формат **JPEG/PNG/GIF** |
| `author` | string | нет | автор |
| `status` | string | нет | `draft` (по умолчанию, на модерацию) или `published` (сразу в публикацию) |
| `published_at` | string | нет | дата/время публикации (ISO-8601, напр. `2026-06-19T15:30:00`). Если пусто и `published` — текущее время |
| `slug` | string | нет | ЧПУ-адрес. По умолчанию генерируется из заголовка |
| `source` | string | нет | название источника |
| `external_url` | string | нет | ссылка на оригинал. **Используется для дедупликации** — см. ниже |
| `meta_title` | string | нет | SEO-заголовок |
| `meta_description` | string | нет | SEO-описание |

### Дедупликация (важно)
Если передать `external_url`, повторная отправка той же статьи **не создаст дубликат** —
существующая новость будет обновлена. Всегда указывайте `external_url`, если опрашиваете
источник периодически.

## 3. Ответы

| Код | Значение | Тело |
|---|---|---|
| `201` | новость создана | `{"ok":true,"action":"created","id":12,"slug":"...","url":"https://impactlaunch.ru/news/.../"}` |
| `200` | обновлена (по `external_url`) | `{"ok":true,"action":"updated","id":12,"slug":"...","url":"..."}` |
| `400` | нет `title`/`body` или невалидный JSON | `{"ok":false,"error":"..."}` |
| `401` | неверный/отсутствует ключ | `{"ok":false,"error":"Unauthorized"}` |
| `422` | проблема с обложкой (не картинка / <700px / неверный формат) | `{"ok":false,"error":"cover: ..."}` |
| `405` | неверный метод | `{"ok":false,"error":"Method not allowed"}` |

## 4. Примеры

### curl
```bash
curl -X POST https://impactlaunch.ru/api/news \
  -H "X-Api-Key: <ВАШ_КЛЮЧ>" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Заголовок новости",
    "summary": "Короткий анонс",
    "body": "<p>Первый абзац.</p><h2>Подзаголовок</h2><p>Ещё текст.</p>",
    "cover": "https://example.com/image-1200x675.jpg",
    "author": "Редакция",
    "status": "published",
    "source": "Название источника",
    "external_url": "https://example.com/original-article-123"
  }'
```

### Python
```python
import requests

resp = requests.post(
    "https://impactlaunch.ru/api/news",
    headers={"X-Api-Key": "<ВАШ_КЛЮЧ>"},
    json={
        "title": "Заголовок новости",
        "summary": "Короткий анонс",
        "body": "<p>Текст новости.</p>",
        "cover": "https://example.com/cover.jpg",
        "status": "published",
        "source": "Название источника",
        "external_url": "https://example.com/article-123",
    },
    timeout=30,
)
print(resp.status_code, resp.json())
```

### Node.js
```javascript
const resp = await fetch("https://impactlaunch.ru/api/news", {
  method: "POST",
  headers: {
    "X-Api-Key": "<ВАШ_КЛЮЧ>",
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    title: "Заголовок новости",
    summary: "Короткий анонс",
    body: "<p>Текст новости.</p>",
    cover: "https://example.com/cover.jpg",
    status: "published",
    source: "Название источника",
    external_url: "https://example.com/article-123",
  }),
});
console.log(resp.status, await resp.json());
```

### Минимальный валидный запрос
```json
{ "title": "Заголовок", "body": "<p>Текст.</p>" }
```
(создастся черновик; для публикации добавьте `"status": "published"`)

## 5. Рекомендации
- Указывайте `external_url` всегда — это защита от дублей.
- Для контроля перед публикацией используйте `status: "draft"`, для авто-публикации — `published`.
- Обложку давайте размером ≥ 700px по ширине (иначе ответ `422`).
- При ошибке `5xx`/таймауте — повторяйте запрос с разумной паузой; благодаря `external_url`
  повтор не создаст дубликат.
- Проверить связь можно `GET https://impactlaunch.ru/api/news` с тем же заголовком `X-Api-Key`
  (вернёт список последних новостей).
