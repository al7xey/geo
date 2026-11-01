"""Human-facing CLI loop; each URL receives an independent, bounded step 0 run."""

import asyncio
from collections.abc import Callable
from pathlib import Path

from .api import validate_site
from .config import Step0Config
from .errors import GeoAuditError
from .models import SiteValidationResult
from .urls import safe_display_url


def _summary(site: SiteValidationResult) -> str:
    response = next((a for a in site.attempts if a.attempt_id == site.final_attempt_id), None)
    lines = [
        f"Результат: {site.status}",
        f"Можно передать в шаг 1: {'да' if site.can_enter_step1 else 'нет'}",
        f"Содержимое: {site.content_state}",
    ]
    if site.effective_url:
        lines.append(f"Конечный сайт: {safe_display_url(site.effective_url)}")
    if response:
        lines.append(f"HTTP: {response.status_code}; TLS: {response.tls_state}")
        lines.append(f"Получено: {response.decoded_bytes} байт после распаковки")
    lines.extend(
        [
            f"Попыток: {site.budget.step0_attempts}; время: {site.budget.elapsed_seconds:.2f} с",
            f"HTTP fallback: {'да' if site.fallback_used else 'нет'}",
            f"Причины: {', '.join(site.reason_codes) or 'нет'}",
            f"Следующее действие: {site.next_action}",
            f"Все данные: {site._run_directory}",
            "",
        ]
    )
    return "\n".join(lines)


def run_interactive(
    config: Step0Config,
    output_dir: Path,
    *,
    read_line: Callable[[str], str] | None = None,
    write_line: Callable[[str], None] = print,
) -> int:
    read = read_line or input
    write_line("GEO — ручная проверка шага 0")
    write_line("Введите публичный адрес сайта. Пустая строка или exit — выход.")
    write_line("Проверяется только входная страница; данные сохраняются в отдельную папку.")
    write_line("При редиректе на другой хост используйте параметр --allow-host при запуске.")
    write_line("")
    while True:
        try:
            url = read("Сайт> ").strip()
        except EOFError:
            return 0
        if not url or url.lower() in {"exit", "quit", "выход"}:
            return 0
        write_line("Проверяю... (лимит шага 0 задаётся конфигурацией)")
        try:
            site = asyncio.run(validate_site(url, config, output_dir=output_dir))
        except GeoAuditError as error:
            write_line(f"Проверка не выполнена: {error.detail.code}\n")
            continue
        write_line(_summary(site))
