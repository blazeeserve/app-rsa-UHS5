FROM python:3.10-slim

# Install system dependencies required for APKEditor and baksmali
RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends \
    default-jre-headless \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy dependency list and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all source files (main.py, extract.py, etc.)
COPY . .

# Run as a worker process
CMD ["python", "bot.py"]
