




#!/usr/bin/env python3
"""
Telegram-бот автопостинга.

Поток:
  1. Пишешь боту идею поста.
  2. Бот спрашивает, в какие соцсети (кнопки: ТГ / ВК / Дзен, можно 1/2/3).
  3. Генерация: фактчек с веб-поиском (Sonnet) -> черновик (Haiku) -> критик (Sonnet).
  4. Бот присылает готовые тексты + кнопки [Опубликовать] [Переписать] [Отмена].
  5. По «Опубликовать» постит в выбранные сети (гейт сохранён — без подтверждения не постит).

Запуск:
  pip install -r requirements.txt
  python bot.py

Локально и на сервере запускается одинаково. Конфиг — config.json рядом.
"""
import json
import re
import sys
import traceback
from pathlib import Path

import requests

import generator
from social_poster import post_telegram, post_vk, post_site
from generator import LABELS

HERE = Path(__file__).resolve().parent
NETWORKS = ["telegram", "vk", "zen"]

sessions = {}  # chat_id -> {stage, idea, networks, drafts, note, photo_file_id}


def load_cfg():
    p = HERE / "config.json"
    if not p.exists():
        sys.exit("Нет config.json. Скопируй config.example.json и заполни.")
    return json.loads(p.read_text(encoding="utf-8"))


# ---------- Telegram API ----------

def tg(cfg, method, payload):
    url = f"https://api.telegram.org/bot{cfg['telegram_bot_token']}/{method}"
    r = requests.post(url, json=payload, timeout=60)
    return r.json()


def send(cfg, chat_id, text, markup=None, parse_mode=None):
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if markup:
        payload["reply_markup"] = markup
    if parse_mode:
        payload["parse_mode"] = parse_mode
    return tg(cfg, "sendMessage", payload)


def edit_text(cfg, chat_id, message_id, text, markup=None):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text,
               "disable_web_page_preview": True}
    if markup:
        payload["reply_markup"] = markup
    return tg(cfg, "editMessageText", payload)


def answer_cb(cfg, cb_id, text=None):
    payload = {"callback_query_id": cb_id}
    if text:
        payload["text"] = text
    return tg(cfg, "answerCallbackQuery", payload)


def download_tg_file(cfg, file_id):
    """Скачивает файл (фото) из Telegram по file_id, возвращает bytes."""
    url = tg_file_url(cfg, file_id)
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    return resp.content


def tg_file_url(cfg, file_id):
    """Прямой URL файла Telegram (используется как cover для сайта — сайт скачает картинку)."""
    r = tg(cfg, "getFile", {"file_id": file_id})
    if not r.get("ok"):
        raise RuntimeError(f"getFile: {r}")
    path = r["result"]["file_path"]
    return f"https://api.telegram.org/file/bot{cfg['telegram_bot_token']}/{path}"


# ---------- Клавиатуры ----------

def network_keyboard(selected):
    rows = [[{"text": ("✅ " if n in selected else "▫️ ") + LABELS[n],
              "callback_data": f"net:{n}"}] for n in NETWORKS]
    rows.append([{"text": "🚀 Сгенерировать", "callback_data": "gen"},
                 {"text": "❌ Отмена", "callback_data": "cancel"}])
    return {"inline_keyboard": rows}


def review_keyboard():
    return {"inline_keyboard": [[
        {"text": "✅ Опубликовать", "callback_data": "publish"},
        {"text": "✏️ Переписать", "callback_data": "rewrite"},
        {"text": "❌ Отмена", "callback_data": "cancel"},
    ]]}


def blocked_keyboard():
    return {"inline_keyboard": [[
        {"text": "⚠️ Всё равно написать (с пометкой)", "callback_data": "force"},
    ], [
        {"text": "❌ Отмена", "callback_data": "cancel"},
    ]]}


VERDICT_BADGE = {
    "confirmed": "✅ Факты подтверждены",
    "partial": "⚠️ Подтверждено частично — проверь оговорки в посте",
    "unconfirmed": "🚫 Факты НЕ подтверждены — публикуешь на свой риск",
}


# ---------- Логика ----------

def is_allowed(cfg, user_id):
    """Пускаем владельца и всех из telegram_allowed_ids. Если список пуст — пускаем всех."""
    allowed = {str(cfg.get("telegram_owner_id", "")).strip()}
    allowed.update(str(i).strip() for i in cfg.get("telegram_allowed_ids", []))
    allowed.discard("")
    return (not allowed) or str(user_id) in allowed


def _render_draft(n, d):
    """Текст превью под площадку. zen — это объект статьи (title/summary/body)."""
    if n == "zen" and isinstance(d, dict):
        plain = re.sub(r"<[^>]+>", "", d.get("body", ""))  # убрать HTML-теги для превью
        seo = (
            f"\n\n🔎 SEO\n"
            f"title: {d.get('meta_title', '—')}\n"
            f"desc: {d.get('meta_description', '—')}\n"
            f"slug: {d.get('slug', '—')}"
        )
        return f"📰 {d.get('title', '')}\n{d.get('summary', '')}\n\n{plain}{seo}".strip()
    return str(d or "(пусто)")


def drafts_message(result, networks):
    parts = []
    badge = VERDICT_BADGE.get(result.get("verdict", ""))
    if badge:
        parts.append(badge)
    if result.get("note"):
        parts.append(f"ℹ️ {result['note']}")
    body = "\n\n".join(
        f"━━━ {LABELS[n]} ━━━\n{_render_draft(n, result['drafts'].get(n))}" for n in networks
    )
    return ("\n".join(parts) + "\n\n" + body) if parts else body


def run_generation(cfg, chat_id, message_id, sess, force=False):
    nets = [n for n in NETWORKS if n in sess["networks"]]
    edit_text(cfg, chat_id, message_id, "⏳ Проверяю факты и пишу посты…")
    try:
        result = generator.generate(cfg, sess["idea"], nets, force=force)
    except Exception as e:
        traceback.print_exc()
        send(cfg, chat_id, f"⚠️ Ошибка генерации: {e}\n\nПопробуй ещё раз или пришли новую идею.")
        sessions.pop(chat_id, None)
        return
    if result.get("blocked"):
        sess["stage"] = "blocked"
        brief = result.get("brief", "")[:1500]
        send(cfg, chat_id,
             "🚫 <b>Факты не подтвердились</b> — публиковать рискованно.\n\n"
             f"{brief}\n\n"
             "Уточни формулировку и пришли заново, либо напиши пост с явной пометкой "
             "«не подтверждено».",
             markup=blocked_keyboard(), parse_mode="HTML")
        return
    sess["drafts"] = result["drafts"]
    sess["note"] = result.get("note", "")
    sess["verdict"] = result.get("verdict", "")
    sess["stage"] = "review"
    send(cfg, chat_id, drafts_message(result, nets), markup=review_keyboard())
    if result.get("image_prompt"):
        send(cfg, chat_id,
             "🎨 <b>Промпт для картинки</b> (Nano Banana Pro):\n\n"
             f"<code>{result['image_prompt']}</code>\n\n"
             "Сгенерируй картинку, <b>пришли её сюда в чат</b> — и нажми ✅ Опубликовать "
             "(можно опубликовать и без фото).",
             parse_mode="HTML")


def handle_message(cfg, msg):
    chat_id = msg["chat"]["id"]
    user_id = msg.get("from", {}).get("id")
    text = (msg.get("text") or "").strip()
    if not is_allowed(cfg, user_id):
        return

    # Фото — прикладываем к текущему посту
    if msg.get("photo"):
        sess = sessions.get(chat_id)
        if not sess:
            send(cfg, chat_id, "Сначала пришли идею и сгенерируй пост, потом фото к нему.")
            return
        sess["photo_file_id"] = msg["photo"][-1]["file_id"]  # самое большое разрешение
        send(cfg, chat_id, "📷 Фото получено — приложу к посту. Жми ✅ Опубликовать.")
        return

    if text in ("/start", "/help"):
        send(cfg, chat_id,
             "Привет! Пришли идею или новость для поста — я напишу тексты под соцсети, "
             "проверю факты и после твоего подтверждения опубликую.")
        return
    if not text:
        return
    sessions[chat_id] = {"stage": "selecting", "idea": text,
                         "networks": {"telegram"}, "drafts": {}, "note": "",
                         "photo_file_id": None}
    send(cfg, chat_id,
         f"Идея принята:\n«{text[:200]}»\n\nВ какие соцсети публикуем?",
         markup=network_keyboard(sessions[chat_id]["networks"]))


def handle_callback(cfg, cb):
    data = cb.get("data", "")
    msg = cb["message"]
    chat_id = msg["chat"]["id"]
    message_id = msg["message_id"]
    user_id = cb.get("from", {}).get("id")
    if not is_allowed(cfg, user_id):
        answer_cb(cfg, cb["id"])
        return
    sess = sessions.get(chat_id)
    if not sess:
        answer_cb(cfg, cb["id"], "Сессия истекла, пришли идею заново")
        return

    if data == "cancel":
        sessions.pop(chat_id, None)
        answer_cb(cfg, cb["id"], "Отменено")
        edit_text(cfg, chat_id, message_id, "❌ Отменено. Пришли новую идею, когда будешь готов.")
        return

    if data.startswith("net:"):
        n = data.split(":", 1)[1]
        if n in sess["networks"]:
            sess["networks"].discard(n)
        else:
            sess["networks"].add(n)
        answer_cb(cfg, cb["id"])
        tg(cfg, "editMessageReplyMarkup", {
            "chat_id": chat_id, "message_id": message_id,
            "reply_markup": network_keyboard(sess["networks"])})
        return

    if data == "gen":
        if not sess["networks"]:
            answer_cb(cfg, cb["id"], "Выбери хотя бы одну соцсеть")
            return
        answer_cb(cfg, cb["id"], "Генерирую…")
        run_generation(cfg, chat_id, message_id, sess)
        return

    if data == "rewrite":
        answer_cb(cfg, cb["id"], "Переписываю…")
        run_generation(cfg, chat_id, message_id, sess)
        return

    if data == "force":
        answer_cb(cfg, cb["id"], "Пишу с пометкой…")
        run_generation(cfg, chat_id, message_id, sess, force=True)
        return

    if data == "publish":
        answer_cb(cfg, cb["id"], "Публикую…")
        nets = [n for n in NETWORKS if n in sess["networks"]]
        photo_file_id = sess.get("photo_file_id")
        # Для ВК нужны байты фото — скачиваем один раз
        photo_bytes = None
        if photo_file_id and "vk" in nets:
            try:
                photo_bytes = download_tg_file(cfg, photo_file_id)
            except Exception:
                traceback.print_exc()
                photo_bytes = None
        # Обложка для сайта: фото из чата как cover (сайт сам его скачает)
        cover_url = None
        if photo_file_id and "zen" in nets:
            try:
                cover_url = tg_file_url(cfg, photo_file_id)
            except Exception:
                traceback.print_exc()
                cover_url = None
        results = []
        for n in nets:
            draft = sess["drafts"].get(n)
            try:
                if n == "telegram":
                    text = (draft or "").strip()
                    if not text:
                        results.append(f"{LABELS[n]}: пусто, пропущено")
                        continue
                    results.append(post_telegram(cfg, text, photo_file_id=photo_file_id))
                elif n == "vk":
                    text = (draft or "").strip()
                    if not text:
                        results.append(f"{LABELS[n]}: пусто, пропущено")
                        continue
                    results.append(post_vk(cfg, text, photo_bytes=photo_bytes))
                elif n == "zen":
                    art = draft if isinstance(draft, dict) else {}
                    if not art.get("title") or not art.get("body"):
                        results.append(f"{LABELS[n]}: пусто, пропущено")
                        continue
                    results.append(post_site(cfg, art["title"], art["body"],
                                             summary=art.get("summary"), cover_url=cover_url,
                                             source="ImpactLaunch",
                                             meta_title=art.get("meta_title"),
                                             meta_description=art.get("meta_description"),
                                             slug=art.get("slug")))
            except Exception as e:  # одна площадка не валит остальные
                results.append(f"{LABELS[n]}: ОШИБКА — {e}")
        send(cfg, chat_id, "📣 Результат:\n" + "\n".join(results))
        sessions.pop(chat_id, None)
        return


def main():
    cfg = load_cfg()
    me = tg(cfg, "getMe", {})
    if not me.get("ok"):
        sys.exit(f"Неверный telegram_bot_token: {me}")
    print(f"Бот запущен: @{me['result']['username']}. Ctrl+C для остановки.")
    offset = None
    while True:
        try:
            resp = tg(cfg, "getUpdates", {"offset": offset, "timeout": 30})
        except requests.RequestException:
            continue
        for u in resp.get("result", []):
            offset = u["update_id"] + 1
            try:
                if "message" in u:
                    handle_message(cfg, u["message"])
                elif "callback_query" in u:
                    handle_callback(cfg, u["callback_query"])
            except Exception:
                traceback.print_exc()


if __name__ == "__main__":
    main()