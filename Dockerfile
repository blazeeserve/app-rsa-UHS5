# # ✅ Base image: Best stable Python version
# FROM python:3.11-slim

# # ✅ Set working directory
# WORKDIR /app

# # Install system dependencies
# RUN apt-get update && apt-get install -y \
#     gcc \
#     ffmpeg \
#     aria2 \
#     libffi-dev \
#     build-essential \
#     python3-dev \
#     poppler-utils \
#     && apt-get clean \
#     && rm -rf /var/lib/apt/lists/*






# # ✅ Copy all source code to container
# COPY . .

# # ✅ Make mp4decrypt executable (if present)
# RUN chmod +x /app/tools/mp4decrypt || true

# # # ✅ Upgrade pip and install Python packages
# # RUN pip install --no-cache-dir --upgrade pip \
# #     && pip install --no-cache-dir -r ugbots.txt

# RUN pip3 install --no-cache-dir --upgrade pip \
#     && pip3 install --no-cache-dir --upgrade --requirement ugbots.txt \
#     && python3 -m pip install -U yt-dlp

# # ✅ Remove old pyrogram if exists
# RUN pip uninstall -y pyrogram || true

# # ✅ Install Pyrofork (maintained fork with forum support)
# # RUN pip install --no-cache-dir -U tgcrypto==1.2.5

# # ✅ Final command: start Flask + Bot together
# CMD ["sh", "-c", "gunicorn app:app & python3 main.py"]




# ✅ Base image: Best stable Python version
FROM python:3.11-slim

# ✅ Set working directory
WORKDIR /app

# ✅ Install system dependencies (including libicu-dev for N_m3u8DL-RE)
RUN apt-get update && apt-get install -y \
    gcc \
    ffmpeg \
    aria2 \
    wget \
    tar \
    libicu-dev \
    libffi-dev \
    build-essential \
    python3-dev \
    poppler-utils \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 🛠️ INSTALL N_m3u8DL-RE (v0.5.1-beta)
RUN wget https://github.com/nilaoda/N_m3u8DL-RE/releases/download/v0.5.1-beta/N_m3u8DL-RE_v0.5.1-beta_linux-x64_20251029.tar.gz \
    && tar -xzf N_m3u8DL-RE_v0.5.1-beta_linux-x64_20251029.tar.gz \
    && chmod +x N_m3u8DL-RE \
    && rm -rf N_m3u8DL-RE_v0.5.1-beta_linux-x64_20251029.tar.gz

# ✅ Copy all source code
COPY . .

# ✅ Build libdecoder.so for THIS arch (replaces any stale prebuilt binary)
RUN gcc -O3 -shared -fPIC -o /app/libdecoder.so /app/libdecoder.c

# ✅ Upgrade pip and install Python packages
RUN pip3 install --no-cache-dir --upgrade pip \
    && pip3 install --no-cache-dir --upgrade --requirement ugbots.txt \
    && python3 -m pip install -U yt-dlp


# ✅ Remove old pyrogram if exists
RUN pip uninstall -y pyrogram || true

# ✅ Final command: start Flask + Bot together
CMD ["sh", "-c", "gunicorn app:app & python3 main.py"]
