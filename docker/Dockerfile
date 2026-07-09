FROM apache/superset:6.1.0

USER root
RUN apt-get update && apt-get install -y procps curl wget  && rm -rf /var/lib/apt/lists/*

# 1. 系统依赖 + 翻译工具 + ODBC 18
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gnupg2 ca-certificates nodejs npm && \
    curl https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > /usr/share/keyrings/microsoft-archive-keyring.gpg && \
    echo "deb [arch=amd64,arm64,armhf signed-by=/usr/share/keyrings/microsoft-archive-keyring.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" > /etc/apt/sources.list.d/mssql-release.list && \
    apt-get update && \
    ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 unixodbc-dev && \
    npm install -g po2json prettier && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# 2. Python 依赖（含翻译库 + OpenCC + Babel）
RUN . /app/.venv/bin/activate && \
    uv pip install --no-cache-dir \
    pyodbc pymssql psycopg2-binary pymysql sqlalchemy-trino \
    clickhouse-connect authlib redis tiktoken fastmcp \
    deep-translator opencc-python-reimplemented babel

# 3. 注册 ODBC 驱动
RUN echo "[ODBC Driver 18 for SQL Server]" > /etc/odbcinst.ini \
 && echo "Driver=/opt/microsoft/msodbcsql18/lib64/libmsodbcsql-18.3.so.2.1" >> /etc/odbcinst.ini

# 4. 复制翻译脚本
COPY scripts/translate_po.py /app/scripts/translate_po.py

# 5. 自动翻译 + 简繁转换 + 编译
RUN . /app/.venv/bin/activate && \
    python /app/scripts/translate_po.py \
    --translations-dir /app/superset/translations \
    --source-lang en \
    --target-lang zh-CN \
    --target-po zh \
    --generate-zh-tw \
    --batch-size 20

# 6. 修复 service-worker.js 404
RUN echo "// Superset 6.1.0 stub" > /app/superset/static/service-worker.js && \
    chown superset:superset /app/superset/static/service-worker.js

USER superset
