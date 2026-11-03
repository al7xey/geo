"""Same-origin loopback HTTP API and static React host, with isolated test workspaces."""

import argparse
import csv
import hashlib
import hmac
import io
import json
import mimetypes
import re
import secrets
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from pydantic import ValidationError

from ..errors import GeoAuditError
from ..evidence import EvidenceStore
from ..step5.pipeline import MAX_METADATA_BYTES, unique_json
from .model_gateway import LiveGateway, load_environment
from .models import QuestionUpdate, ShareSettings
from .service import AppError, Application

PROJECT = re.compile(r"^/api/projects/([a-f0-9]{32})(?:/(.*))?$")
TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port: int, application: Application, frontend: Path, *, dev: bool = False):
        self.application, self.frontend, self.dev = application, EvidenceStore(frontend), dev
        self.capacity = threading.BoundedSemaphore(16)
        secret_path = application.store.io.root / "workspace-secret.bin"
        if not secret_path.exists():
            application.store.io.atomic_bytes(secret_path.name, secrets.token_bytes(32))
        self.secret = application.store.io.read_bytes(secret_path.name, limit=32)
        if len(self.secret) != 32:
            raise ValueError("invalid workspace secret")
        super().__init__(("127.0.0.1", port), Handler)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self.capacity.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.capacity.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.capacity.release()


class Handler(BaseHTTPRequestHandler):
    server: Server

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(10)
        self.new_cookie: str | None = None

    def log_message(self, format: str, *args: Any) -> None:
        # URLs can contain capability tokens; do not log request paths or bodies.
        return

    def hosts(self) -> set[str]:
        port = self.server.server_port
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def gate(self, *, mutate: bool = False) -> tuple[str, str]:
        if self.headers.get("Host") not in self.hosts():
            raise AppError("bad_host", "Недопустимый адрес сервера.", 403)
        origins = {"http://" + host for host in self.hosts()}
        if self.server.dev:
            origins.update({"http://127.0.0.1:5173", "http://localhost:5173"})
        origin = self.headers.get("Origin")
        if origin is not None and origin not in origins:
            raise AppError("bad_origin", "Запрос с другого сайта отклонён.", 403)
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except Exception:
            cookies = SimpleCookie()
        raw = cookies.get("geo_workspace")
        token = raw.value if raw else ""
        owner = token.split(".", 1)[0]
        signature = hmac.new(self.server.secret, owner.encode(), hashlib.sha256).hexdigest()
        if not re.fullmatch(r"[a-f0-9]{32}\.[a-f0-9]{64}", token) or not hmac.compare_digest(
            token, owner + "." + signature
        ):
            owner = secrets.token_hex(16)
            token = (
                owner
                + "."
                + hmac.new(self.server.secret, owner.encode(), hashlib.sha256).hexdigest()
            )
            self.new_cookie = token
        csrf = hmac.new(self.server.secret, (token + ":csrf").encode(), hashlib.sha256).hexdigest()
        if mutate and (
            origin not in origins
            or not hmac.compare_digest(self.headers.get("X-GEO-Token", ""), csrf)
        ):
            raise AppError("csrf", "Обновите страницу и повторите действие.", 403)
        return owner, csrf

    def send(
        self,
        status: int,
        data: bytes,
        content_type: str = "application/json; charset=utf-8",
        *,
        filename: str | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header(
            "Cache-Control",
            "no-store"
            if not self.path.startswith("/assets/")
            else "public, max-age=31536000, immutable",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "font-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
        )
        if self.new_cookie:
            self.send_header(
                "Set-Cookie",
                "geo_workspace="
                + self.new_cookie
                + "; Path=/; HttpOnly; SameSite=Strict; Max-Age=604800",
            )
        if filename:
            self.send_header("Content-Disposition", 'attachment; filename="' + filename + '"')
        self.end_headers()
        self.wfile.write(data)

    def json(self, data: Any, status: int = 200) -> None:
        self.send(status, json.dumps(data, ensure_ascii=False, allow_nan=False).encode())

    def body(self) -> dict[str, Any]:
        if (
            self.headers.get("Transfer-Encoding")
            or self.headers.get_content_type() != "application/json"
        ):
            raise AppError("content_type", "Ожидались данные JSON.", 415)
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            raise AppError("invalid_length", "Некорректный размер запроса.") from None
        if not 0 < length <= 32 * 1024 * 1024:
            raise AppError("body_limit", "Размер данных превышает допустимый предел.", 413)
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise AppError("incomplete_request", "Данные получены не полностью.")
        result = unique_json(raw)
        if not isinstance(result, dict):
            raise AppError("invalid_body", "Ожидался объект JSON.")
        return result

    def do_GET(self) -> None:
        try:
            owner, csrf = self.gate()
            parsed = urlsplit(self.path)
            path = parsed.path
            app = self.server.application
            if path == "/api/session":
                self.json(
                    {
                        "csrf_token": csrf,
                        "identity_mode": "test",
                        "display_name": "Учебный пользователь",
                        "live_models": isinstance(app.gateway, LiveGateway),
                        "email_delivery": False,
                    }
                )
                return
            if path == "/api/projects":
                self.json({"projects": app.list_projects(owner)})
                return
            shared = re.fullmatch(r"/api/shared/([A-Za-z0-9_-]{43})(?:/(answers|export))?", path)
            if shared:
                token, action = shared.groups()
                project = app.shared(token)
                if action == "answers":
                    if not project.share_settings["show_answers"]:
                        raise AppError("not_shared", "Ответы не включены в эту ссылку.", 403)
                    self.json(
                        {
                            "answers": app.examples(
                                project, parse_qs(parsed.query).get("group", [None])[0]
                            )
                        }
                    )
                elif action == "export":
                    self.send(
                        200,
                        json.dumps(app.shared_report(token), ensure_ascii=False).encode(),
                        filename="geo-report.json",
                    )
                else:
                    self.json(app.shared_report(token))
                return
            match = PROJECT.fullmatch(path)
            if match:
                pid, action = match.groups()
                project = app.project(pid, owner)
                if action is None:
                    self.json(app.projection(project))
                elif action == "report":
                    self.json(app.report(project))
                elif action == "answers":
                    self.json(
                        {
                            "answers": app.examples(
                                project, parse_qs(parsed.query).get("group", [None])[0]
                            )
                        }
                    )
                elif action == "export":
                    format = parse_qs(parsed.query).get("format", ["json"])[0]
                    self.export(project, format)
                else:
                    raise AppError("not_found", "Страница не найдена.", 404)
                return
            if path.startswith("/api/"):
                raise AppError("not_found", "Метод API не найден.", 404)
            if path == "/" or path == "/index.html":
                try:
                    data = self.server.frontend.read_bytes("index.html", limit=MAX_METADATA_BYTES)
                except (OSError, GeoAuditError):
                    self.send(
                        503,
                        "Сначала выполните python run.py ui-setup".encode(),
                        "text/plain; charset=utf-8",
                    )
                    return
                self.send(200, data, "text/html; charset=utf-8")
            elif (
                re.fullmatch(r"/assets/[A-Za-z0-9_.-]+\.(js|css|woff2|woff|svg|png)", path)
                and ".." not in path
            ):
                data = self.server.frontend.read_bytes(
                    path.removeprefix("/"), limit=16 * 1024 * 1024
                )
                kind = mimetypes.guess_type(path)[0] or "application/octet-stream"
                self.send(200, data, kind)
            else:
                raise AppError("not_found", "Страница не найдена.", 404)
        except Exception as error:
            self.fail(error)

    def export(self, project: Any, format: str) -> None:
        app = self.server.application
        if format == "json":
            self.send(
                200,
                json.dumps(app.report(project), ensure_ascii=False).encode(),
                filename="geo-report.json",
            )
        elif format == "html":
            data = EvidenceStore(app.result_dir(project) / "report").read_bytes(
                "report.html", limit=MAX_METADATA_BYTES
            )
            self.send(200, data, "text/html; charset=utf-8", filename="geo-report.html")
        elif format == "csv":
            output = io.StringIO(newline="")
            writer = csv.writer(output)
            writer.writerow(["scope", "metric", "percent", "questions", "unknown_answers"])
            for row in app.report(project)["aggregates"]["rates"]:
                writer.writerow(
                    [
                        row["scope"],
                        row["metric"],
                        row["percent"],
                        row["answered_questions"],
                        row["unknown_answers"],
                    ]
                )
            self.send(
                200,
                output.getvalue().encode("utf-8-sig"),
                "text/csv; charset=utf-8",
                filename="geo-metrics.csv",
            )
        else:
            raise AppError("invalid_format", "Неизвестный формат экспорта.")

    def do_POST(self) -> None:
        try:
            owner, _ = self.gate(mutate=True)
            data = self.body()
            path = urlsplit(self.path).path
            app = self.server.application
            if path == "/api/test-login":
                if set(data) - {"code"} or data.get("code") not in {None, "123456"}:
                    raise AppError(
                        "test_code", "Учебный код: 123456. Настоящие пароли не отправляются."
                    )
                self.json({"identity_mode": "test", "display_name": "Учебный пользователь"})
                return
            if path == "/api/projects":
                if set(data) != {"url"} or not isinstance(data["url"], str):
                    raise AppError("invalid_project", "Нужен адрес сайта.")
                self.json(app.create(owner, data["url"]), 201)
                return
            match = PROJECT.fullmatch(path)
            if not match:
                raise AppError("not_found", "Метод API не найден.", 404)
            pid, action = match.groups()
            project = app.project(pid, owner)
            if action == "prepare":
                description = data.get("description", "")
                if (
                    set(data) - {"description"}
                    or not isinstance(description, str)
                    or len(description) > 3000
                ):
                    raise AppError(
                        "invalid_description", "Описание должно быть не длиннее 3000 символов."
                    )
                self.json(app.prepare_questions(owner, pid, description), 202)
            elif action == "questions":
                self.json(app.save_questions(owner, pid, QuestionUpdate.model_validate(data)))
            elif action == "run":
                if set(data) != {"revision"} or type(data["revision"]) is not int:
                    raise AppError("invalid_revision", "Обновите вопросы перед запуском.")
                self.json(app.start(owner, pid, data["revision"]), 202)
            elif action == "cancel":
                self.json(app.cancel(owner, pid))
            elif action == "import":
                if (
                    set(data) != {"profile", "approved", "answers", "synthetic"}
                    or type(data["synthetic"]) is not bool
                ):
                    raise AppError(
                        "invalid_import",
                        "Нужны профиль, утверждённые вопросы, ответы и признак учебных данных.",
                    )
                if (
                    not isinstance(data["profile"], dict)
                    or not isinstance(data["approved"], dict)
                    or not isinstance(data["answers"], str)
                ):
                    raise AppError("invalid_import", "Проверьте формат файлов.")
                self.json(
                    app.import_answers(
                        owner,
                        pid,
                        data["profile"],
                        data["approved"],
                        data["answers"],
                        data["synthetic"],
                    )
                )
            elif action == "completion":
                if (
                    set(data) != {"action_id", "completed"}
                    or not isinstance(data["action_id"], str)
                    or type(data["completed"]) is not bool
                ):
                    raise AppError("invalid_completion", "Некорректный пункт плана.")
                self.json(app.completion(owner, pid, data["action_id"], data["completed"]))
            elif action == "share":
                self.json(app.share(owner, pid, ShareSettings.model_validate(data)))
            elif action == "revoke-share":
                with app.store.lock:
                    app.store.save(project.model_copy(update={"share_hash": None}))
                self.json({"revoked": True})
            elif action == "dispute":
                message = data.get("message")
                if (
                    set(data) != {"message"}
                    or not isinstance(message, str)
                    or not 5 <= len(message.strip()) <= 2000
                ):
                    raise AppError("invalid_dispute", "Опишите замечание: от 5 до 2000 символов.")
                with app.store.lock:
                    latest = app.project(pid, owner)
                    if len(latest.disputes) >= 100:
                        raise AppError("dispute_limit", "Достигнут предел замечаний.", 409)
                    app.store.save(
                        latest.model_copy(update={"disputes": [*latest.disputes, message.strip()]})
                    )
                self.json({"saved": True})
            else:
                raise AppError("not_found", "Метод API не найден.", 404)
        except Exception as error:
            self.fail(error)

    def do_PUT(self) -> None:
        self.send_error(405)

    def fail(self, error: Exception) -> None:
        if isinstance(error, AppError):
            self.json(
                {"error": error.code, "message": error.message, "fields": error.fields},
                error.status,
            )
        elif isinstance(error, (ValueError, ValidationError, GeoAuditError)):
            self.json(
                {
                    "error": "invalid_input",
                    "message": (
                        "Данные не прошли проверку. Проверьте формат и новую папку результата."
                    ),
                },
                400,
            )
        elif isinstance(error, (BrokenPipeError, ConnectionResetError, TimeoutError)):
            return
        else:
            self.json(
                {
                    "error": "internal",
                    "message": "Не удалось выполнить действие. Повторите попытку.",
                },
                500,
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Локальный интерфейс GEO. Учебный вход; модели отключены."
    )
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--data-dir", type=Path, default=Path("private/app"))
    parser.add_argument("--frontend-dir", type=Path, default=Path("frontend/dist"))
    parser.add_argument("--fixture-dir", type=Path, default=Path("examples/mock_answers_v1/input"))
    parser.add_argument("--dev", action="store_true")
    parser.add_argument(
        "--live", action="store_true", help="Разрешить платные API только по кнопке запуска"
    )
    args = parser.parse_args()
    load_environment(Path(".env"))
    load_environment(Path("private/.env"))
    application = Application(
        args.data_dir, args.fixture_dir, LiveGateway(args.fixture_dir) if args.live else None
    )
    server = Server(args.port, application, args.frontend_dir, dev=args.dev)
    print(f"GEO: http://127.0.0.1:{server.server_port} · учебный вход")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        application.closed.set()
        server.server_close()


if __name__ == "__main__":
    main()
