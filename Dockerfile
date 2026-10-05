
---

## 8️⃣ 🐳 `Dockerfile` (BEST option — Heroku buildpacks ka jhanjhat nahi)

Heroku **Container Registry** wala method use karega. Ye zyada reliable hai.

```dockerfile
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Java + baksmali (system package)
RUN apt-get update && apt-get install -y --no-install-recommends \
        default-jre-headless \
        libsmali-java \
        unzip \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY bot.py extract.py ./

# APKEditor.jar download (optional — code bhi khud download kar leta hai)
RUN curl -L -o /app/APKEditor.jar \
      https://github.com/REAndroid/APKEditor/releases/download/V1.4.9/APKEditor-1.4.9.jar \
    || echo "APKEditor download skipped"

ENV APKEDITOR_URL="" \
    MAX_APK_MB=300

CMD ["python", "bot.py"]
