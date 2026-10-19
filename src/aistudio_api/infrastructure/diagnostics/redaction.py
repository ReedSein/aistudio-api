"""Remove authentication material without removing conversation protocol fields."""

from __future__ import annotations

import json
import re
import threading
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

REDACTED = "[REDACTED]"
_CREDENTIAL_FIELDS = frozenset(
    {
        "authorization",
        "proxyauthorization",
        "cookie",
        "cookies",
        "cookiestring",
        "setcookie",
        "apikey",
        "apikeys",
        "xapikey",
        "xgoogapikey",
        "accesstoken",
        "refreshtoken",
        "idtoken",
        "token",
        "secret",
        "password",
        "webpassword",
        "adminpassword",
        "clientsecret",
        "auth",
        "sapisid",
        "snapshot",
        "botguard",
        "botguardtoken",
        "aistudiowebpassword",
        "aistudioadminpassword",
        "aistudioapikey",
        "aistudioapikeys",
    }
)
_FIELD_PATTERN = (
    r"authorization|proxy[-_]?authorization|set-cookie|cookie(?:s|_string)?|"
    r"(?:x[-_](?:goog[-_])?)?api[-_]?keys?|access[-_]?token|refresh[-_]?token|"
    r"id[-_]?token|client[-_]?secret|password|secret|sapisid|"
    r"AISTUDIO_(?:WEB_PASSWORD|ADMIN_PASSWORD|API_KEYS?)"
)
_JSON_SECRET = re.compile(
    r'(?i)("(?:' + _FIELD_PATTERN + r'|token|snapshot)"\s*:\s*)"(?:\\.|[^"\\])*"'
)
_LINE_SECRET = re.compile(
    r"(?im)(\b(?:" + _FIELD_PATTERN + r")\s*[:=]\s*)(?!\[REDACTED\])[^\r\n]+"
)
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:key|api[_-]?key|access_token|token|auth|password)=)[^&\s\"'<>]+"
)
_URL_PASSWORD = re.compile(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@")
_PATH_KEY = re.compile(r"(?i)(/api-keys/)[^/?\s]+")
_TOKEN = re.compile(r"\b(?:ya29\.[A-Za-z0-9_.-]+|SAPISIDHASH\s+[A-Za-z0-9_ -]+)")
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_secrets: dict[str, None] = {}
_secret_lock = threading.RLock()


def sensitive_field(name: str) -> bool:
    return re.sub(r"[^a-z0-9]", "", name.lower()) in _CREDENTIAL_FIELDS


def register_secret(value: object) -> None:
    """Remember only in memory, including encodings that may occur in error URLs."""
    if isinstance(value, str) and 8 <= len(value) <= 16384 and value != REDACTED:
        with _secret_lock:
            _secrets[value] = None
            _secrets[quote(value, safe="")] = None
            while len(_secrets) > 512:
                del _secrets[next(iter(_secrets))]


def redact_text(text: str) -> str:
    text = _ANSI.sub("", text).replace("\x00", "\\0")
    with _secret_lock:
        for secret in sorted(_secrets, key=len, reverse=True):
            text = text.replace(secret, REDACTED)
    text = _URL_PASSWORD.sub(r"\1[REDACTED]@", text)
    text = _PATH_KEY.sub(r"\1[REDACTED]", text)
    text = _QUERY_SECRET.sub(r"\1[REDACTED]", text)
    text = _JSON_SECRET.sub(lambda match: match[1] + '"[REDACTED]"', text)
    text = _LINE_SECRET.sub(r"\1[REDACTED]", text)
    return _TOKEN.sub(REDACTED, text)


def redact_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        query = [
            (
                name,
                REDACTED
                if sensitive_field(name) or name.lower() == "key"
                else redact_text(value),
            )
            for name, value in parse_qsl(parts.query, keep_blank_values=True)
        ]
        host = parts.netloc.rsplit("@", 1)[-1]
        return redact_text(
            urlunsplit((parts.scheme, host, parts.path, urlencode(query), ""))
        )
    except ValueError:
        return "[invalid URL omitted]"


def sanitize(value: object, *, credential_keys: bool = False) -> object:
    """Sanitize structures before serialization, including embedded legacy JSON."""
    if isinstance(value, dict):
        cleaned: dict[str, object] = {}
        for key, item in value.items():
            name = str(key)
            if sensitive_field(name) or (credential_keys and name.lower() == "key"):
                if isinstance(item, str):
                    register_secret(item)
                cleaned[name] = REDACTED
            else:
                cleaned[name] = sanitize(
                    item,
                    credential_keys=name.lower()
                    in ("query", "query_params", "safe_query_params"),
                )
        return cleaned
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        if value.lstrip().startswith(("{", "[")):
            try:
                parsed: object = json.loads(value)
            except ValueError:
                pass
            else:
                if isinstance(parsed, (dict, list)):
                    return json.dumps(sanitize(parsed), ensure_ascii=False)
        return redact_text(value)
    return value


def sanitized_json(value: object) -> str:
    return json.dumps(sanitize(value), ensure_ascii=False, separators=(",", ":"))
