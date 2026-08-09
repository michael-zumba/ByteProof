FROM python:3.12-slim

WORKDIR /app

COPY server/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY server ./server

ENV BYTEPROOF_DATA_DIR=/data
ENV PORT=8000

EXPOSE 8000

CMD ["sh", "-c", "uvicorn server.activation_api:app --host 0.0.0.0 --port ${PORT}"]
