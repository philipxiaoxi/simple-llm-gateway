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
    FRONTEND_DIST=/app/frontend/dist
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --index-url ${PIP_INDEX_URL} -r /tmp/requirements.txt
COPY backend /app
COPY --from=frontend /web/dist /app/frontend/dist
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
