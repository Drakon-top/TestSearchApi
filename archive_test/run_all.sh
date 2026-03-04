#!/bin/bash

set -e  # Остановка при ошибке

# Определяем версию Python (приоритет: python3.10 > python3.9 > python3.8 > python3)
# Проверяем как в PATH, так и в /usr/local/bin
PYTHON_CMD="python3"
if command -v python3.10 &> /dev/null || [ -f /usr/local/bin/python3.10 ]; then
    if command -v python3.10 &> /dev/null; then
        PYTHON_CMD="python3.10"
    else
        PYTHON_CMD="/usr/local/bin/python3.10"
    fi
elif command -v python3.9 &> /dev/null; then
    PYTHON_CMD="python3.9"
elif command -v python3.8 &> /dev/null; then
    PYTHON_CMD="python3.8"
fi

PIP_CMD="${PYTHON_CMD} -m pip"

if [ ! -d venv ]; then
    ${PYTHON_CMD} -m venv venv
fi

source venv/bin/activate

# Обновляем pip и setuptools перед установкой зависимостей
# Для Python 3.6 используем более старую версию pip
PYTHON_VERSION=$(${PYTHON_CMD} -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
if [ "$(printf '%s\n' "3.6" "$PYTHON_VERSION" | sort -V | head -n1)" = "3.6" ] && [ "$PYTHON_VERSION" != "3.6" ]; then
    # Python >= 3.7
    ${PIP_CMD} install --upgrade pip setuptools wheel
else
    # Python 3.6 или старше
    ${PIP_CMD} install --upgrade "pip<21.0" "setuptools<50.0" wheel 2>/dev/null || \
    ${PIP_CMD} install --upgrade pip setuptools wheel
fi

# Устанавливаем зависимости с предпочтением бинарных пакетов
# Protobuf файлы были сгенерированы с версией 6.31.1, но в PyPI доступна только до 5.29.6
# Проверка версии отключена в web_search_pb2.py для совместимости
# grpcio файлы были сгенерированы с версией 1.74.0, но зеркало Alibaba может иметь только до 1.70.0
# Проверка версии отключена в web_search_pb2_grpc.py для совместимости
${PIP_CMD} install --prefer-binary -r requirements.txt

# SSL verification: можно установить через переменную окружения SSL_VERIFY (true/false)
# По умолчанию SSL проверка включена (true)
SSL_VERIFY=${SSL_VERIFY:-true}

${PYTHON_CMD} main.py --threads-count $THREADS_COUNT --requests-count $REQUESTS_COUNT --action v2 --groups-on-page 50 --lang en --ssl-verify $SSL_VERIFY --output report_rest_top_50_lang_en.csv
${PYTHON_CMD} main.py --threads-count $THREADS_COUNT --requests-count $REQUESTS_COUNT --action grpc_default --groups-on-page 50 --lang en --ssl-verify $SSL_VERIFY --output report_grpc_default_top_50_lang_en.csv
