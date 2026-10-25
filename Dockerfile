FROM node:22-alpine AS frontend
WORKDIR /web
ARG NPM_REGISTRY=https://registry.npmmirror.com
COPY frontend/package.json frontend/package-lock.json ./
RUN npm config set registry ${NPM_REGISTRY} \
    && npm ci
COPY frontend ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATABASE_PATH=/data/gateway.db \
    FRONTEND_DIST=/app/frontend/dist \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
# diagram 能力用子进程调用 vendored Archify（Node CLI）渲染图表，运行阶段需要 Node.js。
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt /tmp/requirements.txt
# browser 能力用 Playwright 驱动无头 Chromium；--with-deps 会安装 Chromium 运行所需的系统库。
RUN pip install --no-cache-dir --index-url ${PIP_INDEX_URL} -r /tmp/requirements.txt \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*
COPY backend /app
COPY --from=frontend /web/dist /app/frontend/dist
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
