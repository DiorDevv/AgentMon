FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
# Proxy (HTTP_PROXY/HTTPS_PROXY) — Docker'ning maxsus build argumentlari, bu yerda avtomatik ishlaydi.
RUN pip install --no-cache-dir -r requirements.txt

COPY agentmon ./agentmon

RUN useradd --system --uid 10001 agentmon
USER agentmon
