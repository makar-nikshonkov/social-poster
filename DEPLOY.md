# Деплой бота на VPS (Яндекс.Облако, Docker)

Бот — long-poll Telegram-бот, входящих портов не требует. Гоняем в Docker-контейнере
с `restart: always` (автозапуск после перезагрузки ВМ и рестарт при падении).

Заполни плейсхолдеры под себя:
- `<VM_IP>` — публичный IP виртуалки (Console → Compute Cloud → твоя ВМ).
- `<VM_USER>` — пользователь ОС на ВМ (тот, что задавал при создании; для Ubuntu-образов часто `yc-user` или `ubuntu`).
- `<VM_ID>` — идентификатор инстанса (нужен только для шага 1, вариант через `yc`).

---

## 1. Настроить SSH-доступ к ВМ

SSH ещё не настроен — добавим твой публичный ключ на виртуалку.

### 1.1. Сгенерировать ключ локально (если его ещё нет)
```bash
ls ~/.ssh/id_ed25519.pub        # уже есть? — переходи к 1.2
ssh-keygen -t ed25519 -C "social-poster"   # Enter на все вопросы
```

### 1.2. Положить публичный ключ на ВМ
Способ А — через `yc` CLI (если установлен и авторизован):
```bash
# Собираем файл в формате метаданных: "<VM_USER>:<содержимое .pub>"
printf '%s:%s\n' "<VM_USER>" "$(cat ~/.ssh/id_ed25519.pub)" > /tmp/yc-ssh-keys.txt
yc compute instance update --id <VM_ID> --metadata-from-file ssh-keys=/tmp/yc-ssh-keys.txt
```
Гостевой агент Яндекс.Облака подхватит ключ из метаданных за несколько секунд.

Способ Б — через серийную консоль (если `yc` нет): в Console открой ВМ →
«Серийная консоль» → залогинься и вручную добавь ключ в `~/.ssh/authorized_keys`.
(Серийная консоль должна быть включена в настройках ВМ.)

### 1.3. Проверить вход
```bash
ssh <VM_USER>@<VM_IP>
```

---

## 2. Установить Docker на ВМ
Выполняется **на ВМ** (после `ssh`):
```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER      # чтобы docker без sudo
# плагин compose обычно ставится вместе; проверь:
docker compose version
```
Перелогинься (`exit` и снова `ssh`), чтобы группа `docker` применилась.

---

## 3. Перенести код на ВМ
С **локальной машины** (из каталога проекта):

```bash
# Код — из git (config.json в репозиторий не входит, это правильно):
ssh <VM_USER>@<VM_IP> 'git clone https://github.com/makar-nikshonkov/social-poster.git social-poster || (cd social-poster && git pull)'
```

Если git-remote нет — закинь файлы напрямую через scp:
```bash
ssh <VM_USER>@<VM_IP> 'mkdir -p social-poster'
scp Dockerfile docker-compose.yml .dockerignore requirements.txt \
    bot.py generator.py social_poster.py \
    <VM_USER>@<VM_IP>:social-poster/
```

## 4. Перенести config.json (секреты — отдельно!)
`config.json` намеренно НЕ в git и НЕ в образе. Копируем его на ВМ напрямую:
```bash
scp config.json <VM_USER>@<VM_IP>:social-poster/config.json
```
> Внутри `config.json` уже лежат боевые токены (Telegram, Anthropic, VK, site_api_key)
> и список `telegram_allowed_ids`. Файл монтируется в контейнер только на чтение.

---

## 5. Запустить
На ВМ:
```bash
cd social-poster
docker compose up -d --build
docker compose logs -f          # увидеть «Бот запущен: @...», Ctrl+C для выхода из логов
```

---

## 6. Эксплуатация
```bash
docker compose ps               # статус
docker compose logs -f          # логи (ротация: 3 файла по 10 МБ)
docker compose restart          # перезапуск
docker compose down             # остановить
```

Обновить бота после правок кода:
```bash
cd social-poster
git pull                        # или заново scp изменённые файлы
docker compose up -d --build
```

Поменял только `config.json` (например, добавил пользователя в `telegram_allowed_ids`)?
Пересборка не нужна — он монтируется томом, достаточно рестарта:
```bash
docker compose restart
```

---

## Замечания
- **Один бот = один процесс.** Не запускай `python bot.py` локально и контейнер на ВМ
  одновременно — Telegram long-poll отдаёт каждый апдейт только одному получателю,
  будет «дёргаться». Перед переездом останови локальный запуск.
- Входящие порты открывать в Security Group **не нужно** — бот только исходящие запросы.
- Бэкап секретов: храни `config.json` в надёжном месте, в репозитории его нет.
