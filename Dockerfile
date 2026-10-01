FROM python:3.12-slim

WORKDIR /app

COPY server/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY server ./server

ENV BYTEPROOF_DATA_DIR=/data
ENV PORT=8000

EXPOSE 8000

# Render terminates TLS in front of the container: trust its forwarded
# headers so request.client.host is the real customer IP (the rate limiter
# and the logs depend on it).
CMD ["sh", "-c", "uvicorn server.activation_api:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips=*"]
