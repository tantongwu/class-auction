# Zeabur 會照這個檔案建立並啟動網站
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV PORT=8080
EXPOSE 8080
# 只開 1 個 worker（多個執行緒），讓建立資料表、點數暫存都只有一份
CMD gunicorn "app:create_app()" --bind 0.0.0.0:${PORT} --workers 1 --threads 8 --timeout 60
