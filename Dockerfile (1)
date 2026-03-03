FROM python:3.11-slim

# Создаём пользователя (HuggingFace требует non-root)
RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH"

WORKDIR /app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user . .

# HuggingFace ожидает порт 7860
EXPOSE 7860

CMD ["python", "bot.py"]
