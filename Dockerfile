# ReadLoops — 跨平台镜像
#
# 构建：docker build -t readloops .
# 运行：docker run -d -p 8000:8000 -v readloops-data:/data --name readloops readloops
#
# 数据持久化在 /data（含数据库），通过 -v 挂载到卷或宿主机目录。

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    READLOOPS_DATA_DIR=/data

WORKDIR /app

# 依赖单独成层：只改代码时无需重装依赖
COPY requirements.txt ./
RUN pip install --upgrade pip setuptools wheel \
    && pip install -r requirements.txt

# 安装应用本体
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-deps .

# 数据目录（数据库存放处）
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).status == 200 else 1)"

CMD ["readloops", "serve", "--host", "0.0.0.0", "--no-browser"]
