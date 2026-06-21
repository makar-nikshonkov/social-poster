FROM python:3.12-slim

# Логи сразу в stdout (docker logs), без буфера
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Сначала зависимости — слой кэшируется, пока requirements.txt не менялся
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Затем код (config.json НЕ копируем — он монтируется томом, см. docker-compose.yml)
COPY bot.py generator.py social_poster.py ./

CMD ["python", "bot.py"]
