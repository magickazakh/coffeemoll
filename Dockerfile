FROM python:3.11-slim

# Отключаем создание лишних файлов кеша питона
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Создаем рабочую папку
WORKDIR /app

# Копируем зависимости и устанавливаем их
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем папку со статикой (HTML, JS, CSS)
# Это ВАЖНО, без этого сервер не увидит ваш сайт
COPY static ./static

# Копируем код бота
COPY . .

# Запускаем
CMD ["python", "bot.py"]
