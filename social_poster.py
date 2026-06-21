#!/usr/bin/env python3
"""
Публикация готовых постов в Telegram и ВК. Дзен - ручная вставка.

Идея: посты пишет Claude в сессии Cowork и кладет их в drafts.json.
Этот скрипт только публикует. По умолчанию dry-run (ничего не постит,
только показывает, что улетит). Реальная публикация - с флагом --confirm.

Использование:
  python social_poster.py                 # dry-run, показать черновики
  python social_poster.py --confirm        # реально опубликовать
  python social_poster.py --only telegram  # только одна площадка

Файлы рядом со скриптом:
  config.json  - токены (см. config.example.json)
  drafts.json  - тексты постов (см. drafts.example.json)
"""
import argparse
import json
import re
import sys
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent


def load_json(name):
    p = HERE / name
    if not p.exists():
        sys.exit(f"Нет файла {name}. Возьми пример из {name.replace('.json', '.example.json')}")
    return json.loads(p.read_text(encoding="utf-8"))


def post_telegram(cfg, text, photo_file_id=None):
    base = f"https://api.telegram.org/bot{cfg['telegram_bot_token']}"
    chat_id = cfg["telegram_chat_id"]
    # С фото и коротким текстом — одним сообщением (caption Telegram ≤ 1024 симв.)
    if photo_file_id and len(text) <= 1024:
        r = requests.post(f"{base}/sendPhoto", json={
            "chat_id": chat_id, "photo": photo_file_id,
            "caption": text, "parse_mode": "HTML",
        }, timeout=60)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram: {data.get('description', data)}")
        return f"Telegram: опубликовано с фото (message_id {data['result']['message_id']})"
    # Длинный текст + фото: сначала фото, затем текст отдельным сообщением
    if photo_file_id:
        rp = requests.post(f"{base}/sendPhoto", json={
            "chat_id": chat_id, "photo": photo_file_id}, timeout=60).json()
        if not rp.get("ok"):
            raise RuntimeError(f"Telegram(фото): {rp.get('description', rp)}")
    r = requests.post(f"{base}/sendMessage", json={
        "chat_id": chat_id, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }, timeout=60)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram: {data.get('description', data)}")
    suffix = " (фото отдельным сообщением)" if photo_file_id else ""
    return f"Telegram: опубликовано (message_id {data['result']['message_id']}){suffix}"


def _vk_upload_wall_photo(cfg, photo_bytes):
    """Загружает фото на сервера ВК и возвращает строку вложения photo<owner>_<id>."""
    gid = cfg["vk_group_id"]
    token = cfg["vk_access_token"]
    v = cfg.get("vk_api_version", "5.199")
    # 1. сервер для загрузки
    s = requests.get("https://api.vk.com/method/photos.getWallUploadServer",
                     params={"group_id": gid, "access_token": token, "v": v}, timeout=30).json()
    if "error" in s:
        raise RuntimeError(f"VK(getWallUploadServer): {s['error'].get('error_msg', s['error'])}")
    # 2. заливаем файл
    up = requests.post(s["response"]["upload_url"],
                       files={"photo": ("image.jpg", photo_bytes, "image/jpeg")}, timeout=120).json()
    if not up.get("photo") or up.get("photo") == "[]":
        raise RuntimeError(f"VK(upload): фото не принято — {up}")
    # 3. сохраняем
    sv = requests.get("https://api.vk.com/method/photos.saveWallPhoto",
                      params={"group_id": gid, "server": up["server"], "photo": up["photo"],
                              "hash": up["hash"], "access_token": token, "v": v}, timeout=30).json()
    if "error" in sv:
        raise RuntimeError(f"VK(saveWallPhoto): {sv['error'].get('error_msg', sv['error'])}")
    ph = sv["response"][0]
    return f"photo{ph['owner_id']}_{ph['id']}"


def post_vk(cfg, text, photo_bytes=None):
    body = {
        "owner_id": f"-{cfg['vk_group_id']}",   # минус = постим в сообщество
        "from_group": 1,
        "message": text,
        "access_token": cfg["vk_access_token"],
        "v": cfg.get("vk_api_version", "5.199"),
    }
    if photo_bytes:
        body["attachments"] = _vk_upload_wall_photo(cfg, photo_bytes)
    r = requests.post("https://api.vk.com/method/wall.post", data=body, timeout=60)
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"VK: {data['error'].get('error_msg', data['error'])}")
    suffix = " с фото" if photo_bytes else ""
    return f"VK: опубликовано (post_id {data['response']['post_id']}){suffix}"


def save_zen(text):
    out = HERE / "zen_post.txt"
    out.write_text(text, encoding="utf-8")
    return f"Дзен: текст сохранен в {out.name} для ручной вставки (API нет)"


def _clean_slug(slug):
    """ЧПУ: латиница, цифры и дефисы. Чистим то, что мог прислать генератор."""
    s = re.sub(r"[^a-z0-9-]+", "-", (slug or "").strip().lower())
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:80] or None


def post_site(cfg, title, body_html, summary=None, cover_url=None,
              source=None, external_url=None, status="published",
              meta_title=None, meta_description=None, slug=None):
    """Публикует новость на сайт через API (impactlaunch.ru/api/news).
    Сайт сам отдаёт её в RSS, откуда забирает Дзен.

    meta_title / meta_description — SEO-поля для поисковой выдачи.
    slug — ЧПУ-адрес (латиница); если не задан, сайт сгенерит из заголовка.
    """
    url = cfg.get("site_api_url", "https://impactlaunch.ru/api/news")
    key = cfg.get("site_api_key", "").strip()
    if not key:
        raise RuntimeError("Нет site_api_key в config.json (ключ выдаёт владелец сайта)")

    payload = {"title": title, "body": body_html, "status": status}
    if summary:
        payload["summary"] = summary
    if cover_url:
        payload["cover"] = cover_url
    if source:
        payload["source"] = source
    if external_url:
        payload["external_url"] = external_url
    if meta_title:
        payload["meta_title"] = meta_title.strip()
    if meta_description:
        payload["meta_description"] = meta_description.strip()
    clean_slug = _clean_slug(slug)
    if clean_slug:
        payload["slug"] = clean_slug

    def _send(p):
        r = requests.post(url, headers={"X-Api-Key": key, "Content-Type": "application/json"},
                          json=p, timeout=60)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {"ok": False, "error": f"HTTP {r.status_code}"}

    code, data = _send(payload)
    # если упала только обложка (422) — публикуем без неё, чтобы не терять статью
    if not data.get("ok") and code == 422 and "cover" in payload:
        payload.pop("cover")
        code, data = _send(payload)
        if data.get("ok"):
            return f"Сайт/Дзен: {data.get('action')} без обложки (обложка отклонена) — {data.get('url')}"
    if not data.get("ok"):
        raise RuntimeError(f"Сайт: {data.get('error', code)}")
    return f"Сайт/Дзен: {data.get('action', 'ok')} — {data.get('url', '')}"


PLATFORMS = {
    "telegram": ("Telegram", post_telegram),
    "vk": ("VK", post_vk),
    "zen": ("Дзен", None),  # особый случай, ручная вставка
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm", action="store_true", help="реально опубликовать")
    ap.add_argument("--only", choices=list(PLATFORMS), help="только одна площадка")
    args = ap.parse_args()

    cfg = load_json("config.json")
    drafts = load_json("drafts.json")
    targets = [args.only] if args.only else list(PLATFORMS)

    if not args.confirm:
        print("=== DRY-RUN (ничего не опубликовано, добавь --confirm) ===\n")
        for key in targets:
            text = drafts.get(key, "").strip()
            label = PLATFORMS[key][0]
            print(f"--- {label} ---\n{text or '(пусто, пропущено)'}\n")
        print("Проверь тексты. Для публикации: python social_poster.py --confirm")
        return

    results, errors = [], []
    for key in targets:
        text = drafts.get(key, "").strip()
        label, fn = PLATFORMS[key]
        if not text:
            results.append(f"{label}: пусто, пропущено")
            continue
        try:
            if key == "zen":
                results.append(save_zen(text))
            else:
                results.append(fn(cfg, text))
        except Exception as e:  # одна площадка не должна валить остальные
            errors.append(f"{label}: ОШИБКА - {e}")

    print("=== Результат ===")
    for line in results + errors:
        print(line)
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
