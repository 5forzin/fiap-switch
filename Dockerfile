FROM node:24-bookworm-slim AS web
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home switch
COPY backend/ backend/
COPY collector/ collector/
COPY installers/controller/ installers/controller/
COPY requirements-controller.txt ./
COPY scripts/create_admin.py scripts/create_admin.py
COPY scripts/setup.py scripts/setup.py
COPY --from=web /app/frontend/dist/ frontend/dist/
RUN mkdir -p /app/data && chown switch:switch /app/data
USER switch
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["uvicorn", "backend.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
