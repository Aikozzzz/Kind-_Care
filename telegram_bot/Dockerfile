FROM python:3.12.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY telegram_bot ./telegram_bot
RUN groupadd --gid 10004 kindcare-telegram \
    && useradd --uid 10004 --gid kindcare-telegram --no-create-home kindcare-telegram
RUN mkdir -p /var/lib/kindcare-telegram \
    && chown -R kindcare-telegram:kindcare-telegram /var/lib/kindcare-telegram
ENV TELEGRAM_OFFSET_FILE=/var/lib/kindcare-telegram/offset
USER 10004:10004
CMD ["python", "-m", "telegram_bot.bot"]
