# Dockerfile
FROM python:3.12-slim-bookworm

# --- OS deps + Microsoft ODBC Driver 17 + sqlcmd ---
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg2 apt-transport-https ca-certificates \
        unixodbc unixodbc-dev gcc g++ \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
         | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list \
         > /etc/apt/sources.list.d/mssql-release.list \
    && sed -i 's|deb \[|deb [signed-by=/usr/share/keyrings/microsoft-prod.gpg |' \
         /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends \
         msodbcsql17 mssql-tools18 \
    && echo 'export PATH="$PATH:/opt/mssql-tools18/bin"' >> /etc/profile.d/mssql-tools.sh \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# --- Non-root user ---
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

# --- Python deps ---
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

# --- Locale / encoding / unbuffered output ---
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONIOENCODING=utf-8 \
    PYTHONUNBUFFERED=1

# --- App source ---
COPY . /app

# --- Folders + permissions ---
RUN mkdir -p /app/static/uploads /app/static/documents/generated /app/backups \
    && chown -R appuser:appuser /app \
    && chmod +x /app/entrypoint.sh

USER appuser
EXPOSE 5001
ENTRYPOINT ["/app/entrypoint.sh"]

# Production default (dev override in docker-compose.override.yml)
CMD ["gunicorn", "--bind", "0.0.0.0:5001", "--workers", "4", "--threads", "4", \
     "--access-logfile", "-", "--error-logfile", "-", "app:create_app()"]