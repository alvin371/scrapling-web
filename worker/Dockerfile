FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Minimal runtime libs; playwright install-deps (called by scrapling install) handles the rest
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgtk-3-0 \
    libx11-xcb1 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /worker

# constraints.txt = exact versions of the image running in production (pip freeze,
# 2026-11-10); requirements.txt stays loose so upgrades are a deliberate edit here.
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt

# Install Chromium (via Playwright) + Camoufox + BrowserForge fingerprint databases
RUN scrapling install

COPY . .

CMD ["python", "worker.py"]
