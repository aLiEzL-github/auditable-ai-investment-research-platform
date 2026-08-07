# Dockerfile —— G0-08 §3.1 文件集第 6 件
# 要求：digest 固定 / 非 root（G0-07 运行时基线）
# digest 实测：2026-08-06 经 Docker Registry API 获取（multi-arch manifest list）
#   sha256:94c50be2dc994b873b55bc123e95e6dbade08095b3dfd790f51c34de3f08cbb7
#   （Colima VM DNS 故障期间以宿主机 curl 实测，非编造；见 G1-00 task-record）

FROM python:3.11-slim@sha256:94c50be2dc994b873b55bc123e95e6dbade08095b3dfd790f51c34de3f08cbb7

# 非 root 运行（G0-07）
RUN useradd --create-home --uid 10001 appuser
WORKDIR /srv/app

COPY requirements.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

COPY backend/ ./backend/

# G1-06：read_only 容器适配 —— 禁止写字节码 + 可写目录走 tmpfs（compose）
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/livez')"

CMD ["python3", "backend/app/main.py", "--bind", "0.0.0.0"]
