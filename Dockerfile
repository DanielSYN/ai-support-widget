FROM python:3.13-slim

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY static static

# Mount a volume here so the SQLite database survives redeploys.
ENV DATABASE_PATH=/data/support.db
VOLUME /data
EXPOSE 8000

# --proxy-headers uses the visitor's real IP for rate limiting. Set FORWARDED_ALLOW_IPS to
# your host's proxy address (or "*" on platforms like Render/Railway where it varies).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
