#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, quote, urlparse, urlsplit
from uuid import uuid4


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "relay_manager.db"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

SCHEDULER_KEY = "scheduler_v2"
LEGACY_MIGRATION_KEY = "legacy_credentials_migrated"
DEFAULT_SCHEDULER = {
    "enabled": False,
    "interval_minutes": 60,
    "last_cycle_started_at": "",
    "last_cycle_finished_at": "",
    "last_cycle_status": "idle",
    "last_cycle_note": "",
}

PAGE_ROUTES = {
    "/": "dashboard.html",
    "/stations": "stations.html",
    "/keys": "keys.html",
    "/models": "models.html",
    "/history": "history.html",
}

PROTOCOLS = [
    {
        "adapter_type": "openai_chat",
        "label": "OpenAI Chat",
        "probe_models": ["gpt-4o-mini", "gpt-4.1-mini", "gpt-3.5-turbo"],
    },
    {
        "adapter_type": "openai_responses",
        "label": "OpenAI Responses",
        "probe_models": ["gpt-4.1-mini", "gpt-4o-mini", "gpt-5-mini"],
    },
    {
        "adapter_type": "anthropic_messages",
        "label": "Claude / Anthropic Messages",
        "probe_models": ["claude-3-5-sonnet-latest", "claude-3-7-sonnet-latest"],
    },
    {
        "adapter_type": "gemini_generate_content",
        "label": "Gemini GenerateContent",
        "probe_models": ["gemini-2.0-flash", "gemini-1.5-flash"],
    },
]

PROTOCOL_LABELS = {item["adapter_type"]: item["label"] for item in PROTOCOLS}
DETECTION_MAX_WORKERS = 4
CHECK_MAX_WORKERS = 4
LIST_RETRY_ATTEMPTS = 2
PROBE_RETRY_ATTEMPTS = 2
CHECK_RETRY_ATTEMPTS = 2
RETRY_BACKOFF_SECONDS = 0.8
NETWORK_MODES = {"auto", "direct", "proxy"}
TRANSIENT_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}
PROBE_NEGATIVE_KEYWORDS = ["image", "video", "audio", "veo", "mid", "seedance", "gptimage", "flux", "journey"]
PROBE_FAMILY_KEYWORDS = {
    "openai_chat": ["gpt", "o1", "o3", "o4", "chatgpt"],
    "openai_responses": ["gpt", "o1", "o3", "o4", "chatgpt"],
    "anthropic_messages": ["claude"],
    "gemini_generate_content": ["gemini"],
}
PROBE_AVOID_KEYWORDS = {
    "openai_chat": ["claude", "gemini"],
    "openai_responses": ["claude", "gemini"],
    "anthropic_messages": ["gpt", "o1", "o3", "o4", "gemini"],
    "gemini_generate_content": ["claude", "gpt", "o1", "o3", "o4"],
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso8601(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def json_dumps(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def parse_seed_models(raw: str | None) -> list[str]:
    if not raw:
        return []
    models: list[str] = []
    for part in raw.replace(",", "\n").splitlines():
        model = part.strip()
        if model and model not in models:
            models.append(model)
    return models


def normalize_multiline_models(raw: str | None) -> str:
    return "\n".join(parse_seed_models(raw))


def merge_model_lists(*groups: Iterable[str]) -> list[str]:
    merged: list[str] = []
    for group in groups:
        for model in group:
            model_id = str(model or "").strip()
            if model_id and model_id not in merged:
                merged.append(model_id)
    return merged


def mask_secret(secret: str) -> str:
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}...{secret[-4:]}"


def as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def normalize_network_mode(value: Any, *, allow_inherit: bool = False, default: str = "auto") -> str:
    raw = str(value or "").strip().lower()
    if allow_inherit and raw in {"", "inherit"}:
        return ""
    return raw if raw in NETWORK_MODES else default


def parse_float(value: Any) -> float | None:
    try:
        raw = str(value).strip()
        return float(raw) if raw else None
    except (TypeError, ValueError):
        return None


def protocol_label(adapter_type: str) -> str:
    return PROTOCOL_LABELS.get(adapter_type, adapter_type)


def mask_proxy_url(proxy_url: str | None) -> str:
    raw = str(proxy_url or "").strip()
    if not raw:
        return ""
    parsed = urlsplit(raw)
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    if parsed.scheme:
        return f"{parsed.scheme}://{host}{port}"
    return raw


def openai_api_root(base_url: str) -> str:
    root = base_url.rstrip("/")
    return root if root.endswith("/v1") else f"{root}/v1"


def anthropic_api_root(base_url: str) -> str:
    root = base_url.rstrip("/")
    return root if root.endswith("/v1") else f"{root}/v1"


def gemini_api_root(base_url: str) -> str:
    root = base_url.rstrip("/")
    return root if root.endswith("/v1beta") else f"{root}/v1beta"


def generate_claude_code_probe_user_id() -> str:
    return f"user_{secrets.token_hex(32)}_account__session_{uuid4()}"


def claude_code_probe_headers(api_key: str) -> dict[str, str]:
    return {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "claude-code-20250219,interleaved-thinking-2025-05-14,effort-2025-11-24",
        "anthropic-dangerous-direct-browser-access": "true",
        "x-app": "cli",
        "User-Agent": "claude-cli/2.1.74 (external, sdk-cli)",
        "X-Stainless-Arch": "arm64",
        "X-Stainless-Lang": "js",
        "X-Stainless-OS": "MacOS",
        "X-Stainless-Package-Version": "0.74.0",
        "X-Stainless-Retry-Count": "0",
        "X-Stainless-Runtime": "node",
        "X-Stainless-Runtime-Version": "v24.3.0",
        "X-Stainless-Timeout": "600",
    }


def claude_code_probe_system() -> list[dict[str, Any]]:
    return [
        {
            "type": "text",
            "text": "x-anthropic-billing-header: cc_version=2.1.74.ee0; cc_entrypoint=sdk-cli; cch=relaym;",
        },
        {
            "type": "text",
            "text": "You are a Claude agent, built on Anthropic's Claude Agent SDK.",
            "cache_control": {"type": "ephemeral"},
        },
    ]


@dataclass
class CheckResult:
    status: str
    available: bool
    latency_ms: int
    response_shape: str
    preview: str
    error: str | None = None
    network_mode: str = ""
    network_route: str = ""
    proxy_url_masked: str = ""


class RelayError(Exception):
    pass


def curl_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout_seconds: int = 30,
    proxy_url: str | None = None,
) -> tuple[int, Any]:
    cmd = [
        "curl",
        "-sS",
        "-L",
        "--max-time",
        str(timeout_seconds),
        "-X",
        method.upper(),
        url,
        "-H",
        "Accept: application/json",
        "-H",
        "User-Agent: llm-relay-manager/1.0",
        "-w",
        "\n__STATUS__:%{http_code}",
    ]
    if proxy_url:
        cmd.extend(["--proxy", proxy_url])
    else:
        cmd.extend(["--noproxy", "*"])
    for key, value in (headers or {}).items():
        cmd.extend(["-H", f"{key}: {value}"])
    if payload is not None:
        cmd.extend(["-H", "Content-Type: application/json", "-d", json.dumps(payload, ensure_ascii=False)])

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds + 5)
    if proc.returncode != 0:
        raise RelayError(proc.stderr.strip() or "curl failed")
    marker = "\n__STATUS__:"
    if marker not in proc.stdout:
        raise RelayError("missing HTTP status marker")
    body, status_raw = proc.stdout.rsplit(marker, 1)
    status_code = int(status_raw.strip())
    body = body.strip()
    parsed: Any = None
    if body:
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = body
    return status_code, parsed


def curl_raw(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout_seconds: int = 30,
    proxy_url: str | None = None,
) -> tuple[int, str]:
    cmd = [
        "curl",
        "-sS",
        "-N",
        "-L",
        "--max-time",
        str(timeout_seconds),
        "-X",
        method.upper(),
        url,
        "-H",
        "User-Agent: llm-relay-manager/1.0",
        "-w",
        "\n__STATUS__:%{http_code}",
    ]
    if proxy_url:
        cmd.extend(["--proxy", proxy_url])
    else:
        cmd.extend(["--noproxy", "*"])
    for key, value in (headers or {}).items():
        cmd.extend(["-H", f"{key}: {value}"])
    if payload is not None:
        cmd.extend(["-H", "Content-Type: application/json", "-d", json.dumps(payload, ensure_ascii=False)])

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds + 5)
    if proc.returncode != 0:
        raise RelayError(proc.stderr.strip() or "curl failed")
    marker = "\n__STATUS__:"
    if marker not in proc.stdout:
        raise RelayError("missing HTTP status marker")
    body, status_raw = proc.stdout.rsplit(marker, 1)
    return int(status_raw.strip()), body.strip()


def resolve_network_settings(key_record: dict[str, Any] | sqlite3.Row) -> dict[str, str]:
    key_mode = normalize_network_mode((key_record["network_mode"] if "network_mode" in key_record.keys() else ""), allow_inherit=True)
    station_mode = normalize_network_mode((key_record["station_network_mode"] if "station_network_mode" in key_record.keys() else "auto"))
    effective_mode = key_mode or station_mode

    key_proxy_url = str(key_record["proxy_url"] if "proxy_url" in key_record.keys() else "").strip()
    station_proxy_url = str(key_record["station_proxy_url"] if "station_proxy_url" in key_record.keys() else "").strip()
    effective_proxy_url = key_proxy_url or station_proxy_url

    return {
        "key_mode": key_mode or "inherit",
        "station_mode": station_mode,
        "effective_mode": effective_mode,
        "proxy_url": effective_proxy_url,
        "proxy_url_masked": mask_proxy_url(effective_proxy_url),
    }


def request_json_with_network(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout_seconds: int = 30,
    network_mode: str = "auto",
    proxy_url: str = "",
) -> tuple[int, Any, str]:
    mode = normalize_network_mode(network_mode)
    if mode == "proxy" and not proxy_url:
        raise RelayError("proxy mode requires proxy_url")

    routes = ["direct"]
    if mode == "proxy":
        routes = ["proxy"]
    elif mode == "auto" and proxy_url:
        routes = ["direct", "proxy"]

    last_error: Exception | None = None
    for index, route in enumerate(routes):
        try:
            status_code, parsed = curl_json(
                method,
                url,
                headers=headers,
                payload=payload,
                timeout_seconds=timeout_seconds,
                proxy_url=proxy_url if route == "proxy" else None,
            )
            should_fallback = (
                mode == "auto"
                and route == "direct"
                and proxy_url
                and index + 1 < len(routes)
                and status_code in TRANSIENT_HTTP_STATUS_CODES
            )
            if should_fallback:
                continue
            return status_code, parsed, route
        except Exception as exc:
            last_error = exc
            should_fallback = (
                mode == "auto"
                and route == "direct"
                and proxy_url
                and index + 1 < len(routes)
                and is_transient_error_text(str(exc))
            )
            if not should_fallback:
                raise
    if last_error:
        raise last_error
    raise RelayError("network request failed without result")


def request_text_with_network(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout_seconds: int = 30,
    network_mode: str = "auto",
    proxy_url: str = "",
) -> tuple[int, str, str]:
    mode = normalize_network_mode(network_mode)
    if mode == "proxy" and not proxy_url:
        raise RelayError("proxy mode requires proxy_url")

    routes = ["direct"]
    if mode == "proxy":
        routes = ["proxy"]
    elif mode == "auto" and proxy_url:
        routes = ["direct", "proxy"]

    last_error: Exception | None = None
    for index, route in enumerate(routes):
        try:
            status_code, body = curl_raw(
                method,
                url,
                headers=headers,
                payload=payload,
                timeout_seconds=timeout_seconds,
                proxy_url=proxy_url if route == "proxy" else None,
            )
            should_fallback = (
                mode == "auto"
                and route == "direct"
                and proxy_url
                and index + 1 < len(routes)
                and status_code in TRANSIENT_HTTP_STATUS_CODES
            )
            if should_fallback:
                continue
            return status_code, body, route
        except Exception as exc:
            last_error = exc
            should_fallback = (
                mode == "auto"
                and route == "direct"
                and proxy_url
                and index + 1 < len(routes)
                and is_transient_error_text(str(exc))
            )
            if not should_fallback:
                raise
    if last_error:
        raise last_error
    raise RelayError("network request failed without result")


class BaseAdapter:
    adapter_type = "base"

    def __init__(self, key_record: dict[str, Any] | sqlite3.Row):
        self.key_record = key_record
        self.timeout_seconds = int(key_record["timeout_seconds"] or 30)
        self.network = resolve_network_settings(key_record)

    def list_models(self) -> list[str]:
        return []

    def test_model(self, model_id: str) -> CheckResult:
        raise NotImplementedError

    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, Any, str]:
        return request_json_with_network(
            method,
            url,
            headers=headers,
            payload=payload,
            timeout_seconds=self.timeout_seconds,
            network_mode=self.network["effective_mode"],
            proxy_url=self.network["proxy_url"],
        )

    def request_text(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, str, str]:
        return request_text_with_network(
            method,
            url,
            headers=headers,
            payload=payload,
            timeout_seconds=self.timeout_seconds,
            network_mode=self.network["effective_mode"],
            proxy_url=self.network["proxy_url"],
        )


class OpenAIChatAdapter(BaseAdapter):
    adapter_type = "openai_chat"

    def list_models(self) -> list[str]:
        url = f"{openai_api_root(self.key_record['base_url'])}/models"
        headers = {"Authorization": f"Bearer {self.key_record['api_key']}"}
        status_code, parsed, _route = self.request_json("GET", url, headers=headers)
        if status_code >= 400:
            raise RelayError(_extract_error(parsed) or f"HTTP {status_code}")
        if not isinstance(parsed, dict):
            raise RelayError("unexpected /models response")
        return [item.get("id") for item in parsed.get("data", []) if item.get("id")]

    def test_model(self, model_id: str) -> CheckResult:
        url = f"{openai_api_root(self.key_record['base_url'])}/chat/completions"
        headers = {"Authorization": f"Bearer {self.key_record['api_key']}"}
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": "Reply with exactly ok"}],
            "max_tokens": 16,
            "temperature": 0,
        }
        return _time_and_parse_openai(
            self,
            url,
            headers,
            payload,
            self.timeout_seconds,
            self.network["effective_mode"],
            self.network["proxy_url_masked"],
        )


class OpenAIResponsesAdapter(BaseAdapter):
    adapter_type = "openai_responses"

    def list_models(self) -> list[str]:
        return OpenAIChatAdapter(self.key_record).list_models()

    def test_model(self, model_id: str) -> CheckResult:
        url = f"{openai_api_root(self.key_record['base_url'])}/responses"
        headers = {"Authorization": f"Bearer {self.key_record['api_key']}"}
        payload = {
            "model": model_id,
            "input": "Reply with exactly ok",
            "max_output_tokens": 16,
        }
        return _time_and_parse_openai(
            self,
            url,
            headers,
            payload,
            self.timeout_seconds,
            self.network["effective_mode"],
            self.network["proxy_url_masked"],
        )


class AnthropicMessagesAdapter(BaseAdapter):
    adapter_type = "anthropic_messages"

    def list_models(self) -> list[str]:
        url = f"{anthropic_api_root(self.key_record['base_url'])}/models"
        header_candidates = [
            {
                "x-api-key": self.key_record["api_key"],
                "anthropic-version": "2023-06-01",
            },
            {
                "Authorization": f"Bearer {self.key_record['api_key']}",
            },
        ]
        last_error = "unexpected /models response"
        for headers in header_candidates:
            status_code, parsed, _route = self.request_json("GET", url, headers=headers)
            if status_code >= 400:
                last_error = _extract_error(parsed) or f"HTTP {status_code}"
                continue
            if not isinstance(parsed, dict):
                last_error = "unexpected /models response"
                continue
            return [item.get("id") for item in parsed.get("data", []) if item.get("id")]
        raise RelayError(last_error)

    def test_model(self, model_id: str) -> CheckResult:
        url = f"{anthropic_api_root(self.key_record['base_url'])}/messages?beta=true"
        headers = claude_code_probe_headers(self.key_record["api_key"])
        payload = {
            "model": model_id,
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "Reply with exactly ok"}],
            "system": claude_code_probe_system(),
            "metadata": {"user_id": generate_claude_code_probe_user_id()},
        }
        return _time_and_parse_anthropic(
            self,
            url,
            headers,
            payload,
            self.timeout_seconds,
            self.network["effective_mode"],
            self.network["proxy_url_masked"],
        )


class GeminiGenerateContentAdapter(BaseAdapter):
    adapter_type = "gemini_generate_content"

    def list_models(self) -> list[str]:
        url = f"{gemini_api_root(self.key_record['base_url'])}/models?key={quote(self.key_record['api_key'])}"
        status_code, parsed, _route = self.request_json("GET", url)
        if status_code >= 400:
            raise RelayError(_extract_error(parsed) or f"HTTP {status_code}")
        if not isinstance(parsed, dict):
            raise RelayError("unexpected /models response")
        models = []
        for item in parsed.get("models", []):
            name = item.get("name")
            if name and name.startswith("models/"):
                models.append(name.removeprefix("models/"))
        return models

    def test_model(self, model_id: str) -> CheckResult:
        safe_model = quote(model_id, safe="._-")
        url = (
            f"{gemini_api_root(self.key_record['base_url'])}/models/"
            f"{safe_model}:generateContent?key={quote(self.key_record['api_key'])}"
        )
        payload = {
            "contents": [{"parts": [{"text": "Reply with exactly ok"}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 16},
        }
        return _time_and_parse_gemini(
            self,
            url,
            payload,
            self.timeout_seconds,
            self.network["effective_mode"],
            self.network["proxy_url_masked"],
        )


ADAPTERS: dict[str, type[BaseAdapter]] = {
    OpenAIChatAdapter.adapter_type: OpenAIChatAdapter,
    OpenAIResponsesAdapter.adapter_type: OpenAIResponsesAdapter,
    AnthropicMessagesAdapter.adapter_type: AnthropicMessagesAdapter,
    GeminiGenerateContentAdapter.adapter_type: GeminiGenerateContentAdapter,
}


class StationThrottle:
    """Per-station rate limiter with concurrency, interval, and cooldown."""

    def __init__(self, max_concurrency: int, min_interval_ms: int, cooldown_seconds: int):
        self._semaphore = threading.Semaphore(max(1, max_concurrency))
        self._max_concurrency = max(1, max_concurrency)
        self._interval = max(0, min_interval_ms) / 1000.0
        self._cooldown_seconds = max(0, cooldown_seconds)
        self._last_request_time = 0.0
        self._lock = threading.Lock()
        self._cooldown_until = 0.0

    def acquire(self) -> None:
        self._semaphore.acquire()
        with self._lock:
            now = time.time()
            if now < self._cooldown_until:
                wait = self._cooldown_until - now
                time.sleep(wait)
            elapsed = time.time() - self._last_request_time
            if elapsed < self._interval:
                time.sleep(self._interval - elapsed)
            self._last_request_time = time.time()

    def release(self) -> None:
        self._semaphore.release()

    def enter_cooldown(self, seconds: float | None = None) -> None:
        with self._lock:
            self._cooldown_until = time.time() + (seconds if seconds is not None else self._cooldown_seconds)

    def in_cooldown(self) -> bool:
        return time.time() < self._cooldown_until


def _shape_for(parsed: Any) -> str:
    if isinstance(parsed, dict):
        return ",".join(list(parsed.keys())[:8])
    return type(parsed).__name__


def _extract_error(parsed: Any) -> str | None:
    if not isinstance(parsed, dict):
        return None
    if isinstance(parsed.get("error"), dict):
        error = parsed["error"]
        return error.get("message") or error.get("code") or error.get("type")
    if parsed.get("msg"):
        return str(parsed["msg"])
    if parsed.get("message"):
        return str(parsed["message"])
    return None


def _extract_openai_text(parsed: Any) -> str:
    if not isinstance(parsed, dict):
        return ""
    texts: list[str] = []
    for choice in parsed.get("choices", []) or []:
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            texts.append(content.strip())
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("text"):
                    texts.append(str(item["text"]).strip())
    for item in parsed.get("output", []) or []:
        if item.get("type") == "message":
            for content in item.get("content", []) or []:
                if content.get("type") in {"output_text", "text"} and content.get("text"):
                    texts.append(str(content["text"]).strip())
        elif item.get("type") in {"output_text", "text"} and item.get("text"):
            texts.append(str(item["text"]).strip())
    return " ".join(texts).strip()


def _extract_reasoning_text(parsed: Any) -> str:
    if not isinstance(parsed, dict):
        return ""
    texts: list[str] = []
    for choice in parsed.get("choices", []) or []:
        message = choice.get("message") or {}
        if message.get("reasoning_content"):
            texts.append(str(message["reasoning_content"]).strip())
    return " ".join(texts).strip()


def _iter_sse_payloads(raw: str) -> Iterable[dict[str, Any]]:
    block: list[str] = []
    for line in raw.splitlines():
        stripped = line.rstrip("\r")
        if stripped:
            block.append(stripped)
            continue
        if block:
            payload = _parse_sse_block(block)
            if payload is not None:
                yield payload
            block = []
    if block:
        payload = _parse_sse_block(block)
        if payload is not None:
            yield payload


def _parse_sse_block(lines: list[str]) -> dict[str, Any] | None:
    data_lines: list[str] = []
    for line in lines:
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if not data_lines:
        return None
    raw_data = "\n".join(data_lines).strip()
    if not raw_data or raw_data == "[DONE]":
        return None
    try:
        parsed = json.loads(raw_data)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _extract_openai_stream_text(raw: str) -> tuple[str, str | None, str]:
    stripped = raw.strip()
    if not stripped:
        return "", None, ""

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return _extract_openai_text(parsed), _extract_error(parsed), _shape_for(parsed)

    text_parts: list[str] = []
    last_error: str | None = None
    response_shape = "sse"
    for payload in _iter_sse_payloads(raw):
        payload_type = str(payload.get("type") or "").strip()
        if payload_type and response_shape == "sse":
            response_shape = f"sse:{payload_type}"

        error = _extract_error(payload)
        if error:
            last_error = error

        for choice in payload.get("choices", []) or []:
            delta = choice.get("delta") or {}
            content = delta.get("content")
            if isinstance(content, str) and content:
                text_parts.append(content)
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("text"):
                        text_parts.append(str(item["text"]))

        if payload_type == "response.output_text.delta" and payload.get("delta"):
            text_parts.append(str(payload["delta"]))

    return "".join(text_parts).strip(), last_error, response_shape


def normalize_probe_reply(text: str) -> str:
    normalized = " ".join(str(text or "").strip().split()).lower()
    return normalized.strip("`'\"“”‘’.,!?;:()[]{}<>，。！？；：（）【】《》")


def reply_matches_probe_expectation(text: str) -> bool:
    return normalize_probe_reply(text) == "ok"


def is_transient_error_text(error_text: str | None) -> bool:
    lowered = str(error_text or "").strip().lower()
    if not lowered:
        return False
    permanent_markers = [
        "invalid api key",
        "unauthorized",
        "permission denied",
        "not implemented",
        "model not found",
        "no available channel for model",
        "requires partner access",
        "required key [messages]",
        "required key [input]",
        "unsupported",
        "forbidden",
    ]
    if any(marker in lowered for marker in permanent_markers):
        return False
    transient_markers = [
        "timed out",
        "timeout",
        "temporarily unavailable",
        "try again",
        "bad gateway",
        "gateway timeout",
        "service unavailable",
        "connection reset",
        "ssl_connect",
        "ssl_error",
        "empty reply",
        "http 500",
        "http 502",
        "http 503",
        "http 504",
        "curl: (28)",
        "curl: (35)",
        "curl: (52)",
        "curl: (56)",
    ]
    return any(marker in lowered for marker in transient_markers)


RATE_LIMIT_MARKERS = [
    "rate limit",
    "too many requests",
    "http 429",
    "overloaded",
    "quota exceeded",
    "throttle",
]


def is_rate_limit_error(status_code: int | None, error_text: str | None) -> bool:
    if status_code == 429:
        return True
    lowered = str(error_text or "").strip().lower()
    return any(marker in lowered for marker in RATE_LIMIT_MARKERS)


def should_retry_result(result: CheckResult | None) -> bool:
    if not result or result.status != "error":
        return False
    return is_transient_error_text(result.error)


def run_with_retries(fn: Any, attempts: int) -> Any:
    last_error: Exception | None = None
    for index in range(max(1, attempts)):
        try:
            return fn()
        except Exception as exc:
            last_error = exc
            if index + 1 >= max(1, attempts) or not is_transient_error_text(str(exc)):
                raise
            time.sleep(RETRY_BACKOFF_SECONDS * (index + 1))
    if last_error:
        raise last_error
    raise RelayError("retry runner failed without result")


def test_model_with_retries(adapter: BaseAdapter, model_id: str, attempts: int) -> CheckResult:
    last_result: CheckResult | None = None
    for index in range(max(1, attempts)):
        try:
            result = adapter.test_model(model_id)
        except Exception as exc:
            result = CheckResult("error", False, 0, "", "", str(exc))
        last_result = result
        if result.status == "rate_limited":
            return result
        if not should_retry_result(result) or index + 1 >= max(1, attempts):
            return result
        time.sleep(RETRY_BACKOFF_SECONDS * (index + 1))
    return last_result or CheckResult("error", False, 0, "", "", "missing_result")


def _time_and_parse_openai(
    adapter: BaseAdapter,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: int,
    network_mode: str,
    proxy_url_masked: str,
) -> CheckResult:
    started = datetime.now(timezone.utc)
    status_code, parsed, route = adapter.request_json("POST", url, headers=headers, payload=payload)
    latency_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    response_shape = _shape_for(parsed)
    proxy_masked = proxy_url_masked if route == "proxy" else ""
    if status_code >= 400:
        error_text = _extract_error(parsed) or f"HTTP {status_code}"
        if is_rate_limit_error(status_code, error_text):
            return CheckResult(
                "rate_limited", False, latency_ms, response_shape, "", error_text,
                network_mode, route, proxy_masked,
            )
        return CheckResult(
            "error",
            False,
            latency_ms,
            response_shape,
            "",
            error_text,
            network_mode,
            route,
            proxy_masked,
        )
    text = _extract_openai_text(parsed)
    if text:
        if reply_matches_probe_expectation(text):
            return CheckResult("ok", True, latency_ms, response_shape, text[:160], None, network_mode, route, proxy_masked)
        return CheckResult("partial", False, latency_ms, response_shape, text[:160], "unexpected_output", network_mode, route, proxy_masked)
    reasoning = _extract_reasoning_text(parsed)
    if reasoning:
        return CheckResult("partial", False, latency_ms, response_shape, reasoning[:160], "reasoning_only", network_mode, route, proxy_masked)

    stream_result = _time_and_parse_openai_stream(
        adapter,
        url,
        headers,
        {**payload, "stream": True},
        network_mode,
        proxy_url_masked,
    )
    if stream_result.status != "empty" or stream_result.available:
        return stream_result
    return CheckResult("empty", False, latency_ms, response_shape, "", _extract_error(parsed), network_mode, route, proxy_masked)


def _time_and_parse_openai_stream(
    adapter: BaseAdapter,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    network_mode: str,
    proxy_url_masked: str,
) -> CheckResult:
    started = datetime.now(timezone.utc)
    status_code, raw, route = adapter.request_text(
        "POST",
        url,
        headers={**headers, "Accept": "text/event-stream"},
        payload=payload,
    )
    latency_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    text, error_text, response_shape = _extract_openai_stream_text(raw)
    proxy_masked = proxy_url_masked if route == "proxy" else ""
    if status_code >= 400:
        stream_error = error_text or f"HTTP {status_code}"
        if is_rate_limit_error(status_code, stream_error):
            return CheckResult(
                "rate_limited", False, latency_ms, response_shape or "sse", "", stream_error,
                network_mode, route, proxy_masked,
            )
        return CheckResult(
            "error",
            False,
            latency_ms,
            response_shape or "sse",
            "",
            stream_error,
            network_mode,
            route,
            proxy_masked,
        )
    if text:
        if reply_matches_probe_expectation(text):
            return CheckResult("ok", True, latency_ms, response_shape or "sse", text[:160], None, network_mode, route, proxy_masked)
        return CheckResult("partial", False, latency_ms, response_shape or "sse", text[:160], "unexpected_output", network_mode, route, proxy_masked)
    return CheckResult("empty", False, latency_ms, response_shape or "sse", "", error_text, network_mode, route, proxy_masked)


def _time_and_parse_anthropic(
    adapter: BaseAdapter,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: int,
    network_mode: str,
    proxy_url_masked: str,
) -> CheckResult:
    started = datetime.now(timezone.utc)
    status_code, parsed, route = adapter.request_json("POST", url, headers=headers, payload=payload)
    latency_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    response_shape = _shape_for(parsed)
    if status_code >= 400:
        error_text = _extract_error(parsed) or f"HTTP {status_code}"
        if is_rate_limit_error(status_code, error_text):
            return CheckResult(
                "rate_limited", False, latency_ms, response_shape, "", error_text,
                network_mode, route, proxy_url_masked if route == "proxy" else "",
            )
        return CheckResult(
            "error",
            False,
            latency_ms,
            response_shape,
            "",
            error_text,
            network_mode,
            route,
            proxy_url_masked if route == "proxy" else "",
        )
    texts = []
    for item in (parsed or {}).get("content", []):
        if item.get("type") == "text" and item.get("text"):
            texts.append(item["text"])
    text = " ".join(texts).strip()
    if text:
        if reply_matches_probe_expectation(text):
            return CheckResult("ok", True, latency_ms, response_shape, text[:160], None, network_mode, route, proxy_url_masked if route == "proxy" else "")
        return CheckResult("partial", False, latency_ms, response_shape, text[:160], "unexpected_output", network_mode, route, proxy_url_masked if route == "proxy" else "")
    return CheckResult("empty", False, latency_ms, response_shape, "", _extract_error(parsed), network_mode, route, proxy_url_masked if route == "proxy" else "")


def _time_and_parse_gemini(
    adapter: BaseAdapter,
    url: str,
    payload: dict[str, Any],
    timeout_seconds: int,
    network_mode: str,
    proxy_url_masked: str,
) -> CheckResult:
    started = datetime.now(timezone.utc)
    status_code, parsed, route = adapter.request_json("POST", url, payload=payload)
    latency_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    response_shape = _shape_for(parsed)
    if status_code >= 400:
        error_text = _extract_error(parsed) or f"HTTP {status_code}"
        if is_rate_limit_error(status_code, error_text):
            return CheckResult(
                "rate_limited", False, latency_ms, response_shape, "", error_text,
                network_mode, route, proxy_url_masked if route == "proxy" else "",
            )
        return CheckResult(
            "error",
            False,
            latency_ms,
            response_shape,
            "",
            error_text,
            network_mode,
            route,
            proxy_url_masked if route == "proxy" else "",
        )
    texts = []
    for candidate in (parsed or {}).get("candidates", []):
        content = candidate.get("content") or {}
        for part in content.get("parts", []):
            if part.get("text"):
                texts.append(part["text"])
    text = " ".join(texts).strip()
    if text:
        if reply_matches_probe_expectation(text):
            return CheckResult("ok", True, latency_ms, response_shape, text[:160], None, network_mode, route, proxy_url_masked if route == "proxy" else "")
        return CheckResult("partial", False, latency_ms, response_shape, text[:160], "unexpected_output", network_mode, route, proxy_url_masked if route == "proxy" else "")
    return CheckResult("empty", False, latency_ms, response_shape, "", _extract_error(parsed), network_mode, route, proxy_url_masked if route == "proxy" else "")


def choose_probe_model(adapter_type: str, models: list[str]) -> str:
    preferred_keywords = [
        "gpt",
        "glm",
        "claude",
        "gemini",
        "deepseek",
        "qwen",
        "kimi",
        "llama",
        "mistral",
        "longcat",
        "turbo",
        "openai",
        "tstars",
    ]
    family_keywords = PROBE_FAMILY_KEYWORDS.get(adapter_type, [])
    avoid_keywords = PROBE_AVOID_KEYWORDS.get(adapter_type, [])
    for model in models:
        lowered = model.lower()
        if any(token in lowered for token in PROBE_NEGATIVE_KEYWORDS):
            continue
        if family_keywords and any(token in lowered for token in family_keywords):
            return model
    for model in models:
        lowered = model.lower()
        if any(token in lowered for token in PROBE_NEGATIVE_KEYWORDS):
            continue
        if avoid_keywords and any(token in lowered for token in avoid_keywords):
            continue
        if any(token in lowered for token in preferred_keywords):
            return model
    for model in models:
        lowered = model.lower()
        if any(token in lowered for token in PROBE_NEGATIVE_KEYWORDS):
            continue
        if avoid_keywords and any(token in lowered for token in avoid_keywords):
            continue
        return model
    for model in models:
        lowered = model.lower()
        if not any(token in lowered for token in PROBE_NEGATIVE_KEYWORDS):
            return model
    for item in PROTOCOLS:
        if item["adapter_type"] == adapter_type:
            return item["probe_models"][0]
    return models[0] if models else ""


def choose_probe_models(adapter_type: str, models: list[str], limit: int = 4) -> list[str]:
    candidates: list[str] = []
    preferred = choose_probe_model(adapter_type, models)
    if preferred:
        candidates.append(preferred)

    family_keywords = PROBE_FAMILY_KEYWORDS.get(adapter_type, [])
    avoid_keywords = PROBE_AVOID_KEYWORDS.get(adapter_type, [])
    for model in models:
        lowered = model.lower()
        if any(token in lowered for token in PROBE_NEGATIVE_KEYWORDS):
            continue
        if family_keywords and not any(token in lowered for token in family_keywords):
            continue
        if model not in candidates:
            candidates.append(model)
        if len(candidates) >= limit:
            return candidates

    for model in models:
        lowered = model.lower()
        if any(token in lowered for token in PROBE_NEGATIVE_KEYWORDS):
            continue
        if avoid_keywords and any(token in lowered for token in avoid_keywords):
            continue
        if model not in candidates:
            candidates.append(model)
        if len(candidates) >= limit:
            return candidates

    for item in PROTOCOLS:
        if item["adapter_type"] == adapter_type:
            for fallback in item["probe_models"]:
                if fallback not in candidates:
                    candidates.append(fallback)
                if len(candidates) >= limit:
                    return candidates
            break
    return candidates[:limit]


def protocol_supported(adapter_type: str, models: list[str], probe_result: CheckResult | None, probe_error: str) -> bool:
    if not probe_result:
        return False
    error_text = (probe_error or probe_result.error or "").lower()
    if probe_result.status == "error":
        return False
    if probe_result.response_shape in {"", "str"}:
        return False
    if adapter_type == "openai_responses" and "required key [messages]" in error_text:
        return False
    if adapter_type == "openai_chat" and ("required key [input]" in error_text or "max_output_tokens" in error_text):
        return False
    if adapter_type == "gemini_generate_content" and (
        "no available channel for model" in error_text
        or "model not found" in error_text
        or "permission denied" in error_text
    ):
        return False
    return True


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def table_exists(self, name: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
                (name,),
            ).fetchone()
        return bool(row)

    def column_exists(self, table: str, column: str) -> bool:
        with self.connect() as conn:
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(row["name"] == column for row in rows)

    def add_column_if_missing(self, table: str, column: str, definition: str) -> None:
        if self.column_exists(table, column):
            return
        with self.connect() as conn:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;

                CREATE TABLE IF NOT EXISTS stations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    network_mode TEXT NOT NULL DEFAULT 'auto',
                    proxy_url TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    detect_max_concurrency INTEGER NOT NULL DEFAULT 2,
                    detect_min_interval_ms INTEGER NOT NULL DEFAULT 800,
                    detect_cooldown_seconds INTEGER NOT NULL DEFAULT 60,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS api_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    station_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    api_key TEXT NOT NULL,
                    group_name TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    network_mode TEXT DEFAULT '',
                    proxy_url TEXT DEFAULT '',
                    seed_models TEXT DEFAULT '',
                    timeout_seconds INTEGER NOT NULL DEFAULT 30,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (station_id) REFERENCES stations(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS protocol_bindings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key_id INTEGER NOT NULL,
                    adapter_type TEXT NOT NULL,
                    label TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'unknown',
                    supported INTEGER NOT NULL DEFAULT 0,
                    model_count INTEGER NOT NULL DEFAULT 0,
                    probe_model TEXT DEFAULT '',
                    response_shape TEXT DEFAULT '',
                    preview TEXT DEFAULT '',
                    last_network_mode TEXT DEFAULT '',
                    last_network_route TEXT DEFAULT '',
                    last_proxy_url_masked TEXT DEFAULT '',
                    last_error TEXT DEFAULT '',
                    detected_at TEXT DEFAULT '',
                    last_discovered_at TEXT DEFAULT '',
                    last_checked_at TEXT DEFAULT '',
                    UNIQUE(key_id, adapter_type),
                    FOREIGN KEY (key_id) REFERENCES api_keys(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS binding_models (
                    binding_id INTEGER NOT NULL,
                    model_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (binding_id, model_id),
                    FOREIGN KEY (binding_id) REFERENCES protocol_bindings(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS binding_checks (
                    binding_id INTEGER NOT NULL,
                    model_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    available INTEGER NOT NULL DEFAULT 0,
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    response_shape TEXT DEFAULT '',
                    preview TEXT DEFAULT '',
                    network_mode TEXT DEFAULT '',
                    network_route TEXT DEFAULT '',
                    proxy_url_masked TEXT DEFAULT '',
                    error TEXT DEFAULT '',
                    checked_at TEXT NOT NULL,
                    PRIMARY KEY (binding_id, model_id),
                    FOREIGN KEY (binding_id) REFERENCES protocol_bindings(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS binding_check_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    binding_id INTEGER NOT NULL,
                    model_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    available INTEGER NOT NULL DEFAULT 0,
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    response_shape TEXT DEFAULT '',
                    preview TEXT DEFAULT '',
                    network_mode TEXT DEFAULT '',
                    network_route TEXT DEFAULT '',
                    proxy_url_masked TEXT DEFAULT '',
                    error TEXT DEFAULT '',
                    checked_at TEXT NOT NULL,
                    FOREIGN KEY (binding_id) REFERENCES protocol_bindings(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    scope_type TEXT DEFAULT '',
                    scope_id INTEGER,
                    title TEXT NOT NULL,
                    trigger TEXT DEFAULT 'manual',
                    detail TEXT DEFAULT '',
                    total_steps INTEGER NOT NULL DEFAULT 0,
                    completed_steps INTEGER NOT NULL DEFAULT 0,
                    current_step TEXT DEFAULT '',
                    result_json TEXT DEFAULT '',
                    error_text TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    started_at TEXT DEFAULT '',
                    finished_at TEXT DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_binding_history_checked_at
                ON binding_check_history (checked_at DESC);

                CREATE INDEX IF NOT EXISTS idx_jobs_created_at
                ON jobs (created_at DESC);
                """
            )
            conn.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO NOTHING
                """,
                (SCHEDULER_KEY, json.dumps(DEFAULT_SCHEDULER), utcnow()),
            )
            conn.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO NOTHING
                """,
                (LEGACY_MIGRATION_KEY, "0", utcnow()),
            )
        self.add_column_if_missing("stations", "network_mode", "TEXT NOT NULL DEFAULT 'auto'")
        self.add_column_if_missing("stations", "proxy_url", "TEXT DEFAULT ''")
        self.add_column_if_missing("api_keys", "network_mode", "TEXT DEFAULT ''")
        self.add_column_if_missing("api_keys", "proxy_url", "TEXT DEFAULT ''")
        self.add_column_if_missing("protocol_bindings", "last_network_mode", "TEXT DEFAULT ''")
        self.add_column_if_missing("protocol_bindings", "last_network_route", "TEXT DEFAULT ''")
        self.add_column_if_missing("protocol_bindings", "last_proxy_url_masked", "TEXT DEFAULT ''")
        self.add_column_if_missing("binding_checks", "network_mode", "TEXT DEFAULT ''")
        self.add_column_if_missing("binding_checks", "network_route", "TEXT DEFAULT ''")
        self.add_column_if_missing("binding_checks", "proxy_url_masked", "TEXT DEFAULT ''")
        self.add_column_if_missing("binding_check_history", "network_mode", "TEXT DEFAULT ''")
        self.add_column_if_missing("binding_check_history", "network_route", "TEXT DEFAULT ''")
        self.add_column_if_missing("binding_check_history", "proxy_url_masked", "TEXT DEFAULT ''")
        self.add_column_if_missing("stations", "detect_max_concurrency", "INTEGER NOT NULL DEFAULT 2")
        self.add_column_if_missing("stations", "detect_min_interval_ms", "INTEGER NOT NULL DEFAULT 800")
        self.add_column_if_missing("stations", "detect_cooldown_seconds", "INTEGER NOT NULL DEFAULT 60")
        self.migrate_legacy_credentials()

    def migrate_legacy_credentials(self) -> None:
        if not self.table_exists("credentials"):
            return
        if not self.table_exists("discovered_models") or not self.table_exists("model_checks"):
            return
        with self.connect() as conn:
            flag = conn.execute("SELECT value FROM app_settings WHERE key = ?", (LEGACY_MIGRATION_KEY,)).fetchone()
            if flag and flag["value"] == "1":
                return

            legacy_rows = conn.execute(
                """
                SELECT c.*, s.base_url
                FROM credentials c
                JOIN stations s ON s.id = c.station_id
                ORDER BY c.id ASC
                """
            ).fetchall()
            for legacy in legacy_rows:
                key_id = conn.execute(
                    """
                    INSERT INTO api_keys (
                        station_id, name, api_key, group_name, notes,
                        seed_models, timeout_seconds, enabled, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        legacy["station_id"],
                        legacy["name"],
                        legacy["api_key"],
                        legacy["group_name"],
                        legacy["notes"],
                        legacy["seed_models"],
                        legacy["timeout_seconds"],
                        legacy["enabled"],
                        legacy["created_at"],
                    ),
                ).lastrowid

                binding_id = conn.execute(
                    """
                    INSERT INTO protocol_bindings (
                        key_id, adapter_type, label, status, supported, model_count,
                        detected_at, last_discovered_at, last_checked_at
                    )
                    VALUES (?, ?, ?, 'legacy', 1, 0, ?, ?, '')
                    """,
                    (
                        key_id,
                        legacy["adapter_type"],
                        protocol_label(legacy["adapter_type"]),
                        utcnow(),
                        utcnow(),
                    ),
                ).lastrowid

                legacy_models = conn.execute(
                    "SELECT model_id, source, fetched_at FROM discovered_models WHERE credential_id = ?",
                    (legacy["id"],),
                ).fetchall()
                for row in legacy_models:
                    conn.execute(
                        """
                        INSERT INTO binding_models (binding_id, model_id, source, fetched_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (binding_id, row["model_id"], row["source"], row["fetched_at"]),
                    )

                legacy_checks = conn.execute(
                    """
                    SELECT model_id, status, available, latency_ms, response_shape,
                           preview, error, checked_at
                    FROM model_checks
                    WHERE credential_id = ?
                    """,
                    (legacy["id"],),
                ).fetchall()
                for row in legacy_checks:
                    conn.execute(
                        """
                        INSERT INTO binding_checks (
                            binding_id, model_id, status, available, latency_ms,
                            response_shape, preview, error, checked_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            binding_id,
                            row["model_id"],
                            row["status"],
                            row["available"],
                            row["latency_ms"],
                            row["response_shape"],
                            row["preview"],
                            row["error"],
                            row["checked_at"],
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO binding_check_history (
                            binding_id, model_id, status, available, latency_ms,
                            response_shape, preview, error, checked_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            binding_id,
                            row["model_id"],
                            row["status"],
                            row["available"],
                            row["latency_ms"],
                            row["response_shape"],
                            row["preview"],
                            row["error"],
                            row["checked_at"],
                        ),
                    )

                conn.execute(
                    "UPDATE protocol_bindings SET model_count = ? WHERE id = ?",
                    (len(legacy_models), binding_id),
                )

            conn.execute(
                "UPDATE app_settings SET value = ?, updated_at = ? WHERE key = ?",
                ("1", utcnow(), LEGACY_MIGRATION_KEY),
            )

    def list_stations(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT s.*,
                       COUNT(k.id) AS key_count
                FROM stations s
                LEFT JOIN api_keys k ON k.station_id = s.id
                GROUP BY s.id
                ORDER BY s.id DESC
                """
            ).fetchall()
        return [self.public_station(row) for row in rows]

    def create_station(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as conn:
            station_id = conn.execute(
                """
                INSERT INTO stations (name, base_url, network_mode, proxy_url, notes, enabled,
                                      detect_max_concurrency, detect_min_interval_ms, detect_cooldown_seconds,
                                      created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["name"].strip(),
                    payload["base_url"].strip().rstrip("/"),
                    normalize_network_mode(payload.get("network_mode")),
                    str(payload.get("proxy_url", "")).strip(),
                    payload.get("notes", "").strip(),
                    1 if as_bool(payload.get("enabled"), True) else 0,
                    int(payload.get("detect_max_concurrency", 2)),
                    int(payload.get("detect_min_interval_ms", 800)),
                    int(payload.get("detect_cooldown_seconds", 60)),
                    utcnow(),
                ),
            ).lastrowid
        return self.get_station(station_id)

    def update_station(self, station_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE stations
                SET name = ?, base_url = ?, network_mode = ?, proxy_url = ?, notes = ?, enabled = ?,
                    detect_max_concurrency = ?, detect_min_interval_ms = ?, detect_cooldown_seconds = ?
                WHERE id = ?
                """,
                (
                    payload["name"].strip(),
                    payload["base_url"].strip().rstrip("/"),
                    normalize_network_mode(payload.get("network_mode")),
                    str(payload.get("proxy_url", "")).strip(),
                    payload.get("notes", "").strip(),
                    1 if as_bool(payload.get("enabled"), True) else 0,
                    int(payload.get("detect_max_concurrency", 2)),
                    int(payload.get("detect_min_interval_ms", 800)),
                    int(payload.get("detect_cooldown_seconds", 60)),
                    station_id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError("station not found")
        return self.get_station(station_id)

    def delete_station(self, station_id: int) -> None:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM stations WHERE id = ?", (station_id,))
            if cursor.rowcount == 0:
                raise KeyError("station not found")

    def get_station(self, station_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT s.*,
                       COUNT(k.id) AS key_count
                FROM stations s
                LEFT JOIN api_keys k ON k.station_id = s.id
                WHERE s.id = ?
                GROUP BY s.id
                """,
                (station_id,),
            ).fetchone()
        if not row:
            raise KeyError("station not found")
        return self.public_station(row)

    def list_keys(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT k.*,
                       s.name AS station_name,
                       s.base_url,
                       s.network_mode AS station_network_mode,
                       s.proxy_url AS station_proxy_url,
                       (SELECT COUNT(*) FROM protocol_bindings pb WHERE pb.key_id = k.id) AS binding_count,
                       (SELECT COUNT(*) FROM protocol_bindings pb WHERE pb.key_id = k.id AND pb.supported = 1) AS supported_binding_count,
                       (
                         SELECT COUNT(DISTINCT bc.binding_id)
                         FROM binding_checks bc
                         JOIN binding_models bm
                           ON bm.binding_id = bc.binding_id
                          AND bm.model_id = bc.model_id
                         JOIN protocol_bindings pb ON pb.id = bc.binding_id
                         WHERE pb.key_id = k.id AND bc.available = 1
                       ) AS available_binding_count,
                       (
                         SELECT COUNT(*)
                         FROM binding_checks bc
                         JOIN binding_models bm
                           ON bm.binding_id = bc.binding_id
                          AND bm.model_id = bc.model_id
                         JOIN protocol_bindings pb ON pb.id = bc.binding_id
                         WHERE pb.key_id = k.id AND bc.available = 1
                       ) AS available_model_count,
                       (SELECT MAX(pb.detected_at) FROM protocol_bindings pb WHERE pb.key_id = k.id) AS last_detected_at
                FROM api_keys k
                JOIN stations s ON s.id = k.station_id
                ORDER BY k.id DESC
                """
            ).fetchall()
        return [self.public_key(row) for row in rows]

    def create_key(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as conn:
            key_id = conn.execute(
                """
                INSERT INTO api_keys (
                    station_id, name, api_key, group_name, notes, network_mode, proxy_url,
                    seed_models, timeout_seconds, enabled, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(payload["station_id"]),
                    payload["name"].strip(),
                    payload["api_key"].strip(),
                    payload.get("group_name", "").strip(),
                    payload.get("notes", "").strip(),
                    normalize_network_mode(payload.get("network_mode"), allow_inherit=True),
                    str(payload.get("proxy_url", "")).strip(),
                    normalize_multiline_models(payload.get("seed_models")),
                    int(payload.get("timeout_seconds") or 30),
                    1 if as_bool(payload.get("enabled"), True) else 0,
                    utcnow(),
                ),
            ).lastrowid
        return self.get_key(key_id)

    def update_key(self, key_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.get_key_record(key_id)
        api_key = str(payload.get("api_key", "")).strip() or current["api_key"]
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE api_keys
                SET station_id = ?, name = ?, api_key = ?, group_name = ?, notes = ?,
                    network_mode = ?, proxy_url = ?,
                    seed_models = ?, timeout_seconds = ?, enabled = ?
                WHERE id = ?
                """,
                (
                    int(payload["station_id"]),
                    payload["name"].strip(),
                    api_key,
                    payload.get("group_name", "").strip(),
                    payload.get("notes", "").strip(),
                    normalize_network_mode(payload.get("network_mode"), allow_inherit=True),
                    str(payload.get("proxy_url", "")).strip(),
                    normalize_multiline_models(payload.get("seed_models")),
                    int(payload.get("timeout_seconds") or 30),
                    1 if as_bool(payload.get("enabled"), True) else 0,
                    key_id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError("key not found")
        return self.get_key(key_id)

    def delete_key(self, key_id: int) -> None:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM api_keys WHERE id = ?", (key_id,))
            if cursor.rowcount == 0:
                raise KeyError("key not found")

    def get_key_record(self, key_id: int) -> sqlite3.Row:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT k.*,
                       s.name AS station_name,
                       s.base_url,
                       s.network_mode AS station_network_mode,
                       s.proxy_url AS station_proxy_url,
                       s.detect_max_concurrency,
                       s.detect_min_interval_ms,
                       s.detect_cooldown_seconds
                FROM api_keys k
                JOIN stations s ON s.id = k.station_id
                WHERE k.id = ?
                """,
                (key_id,),
            ).fetchone()
        if not row:
            raise KeyError("key not found")
        return row

    def get_key(self, key_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT k.*,
                       s.name AS station_name,
                       s.base_url,
                       s.network_mode AS station_network_mode,
                       s.proxy_url AS station_proxy_url,
                       (SELECT COUNT(*) FROM protocol_bindings pb WHERE pb.key_id = k.id) AS binding_count,
                       (SELECT COUNT(*) FROM protocol_bindings pb WHERE pb.key_id = k.id AND pb.supported = 1) AS supported_binding_count,
                       (
                         SELECT COUNT(DISTINCT bc.binding_id)
                         FROM binding_checks bc
                         JOIN binding_models bm
                           ON bm.binding_id = bc.binding_id
                          AND bm.model_id = bc.model_id
                         JOIN protocol_bindings pb ON pb.id = bc.binding_id
                         WHERE pb.key_id = k.id AND bc.available = 1
                       ) AS available_binding_count,
                       (
                         SELECT COUNT(*)
                         FROM binding_checks bc
                         JOIN binding_models bm
                           ON bm.binding_id = bc.binding_id
                          AND bm.model_id = bc.model_id
                         JOIN protocol_bindings pb ON pb.id = bc.binding_id
                         WHERE pb.key_id = k.id AND bc.available = 1
                       ) AS available_model_count,
                       (SELECT MAX(pb.detected_at) FROM protocol_bindings pb WHERE pb.key_id = k.id) AS last_detected_at
                FROM api_keys k
                JOIN stations s ON s.id = k.station_id
                WHERE k.id = ?
                """,
                (key_id,),
            ).fetchone()
        if not row:
            raise KeyError("key not found")
        return self.public_key(row)

    def enabled_keys(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT k.*,
                       s.name AS station_name,
                       s.base_url,
                       s.network_mode AS station_network_mode,
                       s.proxy_url AS station_proxy_url,
                       s.detect_max_concurrency,
                       s.detect_min_interval_ms,
                       s.detect_cooldown_seconds
                FROM api_keys k
                JOIN stations s ON s.id = k.station_id
                WHERE k.enabled = 1 AND s.enabled = 1
                ORDER BY k.id ASC
                """
            ).fetchall()

    def upsert_binding(self, key_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM protocol_bindings WHERE key_id = ? AND adapter_type = ?",
                (key_id, payload["adapter_type"]),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE protocol_bindings
                    SET label = ?, status = ?, supported = ?, model_count = ?,
                        probe_model = ?, response_shape = ?, preview = ?,
                        last_network_mode = ?, last_network_route = ?, last_proxy_url_masked = ?, last_error = ?,
                        detected_at = ?, last_discovered_at = ?, last_checked_at = ?
                    WHERE id = ?
                    """,
                    (
                        payload["label"],
                        payload["status"],
                        payload["supported"],
                        payload["model_count"],
                        payload.get("probe_model", ""),
                        payload.get("response_shape", ""),
                        payload.get("preview", ""),
                        payload.get("last_network_mode", ""),
                        payload.get("last_network_route", ""),
                        payload.get("last_proxy_url_masked", ""),
                        payload.get("last_error", ""),
                        payload.get("detected_at", ""),
                        payload.get("last_discovered_at", ""),
                        payload.get("last_checked_at", ""),
                        existing["id"],
                    ),
                )
                binding_id = existing["id"]
            else:
                binding_id = conn.execute(
                    """
                    INSERT INTO protocol_bindings (
                        key_id, adapter_type, label, status, supported, model_count,
                        probe_model, response_shape, preview,
                        last_network_mode, last_network_route, last_proxy_url_masked, last_error,
                        detected_at, last_discovered_at, last_checked_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        key_id,
                        payload["adapter_type"],
                        payload["label"],
                        payload["status"],
                        payload["supported"],
                        payload["model_count"],
                        payload.get("probe_model", ""),
                        payload.get("response_shape", ""),
                        payload.get("preview", ""),
                        payload.get("last_network_mode", ""),
                        payload.get("last_network_route", ""),
                        payload.get("last_proxy_url_masked", ""),
                        payload.get("last_error", ""),
                        payload.get("detected_at", ""),
                        payload.get("last_discovered_at", ""),
                        payload.get("last_checked_at", ""),
                    ),
                ).lastrowid
        return self.get_binding(binding_id)

    def replace_binding_models(self, binding_id: int, models: Iterable[str], source: str) -> None:
        now = utcnow()
        models_list = []
        for model in models:
            if model and model not in models_list:
                models_list.append(model)
        with self.connect() as conn:
            conn.execute("DELETE FROM binding_models WHERE binding_id = ?", (binding_id,))
            for model_id in models_list:
                conn.execute(
                    """
                    INSERT INTO binding_models (binding_id, model_id, source, fetched_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (binding_id, model_id, source, now),
                )
            conn.execute(
                """
                UPDATE protocol_bindings
                SET model_count = ?, last_discovered_at = ?
                WHERE id = ?
                """,
                (len(models_list), now, binding_id),
            )

    def list_bindings(self, key_id: int | None = None) -> list[dict[str, Any]]:
        sql = """
            SELECT pb.*,
                   k.name AS key_name,
                   k.group_name,
                   k.enabled AS key_enabled,
                   k.station_id,
                   s.name AS station_name,
                   s.base_url,
                   (
                     SELECT COUNT(*)
                     FROM binding_checks bc
                     JOIN binding_models bm
                       ON bm.binding_id = bc.binding_id
                      AND bm.model_id = bc.model_id
                     WHERE bc.binding_id = pb.id
                   ) AS checked_model_count,
                   (
                     SELECT COUNT(*)
                     FROM binding_checks bc
                     JOIN binding_models bm
                       ON bm.binding_id = bc.binding_id
                      AND bm.model_id = bc.model_id
                     WHERE bc.binding_id = pb.id AND bc.available = 1
                   ) AS available_model_count,
                   (
                     SELECT COUNT(*)
                     FROM binding_checks bc
                     JOIN binding_models bm
                       ON bm.binding_id = bc.binding_id
                      AND bm.model_id = bc.model_id
                     WHERE bc.binding_id = pb.id AND bc.status = 'partial'
                   ) AS partial_model_count,
                   (
                     SELECT COUNT(*)
                     FROM binding_checks bc
                     JOIN binding_models bm
                       ON bm.binding_id = bc.binding_id
                      AND bm.model_id = bc.model_id
                     WHERE bc.binding_id = pb.id AND bc.status = 'empty'
                   ) AS empty_model_count,
                   (
                     SELECT COUNT(*)
                     FROM binding_checks bc
                     JOIN binding_models bm
                       ON bm.binding_id = bc.binding_id
                      AND bm.model_id = bc.model_id
                     WHERE bc.binding_id = pb.id AND bc.status = 'error'
                   ) AS error_model_count,
                   (SELECT COUNT(*) FROM binding_check_history h WHERE h.binding_id = pb.id) AS history_check_count,
                   (SELECT COUNT(*) FROM binding_check_history h WHERE h.binding_id = pb.id AND h.available = 1) AS history_success_count
            FROM protocol_bindings pb
            JOIN api_keys k ON k.id = pb.key_id
            JOIN stations s ON s.id = k.station_id
        """
        params: list[Any] = []
        if key_id:
            sql += " WHERE pb.key_id = ?"
            params.append(key_id)
        sql += " ORDER BY pb.key_id DESC, pb.adapter_type ASC"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def get_binding_record(self, binding_id: int) -> sqlite3.Row:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT pb.*,
                       k.name AS key_name,
                       k.group_name,
                       k.enabled AS key_enabled,
                       k.api_key,
                       k.timeout_seconds,
                       k.network_mode,
                       k.proxy_url,
                       k.seed_models,
                       s.id AS station_id,
                       s.name AS station_name,
                       s.base_url,
                       s.network_mode AS station_network_mode,
                       s.proxy_url AS station_proxy_url,
                       s.enabled AS station_enabled,
                       s.detect_max_concurrency,
                       s.detect_min_interval_ms,
                       s.detect_cooldown_seconds
                FROM protocol_bindings pb
                JOIN api_keys k ON k.id = pb.key_id
                JOIN stations s ON s.id = k.station_id
                WHERE pb.id = ?
                """,
                (binding_id,),
            ).fetchone()
        if not row:
            raise KeyError("binding not found")
        return row

    def get_binding(self, binding_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT pb.*,
                       k.name AS key_name,
                       k.group_name,
                       k.enabled AS key_enabled,
                       k.network_mode,
                       k.proxy_url,
                       s.id AS station_id,
                       s.name AS station_name,
                       s.base_url,
                       s.network_mode AS station_network_mode,
                       s.proxy_url AS station_proxy_url
                FROM protocol_bindings pb
                JOIN api_keys k ON k.id = pb.key_id
                JOIN stations s ON s.id = k.station_id
                WHERE pb.id = ?
                """,
                (binding_id,),
            ).fetchone()
        if not row:
            raise KeyError("binding not found")
        return dict(row)

    def find_binding_record(self, key_id: int, adapter_type: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT pb.*,
                       k.name AS key_name,
                       k.group_name,
                       k.enabled AS key_enabled,
                       k.api_key,
                       k.timeout_seconds,
                       k.network_mode,
                       k.proxy_url,
                       k.seed_models,
                       s.id AS station_id,
                       s.name AS station_name,
                       s.base_url,
                       s.network_mode AS station_network_mode,
                       s.proxy_url AS station_proxy_url,
                       s.enabled AS station_enabled
                FROM protocol_bindings pb
                JOIN api_keys k ON k.id = pb.key_id
                JOIN stations s ON s.id = k.station_id
                WHERE pb.key_id = ? AND pb.adapter_type = ?
                """,
                (key_id, adapter_type),
            ).fetchone()

    def list_models_for_binding(self, binding_id: int) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT model_id FROM binding_models WHERE binding_id = ? ORDER BY model_id ASC",
                (binding_id,),
            ).fetchall()
        return [row["model_id"] for row in rows]

    def list_current_binding_checks(self, binding_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM binding_checks
                WHERE binding_id = ?
                  AND EXISTS (
                    SELECT 1
                    FROM binding_models bm
                    WHERE bm.binding_id = binding_checks.binding_id
                      AND bm.model_id = binding_checks.model_id
                  )
                ORDER BY checked_at DESC, model_id ASC
                """,
                (binding_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_binding_models_with_checks(self, binding_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT bm.model_id,
                       bm.source,
                       bm.fetched_at,
                       bc.status,
                       bc.available,
                       bc.latency_ms,
                       bc.response_shape,
                       bc.preview,
                       bc.network_mode,
                       bc.network_route,
                       bc.proxy_url_masked,
                       bc.error,
                       bc.checked_at
                FROM binding_models bm
                LEFT JOIN binding_checks bc
                  ON bc.binding_id = bm.binding_id
                 AND bc.model_id = bm.model_id
                WHERE bm.binding_id = ?
                ORDER BY
                    CASE
                        WHEN bc.available = 1 THEN 0
                        WHEN bc.status = 'partial' THEN 1
                        WHEN bc.status = 'empty' THEN 2
                        WHEN bc.status = 'error' THEN 3
                        ELSE 4
                    END,
                    COALESCE(bc.checked_at, '') DESC,
                    bm.model_id ASC
                """,
                (binding_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_binding_detail(self, binding_id: int) -> dict[str, Any]:
        binding = self.get_binding(binding_id)
        models = self.list_binding_models_with_checks(binding_id)
        return {
            "binding": binding,
            "models": models,
            "summary": {
                "model_count": len(models),
                "available_count": sum(1 for row in models if row.get("available")),
                "checked_count": sum(1 for row in models if row.get("status")),
                "partial_count": sum(1 for row in models if row.get("status") == "partial"),
                "empty_count": sum(1 for row in models if row.get("status") == "empty"),
                "error_count": sum(1 for row in models if row.get("status") == "error"),
                "rate_limited_count": sum(1 for row in models if row.get("status") == "rate_limited"),
            },
        }

    def upsert_binding_check(self, binding_id: int, model_id: str, result: CheckResult) -> None:
        checked_at = utcnow()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO binding_checks (
                    binding_id, model_id, status, available, latency_ms,
                    response_shape, preview, network_mode, network_route, proxy_url_masked, error, checked_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(binding_id, model_id)
                DO UPDATE SET
                    status = excluded.status,
                    available = excluded.available,
                    latency_ms = excluded.latency_ms,
                    response_shape = excluded.response_shape,
                    preview = excluded.preview,
                    network_mode = excluded.network_mode,
                    network_route = excluded.network_route,
                    proxy_url_masked = excluded.proxy_url_masked,
                    error = excluded.error,
                    checked_at = excluded.checked_at
                """,
                (
                    binding_id,
                    model_id,
                    result.status,
                    1 if result.available else 0,
                    result.latency_ms,
                    result.response_shape,
                    result.preview,
                    result.network_mode,
                    result.network_route,
                    result.proxy_url_masked,
                    result.error or "",
                    checked_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO binding_check_history (
                    binding_id, model_id, status, available, latency_ms,
                    response_shape, preview, network_mode, network_route, proxy_url_masked, error, checked_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    binding_id,
                    model_id,
                    result.status,
                    1 if result.available else 0,
                    result.latency_ms,
                    result.response_shape,
                    result.preview,
                    result.network_mode,
                    result.network_route,
                    result.proxy_url_masked,
                    result.error or "",
                    checked_at,
                ),
            )
            conn.execute(
                "UPDATE protocol_bindings SET last_checked_at = ? WHERE id = ?",
                (checked_at, binding_id),
            )

    def search_models(
        self,
        query: str,
        available_only: bool,
        filters: dict[str, Any] | None = None,
        sort_by: str = "model_id",
        sort_dir: str = "asc",
    ) -> list[dict[str, Any]]:
        filters = filters or {}
        status_filter = str(filters.get("status") or "").strip().lower()
        supported_filter = str(filters.get("supported") or "").strip().lower()
        available_filter = str(filters.get("available") or "").strip().lower()
        station_id_filter = str(filters.get("station_id") or "").strip()
        key_id_filter = str(filters.get("key_id") or "").strip()
        station_query = str(filters.get("station_name") or "").strip()
        key_query = str(filters.get("key_name") or "").strip()
        protocol_query = str(filters.get("protocol_label") or "").strip()
        preview_query = str(filters.get("preview") or "").strip()
        error_query = str(filters.get("error") or "").strip()
        min_latency = str(filters.get("min_latency_ms") or "").strip()
        max_latency = str(filters.get("max_latency_ms") or "").strip()
        min_success_rate = parse_float(filters.get("min_success_rate"))
        max_success_rate = parse_float(filters.get("max_success_rate"))

        sql = """
            SELECT bm.model_id,
                   bm.source,
                   bm.fetched_at,
                   pb.id AS binding_id,
                   pb.adapter_type,
                   pb.label AS protocol_label,
                   pb.status AS protocol_status,
                   pb.supported,
                   k.id AS key_id,
                   k.name AS key_name,
                   k.group_name,
                   s.id AS station_id,
                   s.name AS station_name,
                   s.base_url,
                   bc.status,
                   bc.available,
                   bc.latency_ms,
                   bc.response_shape,
                   bc.preview,
                   bc.network_mode,
                   bc.network_route,
                   bc.proxy_url_masked,
                   bc.error,
                   bc.checked_at,
                   (
                     SELECT COUNT(*)
                     FROM binding_check_history h
                     WHERE h.binding_id = bm.binding_id AND h.model_id = bm.model_id
                   ) AS history_check_count,
                   (
                     SELECT COUNT(*)
                     FROM binding_check_history h
                     WHERE h.binding_id = bm.binding_id AND h.model_id = bm.model_id AND h.available = 1
                   ) AS history_success_count,
                   CASE
                     WHEN (
                       SELECT COUNT(*)
                       FROM binding_check_history h
                       WHERE h.binding_id = bm.binding_id AND h.model_id = bm.model_id
                     ) = 0 THEN NULL
                     ELSE ROUND(
                       (
                         SELECT COUNT(*)
                         FROM binding_check_history h
                         WHERE h.binding_id = bm.binding_id AND h.model_id = bm.model_id AND h.available = 1
                       ) * 100.0 / (
                         SELECT COUNT(*)
                         FROM binding_check_history h
                         WHERE h.binding_id = bm.binding_id AND h.model_id = bm.model_id
                       ),
                       1
                     )
                   END AS success_rate
            FROM binding_models bm
            JOIN protocol_bindings pb ON pb.id = bm.binding_id
            JOIN api_keys k ON k.id = pb.key_id
            JOIN stations s ON s.id = k.station_id
            LEFT JOIN binding_checks bc
                   ON bc.binding_id = bm.binding_id
                  AND bc.model_id = bm.model_id
            WHERE (? = '' OR bm.model_id LIKE '%' || ? || '%')
        """
        params: list[Any] = [query, query]
        if available_only:
            sql += " AND bc.available = 1"
        if station_id_filter.isdigit():
            sql += " AND s.id = ?"
            params.append(int(station_id_filter))
        if station_query:
            sql += " AND s.name LIKE '%' || ? || '%'"
            params.append(station_query)
        if key_id_filter.isdigit():
            sql += " AND k.id = ?"
            params.append(int(key_id_filter))
        if key_query:
            sql += " AND k.name LIKE '%' || ? || '%'"
            params.append(key_query)
        if protocol_query:
            sql += " AND pb.label LIKE '%' || ? || '%'"
            params.append(protocol_query)
        if preview_query:
            sql += " AND COALESCE(bc.preview, '') LIKE '%' || ? || '%'"
            params.append(preview_query)
        if error_query:
            sql += " AND COALESCE(bc.error, '') LIKE '%' || ? || '%'"
            params.append(error_query)
        if status_filter:
            if status_filter == "unchecked":
                sql += " AND bc.status IS NULL"
            else:
                sql += " AND COALESCE(bc.status, '') = ?"
                params.append(status_filter)
        if supported_filter in {"1", "0"}:
            sql += " AND pb.supported = ?"
            params.append(int(supported_filter))
        if available_filter == "1":
            sql += " AND bc.available = 1"
        elif available_filter == "0":
            sql += " AND COALESCE(bc.available, 0) = 0"
        elif available_filter == "unchecked":
            sql += " AND bc.available IS NULL"
        if min_latency.isdigit():
            sql += " AND COALESCE(bc.latency_ms, 0) >= ?"
            params.append(int(min_latency))
        if max_latency.isdigit():
            sql += " AND COALESCE(bc.latency_ms, 0) <= ?"
            params.append(int(max_latency))
        if min_success_rate is not None:
            sql += """
                AND COALESCE(
                  CASE
                    WHEN (
                      SELECT COUNT(*)
                      FROM binding_check_history h
                      WHERE h.binding_id = bm.binding_id AND h.model_id = bm.model_id
                    ) = 0 THEN NULL
                    ELSE ROUND(
                      (
                        SELECT COUNT(*)
                        FROM binding_check_history h
                        WHERE h.binding_id = bm.binding_id AND h.model_id = bm.model_id AND h.available = 1
                      ) * 100.0 / (
                        SELECT COUNT(*)
                        FROM binding_check_history h
                        WHERE h.binding_id = bm.binding_id AND h.model_id = bm.model_id
                      ),
                      1
                    )
                  END,
                  -1
                ) >= ?
            """
            params.append(min_success_rate)
        if max_success_rate is not None:
            sql += """
                AND COALESCE(
                  CASE
                    WHEN (
                      SELECT COUNT(*)
                      FROM binding_check_history h
                      WHERE h.binding_id = bm.binding_id AND h.model_id = bm.model_id
                    ) = 0 THEN NULL
                    ELSE ROUND(
                      (
                        SELECT COUNT(*)
                        FROM binding_check_history h
                        WHERE h.binding_id = bm.binding_id AND h.model_id = bm.model_id AND h.available = 1
                      ) * 100.0 / (
                        SELECT COUNT(*)
                        FROM binding_check_history h
                        WHERE h.binding_id = bm.binding_id AND h.model_id = bm.model_id
                      ),
                      1
                    )
                  END,
                  -1
                ) <= ?
            """
            params.append(max_success_rate)

        orderable = {
            "model_id": "bm.model_id",
            "station_name": "s.name",
            "key_name": "k.name",
            "protocol_label": "pb.label",
            "supported": "pb.supported",
            "status": "COALESCE(bc.status, '')",
            "available": "COALESCE(bc.available, -1)",
            "history_success_rate": "COALESCE(success_rate, -1)",
            "latency_ms": "COALESCE(bc.latency_ms, -1)",
            "preview": "COALESCE(bc.preview, '')",
            "error": "COALESCE(bc.error, '')",
            "source": "bm.source",
            "fetched_at": "bm.fetched_at",
            "checked_at": "COALESCE(bc.checked_at, '')",
        }
        order_expr = orderable.get(sort_by, "bm.model_id")
        order_direction = "DESC" if str(sort_dir).lower() == "desc" else "ASC"
        sql += f" ORDER BY {order_expr} {order_direction}, bm.model_id ASC, s.name ASC"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def recent_history(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT h.id,
                       h.model_id,
                       h.status,
                       h.available,
                       h.latency_ms,
                       h.response_shape,
                       h.preview,
                       h.network_mode,
                       h.network_route,
                       h.proxy_url_masked,
                       h.error,
                       h.checked_at,
                       pb.id AS binding_id,
                       pb.adapter_type,
                       pb.label AS protocol_label,
                       k.id AS key_id,
                       k.name AS key_name,
                       k.group_name,
                       s.id AS station_id,
                       s.name AS station_name
                FROM binding_check_history h
                JOIN protocol_bindings pb ON pb.id = h.binding_id
                JOIN api_keys k ON k.id = pb.key_id
                JOIN stations s ON s.id = k.station_id
                ORDER BY h.checked_at DESC, h.id DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def summary(self) -> dict[str, Any]:
        scheduler = self.get_scheduler_settings()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM stations) AS station_count,
                    (SELECT COUNT(*) FROM api_keys) AS key_count,
                    (SELECT COUNT(*) FROM protocol_bindings) AS binding_count,
                    (SELECT COUNT(*) FROM protocol_bindings WHERE supported = 1) AS supported_binding_count,
                    (SELECT COUNT(*) FROM binding_models) AS model_count,
                    (
                      SELECT COUNT(*)
                      FROM binding_checks bc
                      JOIN binding_models bm
                        ON bm.binding_id = bc.binding_id
                       AND bm.model_id = bc.model_id
                      WHERE bc.available = 1
                    ) AS available_count,
                    (
                      SELECT COUNT(*)
                      FROM binding_checks bc
                      JOIN binding_models bm
                        ON bm.binding_id = bc.binding_id
                       AND bm.model_id = bc.model_id
                    ) AS checked_count,
                    (SELECT COUNT(*) FROM binding_check_history) AS history_count,
                    (SELECT COUNT(*) FROM jobs WHERE status IN ('queued', 'running')) AS active_job_count
                """
            ).fetchone()
        summary = dict(row)
        summary["scheduler"] = scheduler
        return summary

    def get_scheduler_settings(self) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (SCHEDULER_KEY,)).fetchone()
        if not row:
            return DEFAULT_SCHEDULER.copy()
        try:
            parsed = json.loads(row["value"])
        except json.JSONDecodeError:
            parsed = {}
        settings = DEFAULT_SCHEDULER.copy()
        settings.update(parsed if isinstance(parsed, dict) else {})
        settings["enabled"] = as_bool(settings.get("enabled"), False)
        settings["interval_minutes"] = max(1, int(settings.get("interval_minutes") or 60))
        return settings

    def update_scheduler_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.get_scheduler_settings()
        if "enabled" in payload:
            current["enabled"] = as_bool(payload.get("enabled"), current["enabled"])
        if "interval_minutes" in payload:
            current["interval_minutes"] = max(1, int(payload.get("interval_minutes") or current["interval_minutes"]))
        for key in ("last_cycle_started_at", "last_cycle_finished_at", "last_cycle_status", "last_cycle_note"):
            if key in payload:
                current[key] = str(payload.get(key) or "")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (SCHEDULER_KEY, json.dumps(current, ensure_ascii=False), utcnow()),
            )
        return current

    def mark_incomplete_jobs_interrupted(self) -> None:
        now = utcnow()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'error',
                    detail = '进程重启，任务中断',
                    error_text = CASE
                        WHEN error_text = '' THEN 'process_restarted'
                        ELSE error_text
                    END,
                    finished_at = CASE
                        WHEN finished_at = '' THEN ?
                        ELSE finished_at
                    END
                WHERE status IN ('queued', 'running')
                """,
                (now,),
            )

    def reset_scheduler_if_running(self) -> None:
        settings = self.get_scheduler_settings()
        if settings.get("last_cycle_status") != "running":
            return
        self.update_scheduler_settings(
            {
                "last_cycle_status": "error",
                "last_cycle_note": "process_restarted",
                "last_cycle_finished_at": utcnow(),
            }
        )

    def create_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = utcnow()
        with self.connect() as conn:
            job_id = conn.execute(
                """
                INSERT INTO jobs (
                    job_type, status, scope_type, scope_id, title, trigger, detail,
                    total_steps, completed_steps, current_step, result_json,
                    error_text, created_at, started_at, finished_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["job_type"],
                    payload.get("status", "queued"),
                    payload.get("scope_type", ""),
                    payload.get("scope_id"),
                    payload["title"],
                    payload.get("trigger", "manual"),
                    payload.get("detail", ""),
                    int(payload.get("total_steps") or 0),
                    int(payload.get("completed_steps") or 0),
                    payload.get("current_step", ""),
                    payload.get("result_json", ""),
                    payload.get("error_text", ""),
                    now,
                    payload.get("started_at", ""),
                    payload.get("finished_at", ""),
                ),
            ).lastrowid
        return self.get_job(job_id)

    def update_job(self, job_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.get_job(job_id)
        merged = {**current, **payload}
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = ?, scope_type = ?, scope_id = ?, title = ?, trigger = ?,
                    detail = ?, total_steps = ?, completed_steps = ?, current_step = ?,
                    result_json = ?, error_text = ?, started_at = ?, finished_at = ?
                WHERE id = ?
                """,
                (
                    merged["status"],
                    merged.get("scope_type", ""),
                    merged.get("scope_id"),
                    merged["title"],
                    merged.get("trigger", "manual"),
                    merged.get("detail", ""),
                    int(merged.get("total_steps") or 0),
                    int(merged.get("completed_steps") or 0),
                    merged.get("current_step", ""),
                    merged.get("result_json", ""),
                    merged.get("error_text", ""),
                    merged.get("started_at", ""),
                    merged.get("finished_at", ""),
                    job_id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError("job not found")
        return self.get_job(job_id)

    def get_job(self, job_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            raise KeyError("job not found")
        item = dict(row)
        raw = item.get("result_json") or ""
        if raw:
            try:
                item["result"] = json.loads(raw)
            except json.JSONDecodeError:
                item["result"] = raw
        else:
            item["result"] = None
        item["progress_percent"] = (
            round((item["completed_steps"] / item["total_steps"]) * 100, 1)
            if item.get("total_steps")
            else 0
        )
        return item

    def list_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM jobs
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [self.get_job(row["id"]) for row in rows]

    def find_active_job(self, job_type: str, scope_type: str = "", scope_id: int | None = None) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id
                FROM jobs
                WHERE job_type = ?
                  AND scope_type = ?
                  AND COALESCE(scope_id, -1) = COALESCE(?, -1)
                  AND status IN ('queued', 'running')
                ORDER BY id DESC
                LIMIT 1
                """,
                (job_type, scope_type, scope_id),
            ).fetchone()
        return self.get_job(row["id"]) if row else None

    @staticmethod
    def public_station(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["network_mode"] = normalize_network_mode(item.get("network_mode"))
        item["proxy_url_masked"] = mask_proxy_url(item.get("proxy_url"))
        return item

    @staticmethod
    def public_key(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["api_key_masked"] = mask_secret(item["api_key"])
        item["seed_models_list"] = parse_seed_models(item.get("seed_models"))
        station_mode = normalize_network_mode(item.get("station_network_mode"))
        key_mode = normalize_network_mode(item.get("network_mode"), allow_inherit=True)
        item["network_mode"] = key_mode
        item["proxy_url_masked"] = mask_proxy_url(item.get("proxy_url"))
        item["station_proxy_url_masked"] = mask_proxy_url(item.get("station_proxy_url"))
        item["effective_network_mode"] = key_mode or station_mode
        item["effective_proxy_url"] = str(item.get("proxy_url") or "").strip() or str(item.get("station_proxy_url") or "").strip()
        item["effective_proxy_url_masked"] = mask_proxy_url(item["effective_proxy_url"])
        item["binding_count"] = item.get("binding_count") or 0
        item["supported_binding_count"] = item.get("supported_binding_count") or 0
        item["available_binding_count"] = item.get("available_binding_count") or 0
        item["available_model_count"] = item.get("available_model_count") or 0
        item.pop("station_proxy_url", None)
        return item


class RelayManagerApp:
    def __init__(self, db_path: Path):
        self.db = Database(db_path)
        self.db.mark_incomplete_jobs_interrupted()
        self.db.reset_scheduler_if_running()
        self._run_lock = threading.Lock()
        self._throttles: dict[int, StationThrottle] = {}
        self._throttle_lock = threading.Lock()
        self.jobs = JobManager(self)
        self.scheduler = Scheduler(self)

    def build_adapter(self, adapter_type: str, key_record: dict[str, Any] | sqlite3.Row) -> BaseAdapter:
        adapter_cls = ADAPTERS.get(adapter_type)
        if not adapter_cls:
            raise RelayError(f"unsupported adapter_type: {adapter_type}")
        return adapter_cls(key_record)

    def get_throttle(self, station_id: int) -> StationThrottle:
        with self._throttle_lock:
            if station_id not in self._throttles:
                try:
                    station = self.db.get_station(station_id)
                    self._throttles[station_id] = StationThrottle(
                        station.get("detect_max_concurrency", 2),
                        station.get("detect_min_interval_ms", 800),
                        station.get("detect_cooldown_seconds", 60),
                    )
                except KeyError:
                    self._throttles[station_id] = StationThrottle(2, 800, 60)
            return self._throttles[station_id]

    def invalidate_throttle(self, station_id: int) -> None:
        with self._throttle_lock:
            self._throttles.pop(station_id, None)

    def _detect_protocol_for_key(
        self,
        key_record: sqlite3.Row,
        protocol: dict[str, Any],
    ) -> dict[str, Any]:
        seed_models = parse_seed_models(key_record["seed_models"])
        adapter = self.build_adapter(protocol["adapter_type"], key_record)
        discovered_models: list[str] = []
        list_error = ""
        try:
            discovered_models = run_with_retries(adapter.list_models, LIST_RETRY_ATTEMPTS)
        except Exception as exc:
            list_error = str(exc)
        list_models = merge_model_lists(discovered_models, seed_models)
        source = "adapter_list"
        if discovered_models and seed_models:
            source = "adapter_list+seed_models"
        elif not discovered_models and seed_models:
            source = "seed_models"

        probe_model = ""
        probe_result: CheckResult | None = None
        probe_error = ""
        for candidate in choose_probe_models(protocol["adapter_type"], list_models):
            probe_model = candidate
            current_result = test_model_with_retries(adapter, candidate, PROBE_RETRY_ATTEMPTS)
            current_error = current_result.error or ""

            if current_result:
                probe_result = current_result
                probe_error = current_error

            if current_result and protocol_supported(protocol["adapter_type"], list_models, current_result, current_error):
                break

        response_shape = probe_result.response_shape if probe_result else ""
        preview = probe_result.preview if probe_result else ""
        supported = protocol_supported(protocol["adapter_type"], list_models, probe_result, probe_error)
        last_error = probe_error or (probe_result.error if probe_result and probe_result.error else "")
        if not last_error and not supported:
            last_error = list_error
        status = "unsupported"
        if probe_result and probe_result.status == "rate_limited":
            status = "rate_limited"
        elif supported:
            if probe_result and probe_result.available:
                status = "ok"
            elif probe_result and probe_result.status in {"partial", "empty"}:
                status = "supported"
            else:
                status = "listed"

        existing = self.db.find_binding_record(key_record["id"], protocol["adapter_type"])
        binding = self.db.upsert_binding(
            key_record["id"],
            {
                "adapter_type": protocol["adapter_type"],
                "label": protocol["label"],
                "status": status,
                "supported": 1 if supported else 0,
                "model_count": len(list_models),
                "probe_model": probe_model,
                "response_shape": response_shape,
                "preview": preview,
                "last_network_mode": probe_result.network_mode if probe_result else adapter.network["effective_mode"],
                "last_network_route": probe_result.network_route if probe_result else "",
                "last_proxy_url_masked": probe_result.proxy_url_masked if probe_result else adapter.network["proxy_url_masked"],
                "last_error": last_error,
                "detected_at": utcnow(),
                "last_discovered_at": utcnow() if list_models else "",
                "last_checked_at": existing["last_checked_at"] if existing else "",
            },
        )
        self.db.replace_binding_models(binding["id"], list_models, source)
        return {
            "key_id": key_record["id"],
            "key_name": key_record["name"],
            "binding_id": binding["id"],
            "adapter_type": protocol["adapter_type"],
            "label": protocol["label"],
            "supported": supported,
            "status": status,
            "model_count": len(list_models),
            "probe_model": probe_model,
            "network_mode": probe_result.network_mode if probe_result else adapter.network["effective_mode"],
            "network_route": probe_result.network_route if probe_result else "",
            "proxy_url_masked": probe_result.proxy_url_masked if probe_result else adapter.network["proxy_url_masked"],
            "response_shape": response_shape,
            "preview": preview,
            "error": last_error,
        }

    def detect_protocols(self, key_id: int | None = None, progress: JobProgress | None = None) -> list[dict[str, Any]]:
        keys = [self.db.get_key_record(key_id)] if key_id else self.db.enabled_keys()
        results = []
        for key_record in keys:
            station_id = key_record["station_id"]
            throttle = self.get_throttle(station_id)

            def throttled_detect(key_rec: sqlite3.Row, proto: dict[str, Any]) -> dict[str, Any]:
                if throttle.in_cooldown():
                    return {
                        "key_id": key_rec["id"],
                        "key_name": key_rec["name"],
                        "binding_id": None,
                        "adapter_type": proto["adapter_type"],
                        "label": proto["label"],
                        "supported": False,
                        "status": "rate_limited",
                        "model_count": 0,
                        "probe_model": "",
                        "network_mode": "",
                        "network_route": "",
                        "proxy_url_masked": "",
                        "response_shape": "",
                        "preview": "",
                        "error": "station in cooldown",
                    }
                throttle.acquire()
                try:
                    result = self._detect_protocol_for_key(key_rec, proto)
                    if result.get("status") == "rate_limited" or is_rate_limit_error(None, result.get("error")):
                        throttle.enter_cooldown()
                    return result
                finally:
                    throttle.release()

            max_workers = min(throttle._max_concurrency, len(PROTOCOLS))
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
                future_map = {}
                for index, protocol in enumerate(PROTOCOLS):
                    if progress:
                        progress.step(current_step=f"探测 {key_record['name']} · {protocol['label']}", increment=0)
                    future = executor.submit(throttled_detect, key_record, protocol)
                    future_map[future] = index

                ordered_results: list[dict[str, Any] | None] = [None] * len(PROTOCOLS)
                for future in concurrent.futures.as_completed(future_map):
                    index = future_map[future]
                    ordered_results[index] = future.result()
                    if progress:
                        progress.step(increment=1)
                results.extend([item for item in ordered_results if item is not None])
        return results

    def rediscover_binding(self, binding_id: int) -> dict[str, Any]:
        binding = self.db.get_binding_record(binding_id)
        adapter = self.build_adapter(binding["adapter_type"], binding)
        seed_models = parse_seed_models(binding["seed_models"])
        try:
            models = merge_model_lists(adapter.list_models(), seed_models)
            self.db.replace_binding_models(binding_id, models, "adapter_list")
            updated = self.db.upsert_binding(
                binding["key_id"],
                {
                    "adapter_type": binding["adapter_type"],
                    "label": binding["label"],
                    "status": "ok" if binding["supported"] else "listed",
                    "supported": binding["supported"],
                    "model_count": len(models),
                    "probe_model": binding["probe_model"],
                    "response_shape": binding["response_shape"],
                    "preview": binding["preview"],
                    "last_network_mode": binding["last_network_mode"] or "",
                    "last_network_route": binding["last_network_route"] or "",
                    "last_proxy_url_masked": binding["last_proxy_url_masked"] or "",
                    "last_error": "",
                    "detected_at": binding["detected_at"],
                    "last_discovered_at": utcnow(),
                    "last_checked_at": binding["last_checked_at"],
                },
            )
            return {"binding_id": binding_id, "status": "ok", "model_count": len(models), "models": models, "binding": updated}
        except Exception as exc:
            if seed_models:
                self.db.replace_binding_models(binding_id, seed_models, "seed_models")
                updated = self.db.upsert_binding(
                    binding["key_id"],
                    {
                        "adapter_type": binding["adapter_type"],
                        "label": binding["label"],
                        "status": "listed" if binding["supported"] else binding["status"],
                        "supported": binding["supported"],
                        "model_count": len(seed_models),
                        "probe_model": binding["probe_model"],
                        "response_shape": binding["response_shape"],
                        "preview": binding["preview"],
                        "last_network_mode": binding["last_network_mode"] or "",
                        "last_network_route": binding["last_network_route"] or "",
                        "last_proxy_url_masked": binding["last_proxy_url_masked"] or "",
                        "last_error": str(exc),
                        "detected_at": binding["detected_at"],
                        "last_discovered_at": utcnow(),
                        "last_checked_at": binding["last_checked_at"],
                    },
                )
                return {
                    "binding_id": binding_id,
                    "status": "seed_only",
                    "error": str(exc),
                    "model_count": len(seed_models),
                    "models": seed_models,
                    "binding": updated,
                }
            self.db.upsert_binding(
                binding["key_id"],
                {
                    "adapter_type": binding["adapter_type"],
                    "label": binding["label"],
                    "status": binding["status"],
                    "supported": binding["supported"],
                    "model_count": binding["model_count"],
                    "probe_model": binding["probe_model"],
                    "response_shape": binding["response_shape"],
                    "preview": binding["preview"],
                    "last_network_mode": binding["last_network_mode"] or "",
                    "last_network_route": binding["last_network_route"] or "",
                    "last_proxy_url_masked": binding["last_proxy_url_masked"] or "",
                    "last_error": str(exc),
                    "detected_at": binding["detected_at"],
                    "last_discovered_at": binding["last_discovered_at"],
                    "last_checked_at": binding["last_checked_at"],
                },
            )
            return {"binding_id": binding_id, "status": "error", "error": str(exc)}

    def check_binding(
        self,
        binding_id: int,
        model_id: str | None = None,
        progress: JobProgress | None = None,
    ) -> list[dict[str, Any]]:
        binding = self.db.get_binding_record(binding_id)
        adapter = self.build_adapter(binding["adapter_type"], binding)
        station_id = binding["station_id"]
        throttle = self.get_throttle(station_id)
        models = [model_id] if model_id else self.db.list_models_for_binding(binding_id)
        if not models and binding["probe_model"]:
            models = [binding["probe_model"]]
        results: list[dict[str, Any] | None] = [None] * len(models)

        def run_single(index: int, current_model: str) -> tuple[int, dict[str, Any]]:
            if throttle.in_cooldown():
                rl_result = CheckResult("rate_limited", False, 0, "", "", "station in cooldown")
                self.db.upsert_binding_check(binding_id, current_model, rl_result)
                return index, {
                    "binding_id": binding_id,
                    "model_id": current_model,
                    "status": "rate_limited",
                    "available": False,
                    "latency_ms": 0,
                    "response_shape": "",
                    "preview": "",
                    "network_mode": "",
                    "network_route": "",
                    "proxy_url_masked": "",
                    "error": "station in cooldown",
                }
            throttle.acquire()
            try:
                result = test_model_with_retries(adapter, current_model, CHECK_RETRY_ATTEMPTS)
                if result.status == "rate_limited":
                    throttle.enter_cooldown()
                self.db.upsert_binding_check(binding_id, current_model, result)
                return index, {
                    "binding_id": binding_id,
                    "model_id": current_model,
                    "status": result.status,
                    "available": result.available,
                    "latency_ms": result.latency_ms,
                    "response_shape": result.response_shape,
                    "preview": result.preview,
                    "network_mode": result.network_mode,
                    "network_route": result.network_route,
                    "proxy_url_masked": result.proxy_url_masked,
                    "error": result.error,
                }
            finally:
                throttle.release()

        max_workers = min(throttle._max_concurrency, max(1, len(models)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
            future_map = {}
            for index, current_model in enumerate(models):
                if progress:
                    progress.step(current_step=f"检查 {binding['key_name']} · {binding['label']} · {current_model}", increment=0)
                future = executor.submit(run_single, index, current_model)
                future_map[future] = current_model

            for future in concurrent.futures.as_completed(future_map):
                index, payload = future.result()
                results[index] = payload
                if progress:
                    progress.step(increment=1)
        self.refresh_binding_state(binding_id)
        return [item for item in results if item is not None]

    def binding_check_target_count(self, binding_id: int) -> int:
        binding = self.db.get_binding_record(binding_id)
        models = self.db.list_models_for_binding(binding_id)
        if models:
            return len(models)
        return 1 if binding["probe_model"] else 0

    def refresh_binding_state(self, binding_id: int) -> dict[str, Any]:
        binding = self.db.get_binding_record(binding_id)
        checks = self.db.list_current_binding_checks(binding_id)
        supported = bool(binding["supported"])
        available = False
        response_shape = binding["response_shape"]
        preview = binding["preview"]
        last_error = binding["last_error"]
        last_checked_at = binding["last_checked_at"]
        last_network_mode = binding["last_network_mode"] or ""
        last_network_route = binding["last_network_route"] or ""
        last_proxy_url_masked = binding["last_proxy_url_masked"] or ""
        successful_row: dict[str, Any] | None = None
        supported_row: dict[str, Any] | None = None
        rate_limited_count = 0
        for row in checks:
            result = CheckResult(
                row["status"],
                bool(row["available"]),
                int(row["latency_ms"] or 0),
                row["response_shape"] or "",
                row["preview"] or "",
                row["error"] or "",
                row.get("network_mode", "") or "",
                row.get("network_route", "") or "",
                row.get("proxy_url_masked", "") or "",
            )
            if result.status == "rate_limited":
                rate_limited_count += 1
                continue
            if self.result_indicates_protocol_support(binding["adapter_type"], result):
                supported = True
                if supported_row is None:
                    supported_row = row
            if result.available:
                available = True
                if successful_row is None:
                    successful_row = row
            if row.get("response_shape"):
                response_shape = row["response_shape"]
            if row.get("preview"):
                preview = row["preview"]
            if row.get("error"):
                last_error = row["error"]
            if row.get("checked_at"):
                last_checked_at = row["checked_at"]
            if row.get("network_mode"):
                last_network_mode = row["network_mode"]
            if row.get("network_route"):
                last_network_route = row["network_route"]
            if row.get("proxy_url_masked"):
                last_proxy_url_masked = row["proxy_url_masked"]
        status = "unsupported"
        if available:
            status = "ok"
        elif supported:
            status = "supported"
        elif checks and rate_limited_count == len(checks):
            status = "rate_limited"
        elif checks:
            status = "unsupported"

        if successful_row:
            response_shape = successful_row["response_shape"] or response_shape
            preview = successful_row["preview"] or preview
            last_error = ""
            last_checked_at = successful_row["checked_at"] or last_checked_at
            last_network_mode = successful_row["network_mode"] or last_network_mode
            last_network_route = successful_row["network_route"] or last_network_route
            last_proxy_url_masked = successful_row["proxy_url_masked"] or ""
        elif supported_row:
            response_shape = supported_row["response_shape"] or response_shape
            preview = supported_row["preview"] or preview
            last_checked_at = supported_row["checked_at"] or last_checked_at
            last_network_mode = supported_row["network_mode"] or last_network_mode
            last_network_route = supported_row["network_route"] or last_network_route
            last_proxy_url_masked = supported_row["proxy_url_masked"] or ""

        self.db.upsert_binding(
            binding["key_id"],
            {
                "adapter_type": binding["adapter_type"],
                "label": binding["label"],
                "status": status,
                "supported": 1 if supported else 0,
                "model_count": binding["model_count"],
                "probe_model": binding["probe_model"],
                "response_shape": response_shape,
                "preview": preview,
                "last_network_mode": last_network_mode,
                "last_network_route": last_network_route,
                "last_proxy_url_masked": last_proxy_url_masked,
                "last_error": last_error,
                "detected_at": binding["detected_at"],
                "last_discovered_at": binding["last_discovered_at"],
                "last_checked_at": last_checked_at,
            },
        )
        return self.db.get_binding(binding_id)

    @staticmethod
    def result_indicates_protocol_support(adapter_type: str, result: CheckResult) -> bool:
        return protocol_supported(adapter_type, [], result, result.error or "")

    def run_full_cycle(self, trigger: str = "manual", progress: JobProgress | None = None) -> dict[str, Any]:
        if not self._run_lock.acquire(blocking=False):
            raise RelayError("a cycle is already running")
        started_at = utcnow()
        self.db.update_scheduler_settings(
            {
                "last_cycle_started_at": started_at,
                "last_cycle_status": "running",
                "last_cycle_note": trigger,
            }
        )
        try:
            if progress:
                total_bindings = len(self.db.enabled_keys()) * len(PROTOCOLS)
                progress.set_total(total_bindings)
            detection = self.detect_protocols(progress=progress)
            checked = []
            bindings = [binding for binding in self.db.list_bindings() if binding["supported"]]
            if progress:
                progress.add_total(sum(self.binding_check_target_count(binding["id"]) for binding in bindings))
            skipped_cooldown = 0
            for binding in bindings:
                if binding["supported"]:
                    station_id = binding.get("station_id")
                    if station_id:
                        throttle = self.get_throttle(station_id)
                        if throttle.in_cooldown():
                            skipped_cooldown += 1
                            target_count = self.binding_check_target_count(binding["id"])
                            if progress:
                                progress.step(
                                    current_step=f"跳过 {binding.get('station_name', '')} (限流冷却中)",
                                    increment=target_count,
                                )
                            continue
                    checked.extend(self.check_binding(binding["id"], progress=progress))
            self.db.update_scheduler_settings(
                {
                    "last_cycle_finished_at": utcnow(),
                    "last_cycle_status": "ok",
                    "last_cycle_note": f"{trigger}: detected {len(detection)} protocol rows, checked {len(checked)} models",
                }
            )
            return {"trigger": trigger, "started_at": started_at, "detection": detection, "checked": checked}
        except Exception as exc:
            self.db.update_scheduler_settings(
                {
                    "last_cycle_finished_at": utcnow(),
                    "last_cycle_status": "error",
                    "last_cycle_note": f"{trigger}: {exc}",
                }
            )
            raise
        finally:
            self._run_lock.release()

    def audit_key(
        self,
        key_id: int,
        force_all_bindings: bool = False,
        progress: JobProgress | None = None,
    ) -> dict[str, Any]:
        if progress:
            progress.set_total(len(PROTOCOLS))
        detection = self.detect_protocols(key_id, progress=progress)
        checked: list[dict[str, Any]] = []
        bindings = [binding for binding in self.db.list_bindings(key_id) if force_all_bindings or binding["supported"]]
        if progress:
            progress.add_total(sum(self.binding_check_target_count(binding["id"]) for binding in bindings))
        for binding in bindings:
            if force_all_bindings or binding["supported"]:
                checked.extend(self.check_binding(binding["id"], progress=progress))
        return {
            "key_id": key_id,
            "force_all_bindings": force_all_bindings,
            "detection": detection,
            "checked": checked,
            "key": self.db.get_key(key_id),
        }


class JobProgress:
    def __init__(self, db: Database, job_id: int):
        self.db = db
        self.job_id = job_id
        self._lock = threading.Lock()

    def start(self, total_steps: int = 0, current_step: str = "") -> None:
        job = self.db.get_job(self.job_id)
        self.db.update_job(
            self.job_id,
            {
                "status": "running",
                "started_at": utcnow(),
                "total_steps": max(0, int(total_steps or job["total_steps"] or 0)),
                "completed_steps": 0,
                "current_step": current_step,
                "detail": current_step,
                "error_text": "",
            },
        )

    def set_total(self, total_steps: int) -> None:
        with self._lock:
            job = self.db.get_job(self.job_id)
            self.db.update_job(self.job_id, {"total_steps": max(0, int(total_steps)), "completed_steps": job["completed_steps"]})

    def add_total(self, extra_steps: int) -> None:
        with self._lock:
            job = self.db.get_job(self.job_id)
            self.db.update_job(self.job_id, {"total_steps": max(0, int(job["total_steps"]) + int(extra_steps or 0))})

    def step(self, current_step: str = "", increment: int = 1) -> None:
        with self._lock:
            job = self.db.get_job(self.job_id)
            self.db.update_job(
                self.job_id,
                {
                    "completed_steps": int(job["completed_steps"]) + max(0, int(increment)),
                    "current_step": current_step or job.get("current_step", ""),
                    "detail": current_step or job.get("detail", ""),
                },
            )

    def complete(self, result: dict[str, Any]) -> dict[str, Any]:
        job = self.db.get_job(self.job_id)
        return self.db.update_job(
            self.job_id,
            {
                "status": "ok",
                "completed_steps": max(int(job["completed_steps"]), int(job["total_steps"])),
                "current_step": "",
                "detail": "已完成",
                "result_json": json.dumps(result, ensure_ascii=False),
                "finished_at": utcnow(),
                "error_text": "",
            },
        )

    def fail(self, error_text: str) -> dict[str, Any]:
        return self.db.update_job(
            self.job_id,
            {
                "status": "error",
                "current_step": "",
                "detail": "执行失败",
                "error_text": error_text,
                "finished_at": utcnow(),
            },
        )


class JobManager:
    def __init__(self, app: RelayManagerApp):
        self.app = app
        self._lock = threading.Lock()
        self._threads: dict[int, threading.Thread] = {}

    def start(
        self,
        *,
        job_type: str,
        title: str,
        runner: Any,
        scope_type: str = "",
        scope_id: int | None = None,
        trigger: str = "manual",
        total_steps: int = 0,
    ) -> dict[str, Any]:
        existing = self.app.db.find_active_job(job_type, scope_type, scope_id)
        if existing:
            return existing
        job = self.app.db.create_job(
            {
                "job_type": job_type,
                "status": "queued",
                "scope_type": scope_type,
                "scope_id": scope_id,
                "title": title,
                "trigger": trigger,
                "detail": "等待执行",
                "total_steps": total_steps,
                "completed_steps": 0,
                "current_step": "",
            }
        )
        thread = threading.Thread(
            target=self._run_job,
            args=(job["id"], runner),
            daemon=True,
            name=f"relay-job-{job['id']}",
        )
        with self._lock:
            self._threads[job["id"]] = thread
        thread.start()
        return self.app.db.get_job(job["id"])

    def _run_job(self, job_id: int, runner: Any) -> None:
        progress = JobProgress(self.app.db, job_id)
        progress.start()
        try:
            result = runner(progress)
            progress.complete(result if isinstance(result, dict) else {"result": result})
        except Exception as exc:
            progress.fail(str(exc))
        finally:
            with self._lock:
                self._threads.pop(job_id, None)


class Scheduler:
    def __init__(self, app: RelayManagerApp):
        self.app = app
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="llm-relay-manager-scheduler")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:
                pass
            self._stop_event.wait(5)

    def _tick(self) -> None:
        settings = self.app.db.get_scheduler_settings()
        if not settings["enabled"]:
            return
        if settings.get("last_cycle_status") == "running":
            return
        last_finished = parse_iso8601(settings.get("last_cycle_finished_at"))
        if last_finished is None or datetime.now(timezone.utc) - last_finished >= timedelta(minutes=settings["interval_minutes"]):
            self.app.run_full_cycle(trigger="scheduler")


APP = RelayManagerApp(DB_PATH)


class RelayRequestHandler(BaseHTTPRequestHandler):
    server_version = "RelayManager/0.3"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path in PAGE_ROUTES:
            self._serve_file(TEMPLATES_DIR / PAGE_ROUTES[path], "text/html; charset=utf-8")
            return
        if path.startswith("/static/"):
            file_path = STATIC_DIR / path.removeprefix("/static/")
            if file_path.is_file():
                self._serve_file(file_path, self._guess_mime(file_path))
            else:
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return

        if path == "/api/summary":
            self._send_json(APP.db.summary())
            return
        if path == "/api/stations":
            self._send_json(APP.db.list_stations())
            return
        if path == "/api/keys":
            self._send_json(APP.db.list_keys())
            return
        if path == "/api/bindings":
            query = parse_qs(parsed.query)
            key_id = (query.get("key_id") or [""])[0]
            self._send_json(APP.db.list_bindings(int(key_id)) if key_id else APP.db.list_bindings())
            return
        if path.startswith("/api/bindings/") and path.endswith("/models"):
            binding_id = self._extract_int_id(path, "/api/bindings/", "/models")
            self._send_json(APP.db.get_binding_detail(binding_id))
            return
        if path == "/api/models/search":
            query = parse_qs(parsed.query)
            q = (query.get("q") or [""])[0].strip()
            available_only = (query.get("available_only") or ["0"])[0] == "1"
            filters = {
                "station_id": (query.get("station_id") or [""])[0],
                "key_id": (query.get("key_id") or [""])[0],
                "station_name": (query.get("station_name") or [""])[0],
                "key_name": (query.get("key_name") or [""])[0],
                "protocol_label": (query.get("protocol_label") or [""])[0],
                "supported": (query.get("supported") or [""])[0],
                "status": (query.get("status") or [""])[0],
                "available": (query.get("available") or [""])[0],
                "min_latency_ms": (query.get("min_latency_ms") or [""])[0],
                "max_latency_ms": (query.get("max_latency_ms") or [""])[0],
                "min_success_rate": (query.get("min_success_rate") or [""])[0],
                "max_success_rate": (query.get("max_success_rate") or [""])[0],
                "preview": (query.get("preview") or [""])[0],
                "error": (query.get("error") or [""])[0],
            }
            sort_by = (query.get("sort_by") or ["model_id"])[0].strip() or "model_id"
            sort_dir = (query.get("sort_dir") or ["asc"])[0].strip() or "asc"
            self._send_json(APP.db.search_models(q, available_only, filters=filters, sort_by=sort_by, sort_dir=sort_dir))
            return
        if path == "/api/history":
            query = parse_qs(parsed.query)
            limit = int((query.get("limit") or ["50"])[0] or "50")
            self._send_json(APP.db.recent_history(limit))
            return
        if path == "/api/jobs":
            query = parse_qs(parsed.query)
            limit = int((query.get("limit") or ["100"])[0] or "100")
            self._send_json(APP.db.list_jobs(limit))
            return
        if path.startswith("/api/jobs/"):
            job_id = self._extract_int_id(path, "/api/jobs/")
            self._send_json(APP.db.get_job(job_id))
            return
        if path == "/api/settings/scheduler":
            self._send_json(APP.db.get_scheduler_settings())
            return

        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        payload = self._read_json_body()
        try:
            if path == "/api/stations":
                self._send_json(APP.db.create_station(self._validate_station_payload(payload)), HTTPStatus.CREATED)
                return
            if path == "/api/keys":
                key = APP.db.create_key(self._validate_key_payload(payload, require_api_key=True))
                job = APP.jobs.start(
                    job_type="force_audit_key",
                    title=f"新增后全量校验 Key #{key['id']}",
                    scope_type="key",
                    scope_id=key["id"],
                    runner=lambda progress, key_id=key["id"]: APP.audit_key(key_id, True, progress=progress),
                )
                self._send_json({"key": APP.db.get_key(key["id"]), "job": job}, HTTPStatus.CREATED)
                return
            if path == "/api/run-cycle":
                job = APP.jobs.start(
                    job_type="run_full_cycle",
                    title="执行完整巡检周期",
                    scope_type="system",
                    trigger="manual",
                    runner=lambda progress: APP.run_full_cycle("manual", progress=progress),
                )
                self._send_json(job, HTTPStatus.ACCEPTED)
                return
            if path.startswith("/api/keys/") and path.endswith("/detect"):
                key_id = self._extract_int_id(path, "/api/keys/", "/detect")
                job = APP.jobs.start(
                    job_type="detect_key",
                    title=f"探测 Key #{key_id} 协议",
                    scope_type="key",
                    scope_id=key_id,
                    total_steps=len(PROTOCOLS),
                    runner=lambda progress, current_key_id=key_id: {
                        "key_id": current_key_id,
                        "detection": APP.detect_protocols(current_key_id, progress=progress),
                    },
                )
                self._send_json(job, HTTPStatus.ACCEPTED)
                return
            if path.startswith("/api/keys/") and path.endswith("/audit"):
                key_id = self._extract_int_id(path, "/api/keys/", "/audit")
                job = APP.jobs.start(
                    job_type="audit_key",
                    title=f"校验 Key #{key_id} 已支持协议",
                    scope_type="key",
                    scope_id=key_id,
                    runner=lambda progress, current_key_id=key_id: APP.audit_key(current_key_id, False, progress=progress),
                )
                self._send_json(job, HTTPStatus.ACCEPTED)
                return
            if path.startswith("/api/keys/") and path.endswith("/force-audit"):
                key_id = self._extract_int_id(path, "/api/keys/", "/force-audit")
                job = APP.jobs.start(
                    job_type="force_audit_key",
                    title=f"强制全量校验 Key #{key_id}",
                    scope_type="key",
                    scope_id=key_id,
                    runner=lambda progress, current_key_id=key_id: APP.audit_key(current_key_id, True, progress=progress),
                )
                self._send_json(job, HTTPStatus.ACCEPTED)
                return
            if path.startswith("/api/bindings/") and path.endswith("/discover"):
                binding_id = self._extract_int_id(path, "/api/bindings/", "/discover")
                self._send_json(APP.rediscover_binding(binding_id))
                return
            if path.startswith("/api/bindings/") and path.endswith("/check"):
                binding_id = self._extract_int_id(path, "/api/bindings/", "/check")
                model_id = payload.get("model_id")
                target_model = model_id.strip() if isinstance(model_id, str) and model_id.strip() else None
                job = APP.jobs.start(
                    job_type="check_binding",
                    title=f"检查协议绑定 #{binding_id}" if not target_model else f"检查协议绑定 #{binding_id} · {target_model}",
                    scope_type="binding",
                    scope_id=binding_id,
                    total_steps=1 if target_model else APP.binding_check_target_count(binding_id),
                    runner=lambda progress, current_binding_id=binding_id, current_model=target_model: {
                        "binding_id": current_binding_id,
                        "checked": APP.check_binding(current_binding_id, current_model, progress=progress),
                    },
                )
                self._send_json(job, HTTPStatus.ACCEPTED)
                return
        except KeyError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            return
        except RelayError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        payload = self._read_json_body()
        try:
            if path.startswith("/api/stations/"):
                station_id = self._extract_int_id(path, "/api/stations/")
                result = APP.db.update_station(station_id, self._validate_station_payload(payload))
                APP.invalidate_throttle(station_id)
                self._send_json(result)
                return
            if path.startswith("/api/keys/"):
                key_id = self._extract_int_id(path, "/api/keys/")
                self._send_json(APP.db.update_key(key_id, self._validate_key_payload(payload, require_api_key=False)))
                return
            if path == "/api/settings/scheduler":
                self._send_json(APP.db.update_scheduler_settings(payload))
                return
        except KeyError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            return
        except RelayError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path.startswith("/api/stations/"):
                station_id = self._extract_int_id(path, "/api/stations/")
                APP.db.delete_station(station_id)
                self._send_json({"ok": True})
                return
            if path.startswith("/api/keys/"):
                key_id = self._extract_int_id(path, "/api/keys/")
                APP.db.delete_key(key_id)
                self._send_json({"ok": True})
                return
        except KeyError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            return
        except RelayError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RelayError("invalid JSON body") from exc

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json_dumps(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _serve_file(self, path: Path, content_type: str) -> None:
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _guess_mime(path: Path) -> str:
        if path.suffix == ".js":
            return "application/javascript; charset=utf-8"
        if path.suffix == ".css":
            return "text/css; charset=utf-8"
        return "application/octet-stream"

    @staticmethod
    def _extract_int_id(path: str, prefix: str, suffix: str = "") -> int:
        raw = path.removeprefix(prefix)
        if suffix:
            raw = raw.removesuffix(suffix)
        raw = raw.strip("/")
        if not raw.isdigit():
            raise RelayError("invalid id")
        return int(raw)

    @staticmethod
    def _validate_station_payload(payload: dict[str, Any]) -> dict[str, Any]:
        if not str(payload.get("name", "")).strip():
            raise RelayError("station name is required")
        if not str(payload.get("base_url", "")).strip().startswith(("http://", "https://")):
            raise RelayError("base_url must start with http:// or https://")
        network_mode = normalize_network_mode(payload.get("network_mode"))
        proxy_url = str(payload.get("proxy_url", "")).strip()
        if network_mode == "proxy" and not proxy_url:
            raise RelayError("proxy_url is required when station network_mode=proxy")
        if proxy_url and not proxy_url.startswith(("http://", "https://", "socks5://", "socks5h://")):
            raise RelayError("proxy_url must start with http://, https://, socks5:// or socks5h://")
        concurrency = int(payload.get("detect_max_concurrency", 2))
        if not 1 <= concurrency <= 10:
            raise RelayError("detect_max_concurrency must be between 1 and 10")
        interval = int(payload.get("detect_min_interval_ms", 800))
        if not 0 <= interval <= 30000:
            raise RelayError("detect_min_interval_ms must be between 0 and 30000")
        cooldown = int(payload.get("detect_cooldown_seconds", 60))
        if not 0 <= cooldown <= 600:
            raise RelayError("detect_cooldown_seconds must be between 0 and 600")
        return payload

    @staticmethod
    def _validate_key_payload(payload: dict[str, Any], require_api_key: bool) -> dict[str, Any]:
        if not payload.get("station_id"):
            raise RelayError("station_id is required")
        if not str(payload.get("name", "")).strip():
            raise RelayError("key name is required")
        if require_api_key and not str(payload.get("api_key", "")).strip():
            raise RelayError("api_key is required")
        proxy_url = str(payload.get("proxy_url", "")).strip()
        if proxy_url and not proxy_url.startswith(("http://", "https://", "socks5://", "socks5h://")):
            raise RelayError("proxy_url must start with http://, https://, socks5:// or socks5h://")
        return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LLM Relay Manager")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    APP.scheduler.start()
    server = ThreadingHTTPServer((args.host, args.port), RelayRequestHandler)
    print(f"LLM Relay Manager running on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        APP.scheduler.stop()
        server.server_close()


if __name__ == "__main__":
    main()
