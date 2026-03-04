# Archive Test — тестирование поискового API / Search API testing

Проект для нагрузочного и функционального тестирования REST и gRPC поискового API (Yandex Cloud Search API). Генерирует отчёты в CSV.

This project is for load and functional testing of REST and gRPC Search API (Yandex Cloud Search API). It produces CSV reports.

---

## Русский

### Однострочный запуск из архива .tar.gz

Архив со скриптами: [TestSearchApi / tests.tar.gz](https://github.com/Drakon-top/TestSearchApi/blob/main/tests.tar.gz).

Установка `python3-venv` (Debian/Ubuntu), загрузка архива, распаковка и запуск с сохранением лога в `output.txt`:

```bash
sudo apt install -y python3-venv
curl -L -o archive.tar.gz https://github.com/drakon-top/TestSearchApi/raw/main/tests.tar.gz && tar -xf archive.tar.gz && cd archive_test && THREADS_COUNT=5 REQUESTS_COUNT=250 SEARCH_API_KEY=AQVNXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX SEARCH_API_FOLDERID=b... bash run_all.sh > output.txt && cat output.txt
```

Подставьте свой ключ `SEARCH_API_KEY` и каталог `SEARCH_API_FOLDERID`.

### Требования

- **Bash** (Linux/macOS или аналог)
- **Python 3.8+** (предпочтительно 3.10; скрипт пробует 3.10 → 3.9 → 3.8 → python3)
- Доступ в интернет для установки зависимостей и вызовов API

### Установка и запуск (локально)

1. Распакуйте проект (если у вас архив):
   ```bash
   tar -xzf archive_test.tar.gz
   cd archive_test
   ```

2. Задайте переменные (обязательны для API):
   - `THREADS_COUNT` — число потоков (например, `4`)
   - `REQUESTS_COUNT` — число запросов на тест (например, `100`)
   - `SEARCH_API_KEY` — API-ключ Yandex Cloud (обязательно)
   - `SEARCH_API_FOLDERID` — идентификатор каталога (обязательно)
   - `SSL_VERIFY` — проверка SSL: `true` (по умолчанию) или `false`

3. Запустите все тесты:
   ```bash
   THREADS_COUNT=4 REQUESTS_COUNT=100 ./run_all.sh
   ```

Скрипт сам создаёт виртуальное окружение `venv`, ставит зависимости из `requirements.txt` и запускает два прогона: REST (v2) и gRPC (grpc_default). Результаты сохраняются в `report_rest_top_50_lang_en.csv` и `report_grpc_default_top_50_lang_en.csv`.

### Запуск main.py вручную

Для точечных проверок можно вызывать `main.py` после активации окружения:

```bash
source venv/bin/activate
python main.py --threads-count 4 --requests-count 100 --action v2 --groups-on-page 50 --lang en --ssl-verify true --output report_rest.csv
python main.py --threads-count 4 --requests-count 100 --action grpc_default --groups-on-page 50 --lang en --ssl-verify true --output report_grpc.csv
```

Файл запросов по умолчанию: `queries.jsonl`. Его можно задать через `--queries-file`.

### Результаты

- `report_rest_top_50_lang_en.csv` — результаты REST (v2)
- `report_grpc_default_top_50_lang_en.csv` — результаты gRPC (grpc_default)

### Учёт метрик и прогрев

Перед основным замером в каждом потоке выполняется **один прогревный запрос** (установка соединения, SSL handshake). Эти запросы **не входят в статистику**: в отчёте и в CSV учитываются только следующие за ними запросы. То есть при `REQUESTS_COUNT=250` и `THREADS_COUNT=5` фактически выполняется 255 запросов, а в метриках (RPS, латентность, доля ошибок и т.д.) участвуют 250. Так исключается влияние «холодного» старта на результаты.

### Вывод лога (main.py)

В stdout `main.py` выводит текстовый отчёт по прогону в таком виде:

- **Test Information** — время начала и окончания теста, дата, временной диапазон.
- **Test Configuration** — action, число потоков, общее число запросов, groups on page, язык, SSL verify.
- **Load Metrics** — длительность теста (с и мс), RPS, среднее время между запросами (мс), среднее число одновременных запросов, пиковая нагрузка (потоки).
- **Test Statistics** — всего запросов, число плохих ответов (%), пустых результатов (200 OK без выдачи) (%).
- **Status Code Distribution** — распределение по кодам (HTTP 200, 4xx, 5xx, Exception/Error).
- **Sample Error Messages (first 3)** — до трёх примеров сообщений об ошибках (если есть плохие ответы).
- **Latency Statistics** — Min, Median, Average, P95, P99, Max латентности (мс).

При однострочном запуске с `> output.txt` весь этот вывод попадает в `output.txt`.

---

## English

### One-line run from .tar.gz archive

Archive with scripts: [TestSearchApi / tests.tar.gz](https://github.com/Drakon-top/TestSearchApi/blob/main/tests.tar.gz).

Install `python3-venv` (Debian/Ubuntu), download the archive, extract and run with log saved to `output.txt`:

```bash
sudo apt install -y python3-venv
curl -L -o archive.tar.gz https://github.com/drakon-top/TestSearchApi/raw/main/tests.tar.gz && tar -xf archive.tar.gz && cd archive_test && THREADS_COUNT=5 REQUESTS_COUNT=250 SEARCH_API_KEY=AQVNXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX SEARCH_API_FOLDERID=b... bash run_all.sh > output.txt && cat output.txt
```

Replace `SEARCH_API_KEY` and `SEARCH_API_FOLDERID` with your values.

### Requirements

- **Bash** (Linux/macOS or equivalent)
- **Python 3.8+** (3.10 preferred; script tries 3.10 → 3.9 → 3.8 → python3)
- Internet access for installing dependencies and calling the API

### Setup and run (local)

1. Unpack the project (if you have an archive):
   ```bash
   tar -xzf archive_test.tar.gz
   cd archive_test
   ```

2. Set environment variables (required for API):
   - `THREADS_COUNT` — number of threads (e.g. `4`)
   - `REQUESTS_COUNT` — number of requests per run (e.g. `100`)
   - `SEARCH_API_KEY` — Yandex Cloud API key (required)
   - `SEARCH_API_FOLDERID` — folder ID (required)
   - `SSL_VERIFY` — SSL verification: `true` (default) or `false`

3. Run all tests:
   ```bash
   THREADS_COUNT=4 REQUESTS_COUNT=100 ./run_all.sh
   ```

The script creates a virtual environment `venv`, installs dependencies from `requirements.txt`, and runs two test runs: REST (v2) and gRPC (grpc_default). Results are written to `report_rest_top_50_lang_en.csv` and `report_grpc_default_top_50_lang_en.csv`.

### Running main.py manually

For ad-hoc runs, activate the environment and call `main.py`:

```bash
source venv/bin/activate
python main.py --threads-count 4 --requests-count 100 --action v2 --groups-on-page 50 --lang en --ssl-verify true --output report_rest.csv
python main.py --threads-count 4 --requests-count 100 --action grpc_default --groups-on-page 50 --lang en --ssl-verify true --output report_grpc.csv
```

Default query file: `queries.jsonl`. Override with `--queries-file`.

### Output files

- `report_rest_top_50_lang_en.csv` — REST (v2) results
- `report_grpc_default_top_50_lang_en.csv` — gRPC (grpc_default) results

### Metrics and warm-up

Before the main measurement, **one warm-up request** is sent in each thread (connection setup, SSL handshake). These requests **are not included in the statistics**: only the requests that follow them are counted in the report and in the CSV. So with `REQUESTS_COUNT=250` and `THREADS_COUNT=5`, 255 requests are actually made, but metrics (RPS, latency, error rate, etc.) are computed over 250. This keeps “cold” startup effects out of the results.

### Log output (main.py)

`main.py` prints a text report to stdout with these sections:

- **Test Information** — test start/end time, date, time range.
- **Test Configuration** — action, thread count, total requests, groups on page, language, SSL verify.
- **Load Metrics** — test duration (s and ms), RPS, average time between requests (ms), average concurrent requests, peak load (threads).
- **Test Statistics** — total requests, bad responses (%), empty results (200 OK but no results) (%).
- **Status Code Distribution** — counts by status (HTTP 200, 4xx, 5xx, Exception/Error).
- **Sample Error Messages (first 3)** — up to three sample error messages when there are bad responses.
- **Latency Statistics** — Min, Median, Average, P95, P99, Max latency (ms).

When using the one-line run with `> output.txt`, all of this output is written to `output.txt`.
