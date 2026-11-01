"""Bounded, conservative HTML heuristics. No scripts, XML entities or remote resources."""

import codecs
import re
from dataclasses import dataclass
from html.parser import HTMLParser

from .errors import ErrorCode
from .http_client import HttpAttempt
from .models import ContentState


@dataclass(frozen=True)
class ContentAssessment:
    state: ContentState
    basis: tuple[str, ...]
    confidence: float
    text: str | None
    declared_encoding: str | None
    applied_encoding: str | None
    replacements: int
    sniffed: bool
    diagnostic_allowed: bool
    reasons: tuple[ErrorCode, ...]


class _HTMLFacts(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.in_title = False
        self.title: list[str] = []
        self.visible: list[str] = []
        self.scripts = 0
        self.app_root = False
        self.captcha_widget = False
        self.password = False
        self.tags = 0
        self.media = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags += 1
        values = dict(attrs)
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag == "script":
            self.scripts += 1
        if tag == "title":
            self.in_title = True
        if values.get("id") in {"root", "app", "__next", "__nuxt"}:
            self.app_root = True
        if tag in {"img", "video", "canvas", "svg"}:
            self.media += 1
        if tag == "input" and (values.get("type") or "").lower() == "password":
            self.password = True
        classes = (values.get("class") or "").split()
        if (
            any(c in {"g-recaptcha", "h-captcha", "cf-turnstile"} for c in classes)
            and "data-sitekey" in values
        ):
            self.captcha_widget = True
        if tag == "script" and "/cdn-cgi/challenge-platform/" in (values.get("src") or ""):
            self.captcha_widget = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if tag == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title.append(data)
        if not self.hidden:
            self.visible.append(data)


def _encoding(data: bytes, header: str, head: bytes) -> tuple[str | None, str, bool]:
    match = re.search(r"charset\s*=\s*[\"']?([A-Za-z0-9._-]{1,50})", header, re.I)
    if not match:
        meta_match = re.search(
            rb"<meta\b[^>]{0,500}charset\s*=\s*[\"']?([A-Za-z0-9._-]{1,50})", head, re.I
        )
        declared = meta_match[1].decode("ascii") if meta_match else None
    else:
        declared = match[1]
    if data.startswith(codecs.BOM_UTF32_LE):
        return declared, "utf-32-le", False
    if data.startswith(codecs.BOM_UTF32_BE):
        return declared, "utf-32-be", False
    if data.startswith(codecs.BOM_UTF16_LE):
        return declared, "utf-16-le", False
    if data.startswith(codecs.BOM_UTF16_BE):
        return declared, "utf-16-be", False
    if data.startswith(codecs.BOM_UTF8):
        return declared, "utf-8", False
    applied = (declared or "utf-8").lower()
    try:
        canonical = codecs.lookup(applied).name
        if not re.fullmatch(
            r"utf-(?:8|16-le|16-be|32-le|32-be)|ascii|iso8859-\d+|cp\d+|koi8-[ru]|shift_jis|euc_jp|gb18030|big5",
            canonical,
        ):
            raise LookupError("not a supported text codec")
        return declared, canonical, False
    except LookupError:
        return declared, "utf-8", True


def assess_response(attempt: HttpAttempt, *, sniff_bytes: int = 4096) -> ContentAssessment:
    if not attempt.body_complete:
        code = attempt.status_code
        state: ContentState = "not_received" if code is None else "incomplete"
        if attempt.error and attempt.error.code == "CONTENT_TOO_LARGE":
            state = "too_large"
        if code in {401, 403}:
            state = "http_denied"
        elif code in {404, 410}:
            state = "http_missing"
        elif code is not None and (code in {408, 429} or code >= 500):
            state = "http_unavailable"
        return ContentAssessment(
            state,
            ("Response was not completely received; no HTML asserted",),
            1.0,
            None,
            None,
            None,
            0,
            False,
            False,
            (),
        )
    data = attempt.decoded_body
    headers = [h.value for h in attempt.response_headers if h.name == "content-type"]
    header = headers[0] if len(headers) == 1 else ""
    media = header.partition(";")[0].strip().lower()
    head = data[:sniff_bytes]
    declared, applied, unknown_codec = _encoding(data, header, head)
    bom_text = applied.startswith(("utf-16", "utf-32"))
    binary = head.startswith(
        (b"%PDF-", b"PK\x03\x04", b"\x89PNG", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")
    )
    binary |= not bom_text and (
        b"\0" in head or sum(b < 9 or 13 < b < 32 for b in head) > len(head) * 0.02
    )
    sniff_text = (
        head.decode(applied, errors="replace").lstrip("\ufeff \r\n\t") if not binary else ""
    )
    looks_html = bool(
        re.search(
            r"^(?:<!--.*?-->\s*)*(?:<!doctype\s+html\b|<(?:html|head|body|title|div|p|main|article|form|script)\b)",
            sniff_text,
            re.I | re.S,
        )
    )
    declared_html = media in {"text/html", "application/xhtml+xml"}
    is_html = not binary and (
        looks_html or declared_html and bool(re.search(r"<[a-z][^>]*>", sniff_text, re.I))
    )
    sniffed = is_html and not declared_html
    text = data.decode(applied, errors="replace").removeprefix("\ufeff") if is_html else None
    literal = "\ufffd".encode(applied, errors="ignore")
    replacements = max(0, (text or "").count("\ufffd") - (data.count(literal) if literal else 0))
    reasons: list[ErrorCode] = ["CONTENT_DECODE_ERROR"] if replacements or unknown_codec else []
    basis = ["HTML structure observed" if is_html else "No supported HTML structure"]
    if sniffed:
        basis.append("HTML sniffed within the first 4 KiB; media type missing or contradictory")
    if replacements or unknown_codec:
        basis.append(
            "Text decoding required replacement or an unsupported declared charset fallback"
        )
    state = "html" if is_html else "non_html"
    confidence, diagnostic = 0.95 if declared_html else 0.85, False
    if not data or not data.strip():
        state, confidence = "empty", 1.0
        reasons.append("CONTENT_EMPTY")
    elif binary or not is_html:
        reasons.append("CONTENT_NON_HTML")
    elif text is not None:
        facts = _HTMLFacts()
        facts.feed(text)
        facts.close()
        visible = " ".join(" ".join(facts.visible).split()).lower()
        title = " ".join(facts.title).lower()
        challenge_phrase = any(
            p in visible
            for p in (
                "verify you are human",
                "checking your browser",
                "enable javascript and cookies",
                "подтвердите, что вы человек",
            )
        )
        login_phrase = any(p in title for p in ("sign in", "log in", "login", "вход"))
        challenge_title = any(
            p in title
            for p in (
                "just a moment",
                "verify you are human",
                "checking your browser",
                "security check",
                "подтвердите",
                "проверка браузера",
            )
        )
        if facts.captcha_widget and challenge_phrase and challenge_title and len(visible) < 500:
            state, confidence = "access_barrier", 0.98
            reasons.append("ACCESS_CHALLENGE")
            basis.append(
                "Challenge title, widget and human-verification instructions dominate the document"
            )
        elif facts.password and login_phrase and len(visible) < 500:
            state, confidence = "access_barrier", 0.95
            reasons.append("ACCESS_LOGIN")
            basis.append("Password field plus login title in a dedicated short login document")
        elif (
            any(
                p in title
                for p in (
                    "not found",
                    "404",
                    "access denied",
                    "just a moment",
                    "страница не найдена",
                )
            )
            or len(visible) < 300
            and any(
                p in visible
                for p in (
                    "under construction",
                    "domain is for sale",
                    "coming soon",
                    "сайт в разработке",
                    "домен продается",
                )
            )
        ):
            state, confidence, diagnostic = "suspicious_html", 0.7, True
            reasons.append("CONTENT_SUSPICIOUS")
            basis.append(
                "Possible placeholder/soft-404/access page; heuristic, not a confirmed barrier"
            )
        elif facts.app_root and facts.scripts and len(visible) < 120:
            state, confidence = "js_candidate", 0.8
            reasons.append("CONTENT_JS_CANDIDATE")
            basis.append(
                "Application root plus script and little initial text; requires later JS diagnosis"
            )
        elif not visible and not facts.scripts and not facts.media:
            state, confidence = "empty", 0.9
            reasons.append("CONTENT_EMPTY")
            basis.append("HTML shell has no visible text or media")
        else:
            basis.append("Short text alone is not an error or evidence of JS failure")
    code = attempt.status_code
    if code is None:
        state = "not_received"
    elif code in {401, 403}:
        state = "http_denied"
    elif code in {404, 410}:
        state = "http_missing"
    elif code in {408, 429} or code >= 500:
        state = "http_unavailable"
    elif not 200 <= code < 300 or code == 206:
        state = "http_other"
    elif attempt.error:
        state = "too_large" if attempt.error.code == "CONTENT_TOO_LARGE" else "incomplete"
    elif not attempt.body_complete:
        state = "incomplete"
    return ContentAssessment(
        state,
        tuple(basis),
        confidence,
        text,
        declared,
        applied if text is not None else None,
        replacements,
        sniffed,
        diagnostic,
        tuple(dict.fromkeys(reasons)),
    )
