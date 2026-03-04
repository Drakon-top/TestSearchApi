import aiohttp
import argparse
import asyncio
import base64
import csv
import datetime
import grpc
import itertools
import json
import logging
import math
import os
import pydantic
import tqdm

from google.protobuf.json_format import ParseDict
from statistics import quantiles
from typing import Literal, Optional
import warnings

# Подавляем предупреждение о несовместимости версий protobuf
warnings.filterwarnings('ignore', category=RuntimeWarning, message='.*protobuf.*version.*')

from yandex.cloud.searchapi.v2 import web_search_pb2
from yandex.cloud.searchapi.v2 import web_search_pb2_grpc


lock = asyncio.Lock()


class TraceResult(pydantic.BaseModel):
    start_time: datetime.datetime
    end_time: datetime.datetime
    latency: int
    reqid: Optional[str] = None
    context: Optional[dict] = None
    rejected: bool = False
    text: Optional[str] = None
    status_code: Optional[int] = None
    is_bad_response: bool = False
    is_empty_result: bool = False

    @classmethod
    def new(
        cls,
        start_time: datetime.datetime,
        end_time: datetime.datetime,
        reqid: str  = None,
        context: dict  = None,
        rejected: bool = False,
        text: str  = None,
        status_code: int = None,
        is_bad_response: bool = False,
        is_empty_result: bool = False,
    ):
        latency = delta_ms(start_time, end_time)
        return cls(
            start_time=start_time,
            end_time=end_time,
            latency=latency,
            reqid=reqid,
            context=context,
            rejected=rejected,
            text=text,
            status_code=status_code,
            is_bad_response=is_bad_response,
            is_empty_result=is_empty_result,
        )


class SetraceResult(TraceResult):
    pass


def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries-file", type=str, default="queries.jsonl")
    parser.add_argument("--threads-count", type=int)
    parser.add_argument("--requests-count", type=int)
    parser.add_argument("--groups-on-page", type=int)
    parser.add_argument("--lang", type=str)
    parser.add_argument("--action", type=str)
    parser.add_argument("--full-texts", action="store_true", help="Include full texts in results (only works with grpc_dedicated)")
    parser.add_argument("--no-reuse-ssl", action="store_true", help="Do not reuse SSL context (generally not needed)")
    parser.add_argument("--ssl-verify", type=lambda x: x.lower() in ['true', '1', 'yes', 'on'], default=True, help="Enable/disable SSL certificate verification (default: True). Use --ssl-verify false to disable")
    parser.add_argument("--output", type=str)
    return parser.parse_args()


def delta_ms(start: datetime.datetime, end: datetime.datetime):
    convert_dt = lambda x: (
        datetime.datetime.fromisoformat(x) if isinstance(x, str) else x
    )
    return int((convert_dt(end) - convert_dt(start)).total_seconds() * 1_000)


def is_empty_result(text: str) -> bool:
    """Check if response has empty search results with detailed XML validation."""
    if not text or not text.strip():
        return True
    
    # Check for XML format - detailed validation
    if "<results" in text:
        # Find results tag (can be <results> or <results found="...">)
        results_start = text.find("<results")
        if results_start == -1:
            return True
        
        # Check if it's a self-closing tag <results/> or <results found="0"/>
        if text[results_start:results_start+50].strip().endswith("/>"):
            # Check found attribute if present
            found_attr = text[results_start:text.find(">", results_start)]
            if 'found="0"' in found_attr or 'found=\'0\'' in found_attr:
                return True
            # Self-closing without found attribute - consider empty
            return True
        
        # Find closing tag
        results_end = text.find("</results>", results_start)
        if results_end == -1:
            # No closing tag - might be self-closing, check found attribute
            tag_end = text.find(">", results_start)
            if tag_end != -1:
                tag_content = text[results_start:tag_end]
                if 'found="0"' in tag_content or 'found=\'0\'' in tag_content:
                    return True
            return True
        
        # Extract content between <results> and </results>
        results_content = text[results_start + text.find(">", results_start) + 1:results_end].strip()
        
        # Check found attribute in opening tag
        opening_tag = text[results_start:text.find(">", results_start)]
        if 'found="0"' in opening_tag or 'found=\'0\'' in opening_tag:
            return True
        
        # Check if content is empty or only whitespace
        if not results_content or not results_content.strip():
            return True
        
        # Check for group tags with actual content
        if "<group>" in results_content:
            # Find all group tags
            import re
            group_pattern = r'<group[^>]*>(.*?)</group>'
            groups = re.findall(group_pattern, results_content, re.DOTALL)
            if not groups:
                # Check for self-closing groups (empty)
                if not re.search(r'<group[^>]*>.*?</group>', results_content, re.DOTALL):
                    return True
            else:
                # Check if any group has actual content (not just whitespace or empty tags)
                has_content = False
                for group_content in groups:
                    # Remove CDATA markers and check for actual content
                    group_clean = re.sub(r'<!\[CDATA\[.*?\]\]>', '', group_content, flags=re.DOTALL)
                    group_clean = re.sub(r'<[^>]+>', '', group_clean)  # Remove all tags
                    if group_clean.strip():
                        has_content = True
                        break
                if not has_content:
                    return True
        
        # Check for doc tags with actual content
        if "<doc>" in results_content:
            # Find all doc tags
            import re
            doc_pattern = r'<doc[^>]*>(.*?)</doc>'
            docs = re.findall(doc_pattern, results_content, re.DOTALL)
            if not docs:
                # Check for self-closing docs (empty)
                if not re.search(r'<doc[^>]*>.*?</doc>', results_content, re.DOTALL):
                    return True
            else:
                # Check if any doc has actual content
                has_content = False
                for doc_content in docs:
                    # Remove CDATA markers and check for actual content
                    doc_clean = re.sub(r'<!\[CDATA\[.*?\]\]>', '', doc_content, flags=re.DOTALL)
                    doc_clean = re.sub(r'<[^>]+>', '', doc_clean)  # Remove all tags
                    if doc_clean.strip():
                        has_content = True
                        break
                if not has_content:
                    return True
        
        # If no group or doc tags at all, consider empty
        if "<group>" not in results_content and "<doc>" not in results_content:
            return True
    
    # Check for JSON format (rawData)
    elif "rawData" in text:
        # JSON format - check if rawData contains actual results
        try:
            import json
            json_data = json.loads(text)
            if "rawData" in json_data:
                # Decode base64 if needed
                raw_data = json_data["rawData"]
                if isinstance(raw_data, str):
                    import base64
                    try:
                        decoded = base64.b64decode(raw_data).decode()
                        # Recursively check decoded content
                        return is_empty_result(decoded)
                    except:
                        pass
                # If rawData is empty or doesn't contain results
                if not raw_data or (isinstance(raw_data, str) and not raw_data.strip()):
                    return True
        except:
            # If JSON parsing fails, consider it might have results
            pass
    else:
        # No results tag and no rawData - consider empty
        return True
    
    return False


def get_base_url(action: str):
    return {
        "v1": "https://yandex.com",
        "v2": "https://searchapi.api.cloud.yandex.net",
        "v2_images": "https://searchapi.api.cloud.yandex.net",
        "ping": "https://yandex.com",
        "grpc_default": "https://searchapi.api.cloud.yandex.net",
        "grpc_dedicated": "https://api.search.yandexcloud.net",
    }.get(action)


def get_trace_config():
    async def on_event(session, trace_config_ctx, params):
        kind = params.__class__.__name__.split(".")[-1].lstrip("Trace").rstrip("Params")
        trace_config_ctx.trace_request_ctx[kind] = datetime.datetime.now()

    config = aiohttp.TraceConfig()
    events = [
        "on_request_start",
        "on_connection_queued_start",
        "on_connection_create_start",
        "on_connection_reuseconn",
        "on_connection_queued_end",
        "on_connection_create_end",
        "on_request_headers_sent",
        "on_request_end",
        "on_request_chunk_sent",
        "on_response_chunk_received",
    ]
    for event in events:
        getattr(config, event).append(on_event)
    return config


async def do_nothing():
    return


async def trace_single(
    session: aiohttp.ClientSession,
    pbar: tqdm.tqdm,
    action: Literal["v1", "v2", "v2_images", "ping", "grpc_default", "grpc_dedicated"],
    full_texts: bool,
    no_reuse_ssl: bool,
    ssl_verify: bool,
    queries,
    groups_on_page: int,
    lang: str ,
    requests_count: int,
    results: list,
    results_count: list,
):
    async def inner(session: aiohttp.ClientSession):
        status_code = None
        is_bad = False
        text = ""
        reqid = None
        end_time = None
        
        # RequestStart for HTTP requests is set by trace_config via on_request_start event
        # For gRPC it's set explicitly later
        
        try:
            if action == "v1":
                response = await session.get(
                    "/search/xml",
                    headers={"Authorization": "Api-Key " + os.environ["SEARCH_API_KEY"]},
                    params={
                        "text": query,
                        "folderid": os.environ["SEARCH_API_FOLDERID"],
                        "groupby": f"attr=d.mode=deep.groups-on-page={groups_on_page}",
                    } | ({"xml_full_texts": "1"} if full_texts else {}),
                    trace_request_ctx=context,
                )
                status_code = response.status
                is_bad = status_code != 200
                text = await response.text()
                end_time = datetime.datetime.now()
                reqid = response.headers.get("X-Yandex-Req-Id")
            elif action == "v2":
                try:
                    response = await session.post(
                        "/v2/web/search",
                        headers={"Authorization": "Bearer " + os.environ["SEARCH_API_KEY"]},
                        json={
                            "query": {"queryText": query, "searchType": "SEARCH_TYPE_COM"},
                            "folderid": os.environ["SEARCH_API_FOLDERID"],
                            "responseFormat": "FORMAT_XML",
                            "groupSpec": {
                                "groupMode": "GROUP_MODE_DEEP",
                                "groupsOnPage": str(groups_on_page),
                            }
                        },
                        trace_request_ctx=context,
                    )
                    status_code = response.status
                    end_time = datetime.datetime.now()
                    text = ""
                    reqid = None
                    is_bad = False
                except aiohttp.ClientResponseError as e:
                    # Ошибка ответа от сервера (4xx, 5xx)
                    status_code = e.status
                    is_bad = status_code != 200
                    text = f"HTTP {status_code}: {e.message}"
                    end_time = datetime.datetime.now()
                    reqid = None
                    # Пропускаем дальнейшую обработку для этого случая
                    return text, end_time, reqid, status_code, is_bad
                except aiohttp.ClientError as e:
                    # Ошибка клиента (сетевые ошибки, таймауты)
                    status_code = 0
                    is_bad = True
                    text = f"Client error: {type(e).__name__}: {str(e)}"
                    end_time = datetime.datetime.now()
                    reqid = None
                    return text, end_time, reqid, status_code, is_bad
                
                if status_code == 200:
                    try:
                        json_response = await response.json()
                        if "rawData" in json_response:
                            text = base64.b64decode(json_response["rawData"]).decode()
                            try:
                                reqid = text.split("<reqid>")[1].split("</reqid>")[0]
                            except:
                                reqid = None
                        else:
                            # Ответ 200, но нет rawData - проверяем, есть ли ошибка
                            text = str(json_response)
                            # Проверяем, есть ли ошибка в ответе
                            if isinstance(json_response, dict):
                                if "error" in json_response or ("message" in json_response and "error" in str(json_response.get("message", "")).lower()):
                                    is_bad = True
                                # Если нет rawData и нет явной ошибки, это тоже может быть проблемой
                                elif "rawData" not in json_response:
                                    # Но не помечаем как bad, если это просто другой формат ответа
                                    pass
                    except Exception as e:
                        # Если не удалось прочитать JSON при статусе 200, 
                        # это может быть ошибка парсинга, но не обязательно плохой ответ
                        # Пытаемся прочитать как текст (если еще не прочитано)
                        try:
                            # Если JSON уже был прочитан, это не сработает, но попробуем
                            text = await response.text()
                        except:
                            text = f"Error parsing JSON: {str(e)}"
                        # Не помечаем как bad, если статус был 200 - возможно, это просто неожиданный формат
                        # is_bad остается False
                else:
                    # Для не-200 ответов читаем текст ошибки
                    is_bad = True
                    try:
                        text = await response.text()
                    except:
                        text = f"HTTP {status_code} error"
            elif action == "ping":
                response = await session.get(
                    "/ping",
                    trace_request_ctx=context,
                )
                status_code = response.status
                is_bad = status_code != 200
                text = await response.text()
                end_time = datetime.datetime.now()
                reqid = None
            elif action in ("grpc_default", "grpc_dedicated"):
                stub = web_search_pb2_grpc.WebSearchServiceStub(channel)
                request_dict = {
                    "query": {
                        "search_type": "SEARCH_TYPE_COM",
                        "query_text": query,
                    },
                    "folder_id": os.environ["SEARCH_API_FOLDERID"],
                    "response_format": "FORMAT_XML",
                    "group_spec": {
                        "groupMode": "GROUP_MODE_DEEP",
                        "groupsOnPage": str(groups_on_page),
                    }
                }
                
                request = web_search_pb2.WebSearchRequest() # type: ignore
                ParseDict(request_dict, request)
                context["RequestStart"] = datetime.datetime.now()
                response = await stub.Search(request, metadata=(("x-xml-full-text", "1"),) if full_texts else None)
                end_time = datetime.datetime.now()
                text = response.raw_data.decode()
                status_code = 200  # gRPC success
                is_bad = False
                try:
                    reqid = text.split("<reqid>")[1].split("</reqid>")[0]
                except:
                    reqid = None
        except aiohttp.ClientResponseError as e:
            # Ошибка ответа от сервера (4xx, 5xx) - если не обработана ранее
            if status_code is None:
                status_code = e.status
                is_bad = status_code != 200
            text = f"HTTP {status_code}: {e.message}"
            if end_time is None:
                end_time = datetime.datetime.now()
        except aiohttp.ClientError as e:
            # Ошибки клиента (сетевые ошибки, таймауты и т.д.)
            if status_code is None:
                is_bad = True
                status_code = 0  # Error code for exceptions
            error_type = type(e).__name__
            text = f"Client error ({error_type}): {str(e)}"
            if end_time is None:
                end_time = datetime.datetime.now()
        except grpc.RpcError as e:
            # Ошибки gRPC
            if status_code is None:
                is_bad = True
                status_code = e.code().value[0] if hasattr(e.code(), 'value') else 0
            error_type = type(e).__name__
            text = f"gRPC error ({error_type}): {str(e)}"
            if end_time is None:
                end_time = datetime.datetime.now()
        except Exception as e:
            # Другие исключения - проверяем, был ли уже установлен status_code
            if status_code is None:
                # Исключение произошло до получения response
                is_bad = True
                status_code = 0
            error_type = type(e).__name__
            text = f"Error ({error_type}): {str(e)}"
            if end_time is None:
                end_time = datetime.datetime.now()
        
        # Убеждаемся, что end_time установлен
        if end_time is None:
            end_time = datetime.datetime.now()
        
        return text, end_time, reqid, status_code, is_bad

    global lock
    while True:
        async with lock:
            if results_count[0] >= requests_count:
                break
            results_count[0] += 1
            pbar.update(results_count[0] - pbar.n)
        context = {}
        query = " ".join(next(queries)) + (" lang:" + lang if lang else "")
        # Если нужно не переиспользовать SSL, создаем новую сессию
        # (основная сессия уже создана с правильным connector)
        if no_reuse_ssl:
            base_url = get_base_url(action)
            # Создаем connector с отключенной проверкой SSL, если нужно
            temp_connector = None
            if not ssl_verify:
                import ssl
                temp_connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(
                base_url, trace_configs=[get_trace_config()], connector=temp_connector
            ) as temp_session:
                text, end_time, reqid, status_code, is_bad = await inner(temp_session)
        else:
            # Используем основную сессию (она уже создана с connector, если ssl_verify=False)
            text, end_time, reqid, status_code, is_bad = await inner(session)
        
        # Ensure RequestStart is set (for HTTP it's set by trace_config via on_request_start, for gRPC it's set in inner)
        if "RequestStart" not in context:
            # Fallback: try to find earliest timing event or use a reasonable default
            if context:
                # Use the earliest event time if available
                timings = [v for v in context.values() if isinstance(v, datetime.datetime)]
                if timings:
                    context["RequestStart"] = min(timings)
                else:
                    # Last resort: use end_time (will result in 0 latency, but won't crash)
                    context["RequestStart"] = end_time
            else:
                context["RequestStart"] = end_time
        
        # Check for empty results (status 200 but no results in response)
        is_empty = False
        if status_code == 200 and not is_bad:
            is_empty = is_empty_result(text)
        
        results.append(
            TraceResult.new(
                start_time=context["RequestStart"],
                end_time=end_time,
                reqid=reqid,
                context=context,
                rejected=("<results>" not in text) and ("rawData" not in text),
                text=text,
                status_code=status_code,
                is_bad_response=is_bad,
                is_empty_result=is_empty,
            )
        )


def create_grpc_channel(action: str):
    """Create an authenticated gRPC channel."""
    credentials = grpc.access_token_call_credentials(os.environ["SEARCH_API_KEY"])
    channel_credentials = grpc.composite_channel_credentials(
        grpc.ssl_channel_credentials(),
        credentials
    )
    MAX_MESSAGE_LENGTH = 10_000_000
    return grpc.aio.secure_channel(
        get_base_url(action).replace("https://", "") + ":443",
        channel_credentials,
        options=[
            ('grpc.max_message_length', MAX_MESSAGE_LENGTH),
            ('grpc.max_send_message_length', MAX_MESSAGE_LENGTH),
            ('grpc.max_receive_message_length', MAX_MESSAGE_LENGTH),
        ],
    )


async def main(
    threads_count: int,
    requests_count: int,
    queries_file: str,
    groups_on_page: int,
    lang: str ,
    action: str,
    full_texts: bool,
    no_reuse_ssl: bool,
    ssl_verify: bool,
    output: str,
):
    base_url = get_base_url(action)
    if os.path.exists(queries_file):
        unique_queries = [
            [json.loads(line)["query"]] for line in open(queries_file)
        ]
    else:
        logging.info("No queries file found, generating queries...")
        unique_queries = list(
            itertools.product(
                ["tiny", "small", "medium", "big", "huge"],
                ["red", "green", "blue", "violet", "black"],
                ["rose", "onion", "fly", "elephant", "owl", "tree"],
            )
        )
    queries = itertools.chain.from_iterable(
        itertools.repeat(
            unique_queries, math.ceil(requests_count / len(unique_queries))
        )
    )
    pbar = tqdm.tqdm(total=requests_count + threads_count)
    if action in ("grpc_default", "grpc_dedicated"):
        global channel
        channel = create_grpc_channel(action)
    
    # Создаем connector с отключенной проверкой SSL, если нужно
    connector = None
    if not ssl_verify:
        import ssl
        connector = aiohttp.TCPConnector(ssl=False)
        logging.info("SSL verification disabled for this session")
    
    async with aiohttp.ClientSession(
        base_url, trace_configs=[get_trace_config()], connector=connector
    ) as session:
        traces, traces_count = [], [0]
        await asyncio.gather(
            *[
                trace_single(
                    session,
                    pbar,
                    action,
                    full_texts,
                    no_reuse_ssl,
                    ssl_verify,
                    queries,
                    groups_on_page,
                    lang,
                    requests_count + threads_count,
                    traces,
                    traces_count,
                )
                for _ in range(threads_count)
            ]
        )
        traces = traces[threads_count:]
    
    # Calculate statistics
    bad_responses_count = sum(1 for trace in traces if trace.is_bad_response)
    empty_results_count = sum(1 for trace in traces if trace.is_empty_result)
    total_requests = len(traces)
    
    # Статистика по статус кодам
    status_codes = {}
    for trace in traces:
        code = trace.status_code or 0
        status_codes[code] = status_codes.get(code, 0) + 1
    
    data = [[
        "Reqid",
        "Status Code",
        "Bad Response",
        "Empty Result",
        "Rejected",
        "Error Message",
        "Local Start",
        "Local End",
        "Local Latency",
        "Local Trace",
        "Min Latency",
        "Median Latency",
        "P95",
        "P99",
        "Max Latency",
    ]]
    for row_id, trace_result in enumerate(traces):
        timings = sorted(
            (trace_result.context or {}).items(),
            key=lambda x: x[1],
        )
        full_trace, start_time = "", None
        for key, value in timings:
            if not start_time:
                start_time = value
            full_trace += f"{key}: {delta_ms(start_time, value)}\n"
        # Извлекаем сообщение об ошибке из text (первые 200 символов)
        error_msg = ""
        if trace_result.text:
            # Показываем текст ошибки для всех bad responses или если статус код 0
            if trace_result.is_bad_response or (trace_result.status_code == 0):
                error_text = str(trace_result.text)
                # Берем первые 200 символов и заменяем проблемные символы
                error_msg = error_text[:200].replace("\n", " ").replace(";", ",").replace("\r", "")
        
        data.append(
            [
                trace_result.reqid or "",
                trace_result.status_code or "",
                ["false", "true"][trace_result.is_bad_response],
                ["false", "true"][trace_result.is_empty_result],
                ["false", "true"][trace_result.rejected],
                error_msg,
                trace_result.start_time.isoformat(),
                trace_result.end_time.isoformat(),
                trace_result.latency,
                full_trace,
            ]
        )
        data[-1].extend([
            "=MIN(I:I)",
            "=MEDIAN(I:I)",
            "=PERCENTILE.INC(I:I;0,95)",
            "=PERCENTILE.INC(I:I;0,99)",
            "=MAX(I:I)",
        ] if row_id == 0 else [""] * 5)
        
    with open(output, "w") as csvfile:
        writer = csv.writer(csvfile, delimiter=";")
        writer.writerows(data)
    
    # Calculate test duration
    if traces:
        test_start_time = min(trace.start_time for trace in traces)
        test_end_time = max(trace.end_time for trace in traces)
        test_duration_seconds = (test_end_time - test_start_time).total_seconds()
        test_duration_ms = test_duration_seconds * 1000
    else:
        test_duration_seconds = 0
        test_duration_ms = 0
    
    # Calculate load metrics
    rps = total_requests / test_duration_seconds if test_duration_seconds > 0 else 0
    avg_time_between_requests = test_duration_seconds / total_requests if total_requests > 0 else 0
    concurrent_requests_avg = threads_count  # Average concurrent requests
    
    # Print statistics
    latencies = [trace.latency for trace in traces]
    
    # Get test start and end times for display
    if traces:
        test_start_display = min(trace.start_time for trace in traces)
        test_end_display = max(trace.end_time for trace in traces)
    else:
        test_start_display = datetime.datetime.now()
        test_end_display = datetime.datetime.now()
    
    print(f"\n=== Test Information ===")
    print(f"Test started: {test_start_display.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Test finished: {test_end_display.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Test date: {test_start_display.strftime('%Y-%m-%d')}")
    print(f"Test time: {test_start_display.strftime('%H:%M:%S')} - {test_end_display.strftime('%H:%M:%S')}")
    
    print(f"\n=== Test Configuration ===")
    print(f"Action: {action}")
    print(f"Threads: {threads_count}")
    print(f"Total requests: {requests_count}")
    print(f"Groups on page: {groups_on_page}")
    print(f"Language: {lang}")
    print(f"SSL verify: {ssl_verify}")
    
    print(f"\n=== Load Metrics ===")
    print(f"Test duration: {test_duration_seconds:.2f} seconds ({test_duration_ms:.0f} ms)")
    print(f"Requests per second (RPS): {rps:.2f}")
    print(f"Average time between requests: {avg_time_between_requests*1000:.2f} ms")
    print(f"Average concurrent requests: {concurrent_requests_avg}")
    print(f"Peak load (threads): {threads_count}")
    
    print(f"\n=== Test Statistics ===")
    print(f"Total requests: {total_requests}")
    print(f"Bad responses: {bad_responses_count} ({bad_responses_count/total_requests*100:.2f}%)")
    print(f"Empty results (200 OK but no results): {empty_results_count} ({empty_results_count/total_requests*100:.2f}%)")
    print(f"\n=== Status Code Distribution ===")
    for code, count in sorted(status_codes.items()):
        code_name = "Exception/Error" if code == 0 else f"HTTP {code}"
        print(f"  {code_name}: {count} ({count/total_requests*100:.2f}%)")
    
    # Показываем примеры ошибок для диагностики
    error_traces = [trace for trace in traces if trace.is_bad_response]
    if error_traces:
        print(f"\n=== Sample Error Messages (first 3) ===")
        for i, trace in enumerate(error_traces[:3]):
            error_text = str(trace.text)[:150] if trace.text else "No error message"
            print(f"  {i+1}. Status: {trace.status_code or 'N/A'}, Error: {error_text}")
    
    print(f"\n=== Latency Statistics ===")
    if latencies:
        latencies_sorted = sorted(latencies)
        min_latency = min(latencies)
        max_latency = max(latencies)
        median_latency = latencies_sorted[len(latencies_sorted) // 2]
        avg_latency = sum(latencies) / len(latencies)
        p95_latency = quantiles(latencies, n=100)[94]
        p99_latency = quantiles(latencies, n=100)[98]
        
        print(f"Min latency: {min_latency:.2f} ms")
        print(f"Median latency: {median_latency:.2f} ms")
        print(f"Average latency: {avg_latency:.2f} ms")
        print(f"P95 latency: {p95_latency:.2f} ms")
        print(f"P99 latency: {p99_latency:.2f} ms")
        print(f"Max latency: {max_latency:.2f} ms")


if __name__ == "__main__":
    args = get_args()
    if not args.ssl_verify:
        logging.info("SSL verification is disabled")
    asyncio.run(
        main(
            threads_count=args.threads_count,
            requests_count=args.requests_count,
            queries_file=args.queries_file,
            groups_on_page=args.groups_on_page,
            lang=args.lang,
            action=args.action,
            full_texts=args.full_texts,
            no_reuse_ssl=args.no_reuse_ssl,
            ssl_verify=args.ssl_verify,
            output=args.output,
        )
    )
