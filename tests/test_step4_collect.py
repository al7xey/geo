"""Офлайн-тесты шага 4 на локальном фейковом API. Настоящие API и ключи не нужны."""

import asyncio
import gzip
import hashlib
import itertools
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import step4_collect as S  # noqa: E402

DOC = {
    "domain": "romashka.ru",
    "language": "русский",
    "country": "Россия",
    "name_variants": ["Ромашка"],
    "prompts": [
        {"id": "q01", "level": 1, "text": "Где заказать цветы в Москве"},
        {"id": "q31", "level": 4, "text": "Сравни службы доставки цветов в Москве"},
    ],
}
OPENAI = {
    "output": [
        {"type": "web_search_call", "action": {"sources": [{"url": "https://a.ru/"}]}},
        {"type": "message", "content": [{"type": "output_text", "text": "Ромашка и другие",
                                          "annotations": [{"url": "https://b.ru/"}]}]},
    ],
    "usage": {"input_tokens": 1000, "output_tokens": 100},
}  # fmt: skip
GEMINI = {  # Vertex AI generateContent с googleSearch
    "candidates": [{
        "content": {"parts": [{"text": "думаю", "thought": True}, {"text": "Флора"}]},
        "groundingMetadata": {
            "webSearchQueries": ["цветы", "доставка"],
            "groundingChunks": [{"web": {"uri": "https://vertexaisearch.cloud.google.com/r/1",
                                         "title": "c.ru"}}],
        },
    }],
    "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 40,
                      "thoughtsTokenCount": 10},
}  # fmt: skip
PERPLEXITY = {
    "output": [
        {"type": "search_results", "queries": ["цветы"], "results": [{"url": "https://e.ru/"}]},
        {"type": "message", "content": [{"type": "output_text", "text": "Ромашка"}]},
    ],
    "usage": {"input_tokens": 3000, "output_tokens": 500,
              "tool_calls_details": {"search_web": {"invocation": 2}}},
}  # fmt: skip


def deepseek_chat(tool_call):
    msg = {"role": "assistant", "content": "" if tool_call else "Ромашка лучше"}
    if tool_call:
        msg["tool_calls"] = [
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "web_search", "arguments": '{"query": "цветы"}'},
            }
        ]
    usage = {"prompt_tokens": 500, "completion_tokens": 50}
    return {"choices": [{"message": msg}], "usage": usage}


def claude(text):
    return {
        "content": [{"type": "text", "text": text, "citations": [{"url": "https://d.ru/"}]}],
        "usage": {"input_tokens": 2000, "output_tokens": 200,
                  "server_tool_use": {"web_search_requests": 1}},
    }  # fmt: skip


def test_parse_each_system():
    assert S.parse("chatgpt", OPENAI) == (
        "Ромашка и другие",
        ["https://a.ru/", "https://b.ru/"],
        1,
        1000,
        100,
    )
    text, src, n, tin, tout = S.parse("gemini", GEMINI)
    assert (text, src, n, tin, tout) == ("Флора", ["https://c.ru/"], 2, 100, 50)
    assert S.cost("gemini", "gemini-3.5-flash-lite", 0, None, None) is None  # неизвестно
    assert S.parse("claude", claude("x"))[2:] == (1, 2000, 200)
    assert S.cost("claude", "claude-haiku-5-5", 1, 2000, 200) == pytest.approx(0.0103)
    assert S.parse("perplexity", PERPLEXITY) == ("Ромашка", ["https://e.ru/"], 2, 3000, 500)
    loop = {"steps": [
        {"type": "chat", "response": deepseek_chat(True)},
        {"type": "search", "query": "цветы", "response": {"results": [{"url": "https://f.ru/"}]}},
        {"type": "chat", "response": deepseek_chat(False)},
    ]}  # fmt: skip
    assert S.parse("deepseek", loop) == ("Ромашка лучше", ["https://f.ru/"], 1, 1000, 100)


def test_estimate_and_routes(monkeypatch):
    for key in S.KEYS.values():
        monkeypatch.setenv(key, "test-key")
    est = S.estimate(DOC, ["chatgpt", "claude"])
    assert 0 < est["всего"][0] < est["всего"][1]
    assert S.ROUTES["chatgpt"][1] == ("gpt-6-sol", "none")
    assert S.ROUTES["claude"][4] == ("claude-haiku-5-5", "medium")
    assert S.ROUTES["chatgpt"][4] == ("gpt-6-sol", "none")
    body = S.request("chatgpt", "gpt-6-sol", "none", "x", "Россия", 4)[2]
    assert body["max_tool_calls"] == 6 and body["tools"][0]["search_context_size"] == "low"
    body = S.request("claude", "claude-haiku-5-5", "medium", "x", "Россия", 2)[2]
    assert body["tools"][0]["max_uses"] == 3


@pytest.fixture
def fake_api(monkeypatch):
    flip = itertools.count()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert "system" not in body and "instructions" not in body  # без служебной инструкции
            if self.path == "/v1/responses":
                status, doc = 200, OPENAI
            elif self.path == "/v1/agent":
                status, doc = 200, PERPLEXITY
            elif self.path == "/chat/completions":  # DeepSeek: сначала просит поиск, потом отвечает
                asked = any(m["role"] == "tool" for m in body["messages"])
                assert asked or "tool_choice" not in body
                status, doc = 200, deepseek_chat(not asked)
            elif self.path == "/v1/tools/search":
                assert body["count"] == S.DEEPSEEK_RESULTS
                status, doc = 200, {"results": [{"url": "https://romashka.ru/", "title": "t"}]}
            elif self.path.endswith(":generateContent"):
                status, doc = 400, {"error": "bad request"}
            else:  # Claude: то называет компанию, то нет — нужны повторы до 10
                status, doc = 200, claude("Ромашка" if next(flip) % 2 else "Флора")
            raw = json.dumps(doc, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(S, "BASE", dict.fromkeys(S.ROUTES, base))
    for key in S.KEYS.values():
        monkeypatch.setenv(key, "test-key")
    yield
    server.shutdown()
    server.server_close()


def test_collect_adaptive_repeats_errors_and_raw(fake_api, tmp_path):
    col = S.Collector(DOC, tmp_path, ["chatgpt", "gemini", "claude"], max_cost=100)
    rows = asyncio.run(col.run(DOC["prompts"]))
    by = lambda s, q: [r for r in rows if r["system"] == s and r["prompt_id"] == q]  # noqa: E731
    assert len(by("chatgpt", "q01")) == 3  # везде назвали — стоп после 3
    assert len(by("claude", "q01")) == 5  # результаты разные — добрали до 5
    assert {r["cites_site"] for r in by("chatgpt", "q01")} == {False}
    assert {r["status"] for r in by("gemini", "q01")} == {"error"}  # 400: без повторов
    lines = (tmp_path / "answers.jsonl").read_text().splitlines()
    assert len(lines) == len(rows)
    ok = next(r for r in rows if r["status"] == "ok")
    packed = (tmp_path / ok["raw_file"]).read_bytes()
    assert hashlib.sha256(packed).hexdigest() == ok["raw_sha256"]
    assert json.loads(gzip.decompress(packed))
    summary = S.summarize(rows, ["chatgpt", "gemini", "claude"])
    assert summary["usable_systems"] == ["chatgpt", "claude"]
    assert summary["excluded"][0]["system"] == "gemini"


def test_fixed_repeats_on_request(fake_api, tmp_path):
    col = S.Collector(DOC, tmp_path, ["chatgpt", "claude"], max_cost=100, repeats=5)
    rows = asyncio.run(col.run(DOC["prompts"]))
    assert len(rows) == 2 * 2 * 5 == 20


def test_cites_site_matches_subdomains_only(tmp_path):
    col = S.Collector(DOC, tmp_path, ["claude"])
    assert col.cites_site(["https://www.romashka.ru/a", "https://x.ru/"])
    assert col.cites_site(["https://shop.romashka.ru/"])
    assert not col.cites_site(["https://notromashka.ru/", "https://romashka.ru.evil.com/"])


def test_cost_ceiling_stops_new_requests(fake_api, tmp_path):
    col = S.Collector(DOC, tmp_path, ["claude"], max_cost=0.0001)
    rows = asyncio.run(col.run(DOC["prompts"]))
    assert col.stopped and any(r["status"] == "skipped" for r in rows)


def test_per_system_ceiling_blocks_only_that_system(fake_api, tmp_path):
    col = S.Collector(DOC, tmp_path, ["chatgpt", "claude"], per_system=0.0001)
    rows = asyncio.run(col.run(DOC["prompts"]))
    assert col.blocked == {"chatgpt", "claude"} and not col.stopped
    assert all(r["status"] in {"ok", "skipped"} for r in rows)


def test_latest_row_replaces_error(tmp_path):
    f = tmp_path / "answers.jsonl"
    rows = [
        {"prompt_id": "q01", "system": "chatgpt", "repeat": 1, "status": "error"},
        {"prompt_id": "q01", "system": "chatgpt", "repeat": 2, "status": "ok"},
        {"prompt_id": "q01", "system": "chatgpt", "repeat": 1, "status": "ok"},
    ]
    f.write_text("\n".join(json.dumps(r) for r in rows))
    assert sorted(r["status"] for r in S.latest(f)) == ["ok", "ok"]


def test_perplexity_and_deepseek_loop(fake_api, tmp_path):
    col = S.Collector(DOC, tmp_path, ["perplexity", "deepseek"], max_cost=100, repeats=1)
    rows = asyncio.run(col.run(DOC["prompts"][:1]))
    by = {r["system"]: r for r in rows}
    assert by["perplexity"]["status"] == "ok" and by["perplexity"]["searches"] == 2
    ds = by["deepseek"]
    assert ds["status"] == "ok" and ds["mentioned"] and ds["cites_site"]
    assert (ds["searches"], ds["input_tokens"], ds["sources"]) == (
        1,
        1000,
        ["https://romashka.ru/"],
    )
