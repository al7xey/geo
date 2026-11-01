"""Loopback-only, single-audit web presentation over the existing safe pipeline."""

import asyncio
import hmac
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, cast

from .errors import GeoAuditError
from .models import SiteValidationResult
from .recommendations import recommendation_report
from .step1.models import Step1Result
from .step1.pipeline import audit_site

HTML = r"""<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>GEO Audit</title>
<style nonce="NONCE">
*{box-sizing:border-box}body{margin:0;background:#f6f7f9;color:#20242c;font:15px/1.6 system-ui}
main{max-width:1000px;margin:55px auto;padding:0 24px}h1{font-size:32px;letter-spacing:-1px}
.muted{color:#687080}header{margin-bottom:28px}section{background:white;border:1px solid #e3e6eb;
border-radius:16px;padding:24px;margin:16px 0}form{display:flex;gap:10px}input{flex:1;min-width:0;
border:1px solid #cdd3dd;border-radius:9px;padding:13px;font:inherit}button{background:#263ed6;
color:white;border:0;border-radius:9px;padding:12px 20px;font:inherit;cursor:pointer}
button:disabled{opacity:.5;cursor:wait}textarea{width:100%;min-height:90px;font:inherit;
padding:10px;border:1px solid #cdd3dd;border-radius:9px}
.metrics{display:flex;gap:30px;flex-wrap:wrap}
.metric strong{display:block;font-size:26px}.metric span{color:#687080;font-size:13px}
.row{padding:14px 0;border-top:1px solid #edf0f3}.tag{display:inline-block;background:#eef1f6;
border-radius:5px;padding:1px 8px;font-size:12px;margin-right:8px}.fail{color:#ad2535}
.warning,.unknown{color:#916000}.pass{color:#18734c}h2{font-size:19px}h3{font-size:15px;
margin:4px 0}p{margin:6px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}
details{margin:12px 0}#state{margin-top:12px}a{color:#263ed6}
@media(max-width:600px){main{margin:25px auto}form{flex-direction:column}section{padding:18px}}
</style><main><header><h1>GEO Audit</h1><p class="muted">Технический анализ сайта.
Проверяемые наблюдения и рекомендации — без платных API.</p></header>
<section><form id="form"><input id="url" aria-label="Адрес сайта" placeholder="https://example.com"
required maxlength="8192"><button id="start">Анализировать</button></form>
<details><summary>Описание бизнеса (необязательно)</summary>
<textarea id="description" maxlength="8192" aria-label="Описание бизнеса"
placeholder="Например: Мы производим промышленное оборудование для…"></textarea></details>
<p id="state" role="status" class="muted">Введите адрес публичного сайта.</p>
<p class="muted">До 30 страниц · общий предел 5 минут · браузер и UA probes выключены</p></section>
<div id="result"></div><footer class="muted">Локальный интерфейс.
Результаты сохраняются в private/runs.
Видимость в ответах ИИ не измеряется. CAPTCHA, robots и авторизация не обходятся.</footer></main>
<script nonce="NONCE">
const $=id=>document.getElementById(id), token='TOKEN';let timer;
const labels={ok:'Завершён',partial:'Частичный результат',needs_input:'Нужно описание бизнеса',
failed:'Не завершён',pass:'Пройдено',fail:'Проблема',warning:'Предупреждение',unknown:'Неизвестно',
not_applicable:'Не применяется',limited:'Ограниченный',usable:'Достаточный',
insufficient:'Недостаточный',needs_description:'Нужно описание',
user_supported:'С описанием пользователя'};
function node(tag,text,cls){const n=document.createElement(tag);n.textContent=text;
if(cls)n.className=cls;return n}
function render(r){$('result').replaceChildren();const box=node('section','');
box.append(node('h2',labels[r.status]||r.status));const metrics=node('div','','metrics');
for(const [label,value] of [['Выбрано страниц',r.coverage?.selected_candidates??0],
['Полезных',r.coverage?.useful_pages??0],['HTTP-попыток',r.attempts],
['Корпус',labels[r.material]||r.material||'Нет данных']]){
const m=node('div','','metric');m.append(node('strong',String(value)),node('span',label));
metrics.append(m)}
box.append(metrics);box.append(node('p','Результат: '+r.result_directory,'muted'));
if(r.limitations?.length)box.append(node('p','Ограничения: '+r.limitations.join('; '),'unknown'));
$('result').append(box);const rec=node('section','');rec.append(node('h2','Рекомендации'));
for(const item of r.recommendations.recommendations){const row=node('div','','row');
row.append(node('span',item.priority,'tag'),node('h3',item.title),node('p',item.action));rec.append(row)}
if(!r.recommendations.recommendations.length)
rec.append(node('p','Новых технических рекомендаций нет.'));
$('result').append(rec);const checks=node('section','');checks.append(node('h2','Наблюдения'));
for(const c of r.checks){const row=node('details','','row');const s=node('summary','');
s.append(node('span',labels[c.status]||c.status,'tag '+c.status),
node('span',c.kind+' · '+c.reason_code));row.append(s,node('p',c.observation),
node('p',c.scope,'muted'),node('p',c.limitation,'muted'));checks.append(row)}
$('result').append(checks);const signals=node('section','');
signals.append(node('h2','Язык и география'));
signals.append(node('p','Это сигналы источника, не установленный профиль компании.','muted'));
for(const s of r.signals)signals.append(node('p',s.kind+': '+s.value));
if(!r.signals.length)signals.append(node('p','Подтверждённых сигналов не найдено.'));
$('result').append(signals);const raw=node('details','');
raw.append(node('summary','JSON результата'),
node('pre',JSON.stringify(r,null,2)));$('result').append(raw)}
async function poll(){try{const response=await fetch('/api/status',{cache:'no-store'});
const job=await response.json();if(job.state==='running'){
$('state').textContent='Анализ выполняется… '+Math.round(job.elapsed_seconds)+' с';
timer=setTimeout(poll,1500);return} $('start').disabled=false;
if(job.state==='done'){render(job.result);$('state').textContent='Анализ завершён.'}
else if(job.state==='error')$('state').textContent='Ошибка: '+job.error;
}catch(e){$('start').disabled=false;$('state').textContent='Нет связи с локальным сервером.'}}
$('form').onsubmit=async e=>{e.preventDefault();clearTimeout(timer);$('start').disabled=true;
$('state').textContent='Запуск анализа…';$('result').replaceChildren();try{
const response=await fetch('/api/audit',{method:'POST',headers:{'Content-Type':'application/json',
'X-GEO-Token':token},body:JSON.stringify({url:$('url').value.trim(),
description:$('description').value.trim()||null})});
if(!response.ok){const x=await response.json();throw Error(x.error)}await poll();
}catch(e){$('start').disabled=false;$('state').textContent='Ошибка: '+e.message}};poll();
</script></html>"""


def summary(result: Step1Result | SiteValidationResult, output: Path) -> dict[str, Any]:
    if isinstance(result, Step1Result):
        directory = output / result.run.publication_id
        return dict(
            status=result.run.status,
            material=result.crawl.material.state,
            coverage=result.crawl.coverage.model_dump(mode="json", exclude={"skip_examples"}),
            attempts=result.run.budget.total_attempts,
            limitations=result.run.limitations,
            result_directory=str(directory),
            checks=[c.model_dump(mode="json") for c in result.checks.checks],
            signals=[s.model_dump(mode="json") for p in result.crawl.pages for s in p.signals],
            recommendations=recommendation_report(directory).model_dump(mode="json"),
        )
    source_directory = result._run_directory
    if not isinstance(source_directory, Path):
        raise ValueError("No persisted source result")
    return dict(
        status=result.status,
        material=None,
        coverage=None,
        attempts=result.budget.total_attempts if result.budget else len(result.attempts),
        limitations=result.reason_codes,
        result_directory=str(source_directory),
        checks=[],
        signals=[],
        recommendations=recommendation_report(source_directory).model_dump(mode="json"),
    )


class AuditJob:
    """One bounded job; no unbounded queue or retained history in RAM."""

    def __init__(self, output: Path):
        self.output = output
        self.lock = threading.Lock()
        self.state: dict[str, Any] = {"state": "idle"}
        self.started = 0.0
        self.error: BaseException | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.task: asyncio.Task[Step1Result | SiteValidationResult] | None = None
        self.worker: threading.Thread | None = None
        self.closing = threading.Event()

    def start(self, url: str, description: str | None = None) -> bool:
        import time

        with self.lock:
            if self.state["state"] == "running":
                return False
            self.state = {"state": "running"}
            self.started = time.monotonic()
            self.error = None
        self.worker = threading.Thread(target=self.work, args=(url, description), daemon=True)
        self.worker.start()
        return True

    def work(self, url: str, description: str | None = None) -> None:
        async def execute() -> Step1Result | SiteValidationResult:
            self.loop = asyncio.get_running_loop()
            self.task = asyncio.create_task(
                audit_site(url, output_dir=self.output, description=description)
            )
            if self.closing.is_set():
                self.task.cancel()
            return await self.task

        try:
            result = asyncio.run(execute())
            completed = {"state": "done", "result": summary(result, self.output)}
        except (Exception, asyncio.CancelledError) as error:
            # UI boundary only: retain the original exception, never fabricate success
            # or reflect untrusted exception text into browser/log output.
            self.error = error
            code = error.detail.code if isinstance(error, GeoAuditError) else "IMPLEMENTATION_BUG"
            if isinstance(error, OSError):
                code = "OUTPUT_WRITE_FAILED"
            if isinstance(error, asyncio.CancelledError):
                code = "NETWORK_CANCELLED"
            completed = {"state": "error", "error": code}
        with self.lock:
            self.state = completed

    def close(self) -> None:
        self.closing.set()
        loop, task = self.loop, self.task
        if loop is not None and task is not None and not loop.is_closed():
            loop.call_soon_threadsafe(task.cancel)
        if self.worker is not None:
            self.worker.join(timeout=10)

    def snapshot(self) -> dict[str, Any]:
        import time

        with self.lock:
            return {**self.state, "elapsed_seconds": max(0.0, time.monotonic() - self.started)}


def create_server(port: int, output: Path) -> HTTPServer:
    job = AuditJob(output)
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass  # Request paths/URLs must not become an access log.

        def allowed_host(self) -> bool:
            server = cast(HTTPServer, self.server)
            expected = {
                f"127.0.0.1:{server.server_port}",
                f"localhost:{server.server_port}",
            }
            return self.headers.get("Host") in expected

        def reply(self, status: int, body: bytes, mime: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", mime + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                f"default-src 'none'; script-src 'nonce-{token}'; style-src 'nonce-{token}'; "
                "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            )
            self.end_headers()
            self.wfile.write(body)

        def json_reply(self, status: int, value: object) -> None:
            self.reply(status, json.dumps(value, ensure_ascii=True).encode("ascii"))

        def do_GET(self) -> None:
            if not self.allowed_host():
                self.json_reply(403, {"error": "HOST_REJECTED"})
            elif self.path == "/":
                self.reply(
                    200, HTML.replace("NONCE", token).replace("TOKEN", token).encode(), "text/html"
                )
            elif self.path == "/api/status":
                self.json_reply(200, job.snapshot())
            else:
                self.json_reply(404, {"error": "NOT_FOUND"})

        def do_POST(self) -> None:
            if not self.allowed_host() or not hmac.compare_digest(
                self.headers.get("X-GEO-Token", "").encode("utf-8"), token.encode("ascii")
            ):
                self.json_reply(403, {"error": "REQUEST_REJECTED"})
                return
            origin = self.headers.get("Origin")
            if origin is not None and origin != "http://" + self.headers["Host"]:
                self.json_reply(403, {"error": "ORIGIN_REJECTED"})
                return
            if self.path != "/api/audit":
                self.json_reply(404, {"error": "NOT_FOUND"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 32768 or self.headers.get("Transfer-Encoding") is not None:
                    raise ValueError("invalid length")
                payload = json.loads(self.rfile.read(size))
                if (
                    not isinstance(payload, dict)
                    or "url" not in payload
                    or not set(payload) <= {"url", "description"}
                ):
                    raise ValueError("invalid payload")
                url = payload["url"]
                if not isinstance(url, str) or not 1 <= len(url.strip()) <= 8192:
                    raise ValueError("invalid URL")
                description = payload.get("description")
                if description is not None and (
                    not isinstance(description, str) or not 1 <= len(description.strip()) <= 8192
                ):
                    raise ValueError("invalid description")
            except (ValueError, UnicodeError):
                self.json_reply(400, {"error": "INVALID_INPUT"})
                return
            self.json_reply(202 if job.start(url.strip(), description) else 409, job.snapshot())

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(5)

    class LocalServer(HTTPServer):
        def server_close(self) -> None:
            job.close()
            super().server_close()

    return LocalServer(("127.0.0.1", port), Handler)


def serve(port: int = 8765, output: Path = Path("private/runs")) -> None:
    with create_server(port, output) as server:
        print(f"GEO Audit: http://127.0.0.1:{server.server_port} (Ctrl+C to stop)", flush=True)
        server.serve_forever()
