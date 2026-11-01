from pathlib import Path

import pytest

from geo_audit import Budget, SafeHttpClient, Step0Config, cli, interactive, validate_site
from geo_audit.errors import GeoAuditError, error_detail
from tests.fakes import Connector, Resolver, VirtualTime, install_connector, wire


def scripted_input(*values):
    entries = iter(values)

    def read(prompt):
        try:
            return next(entries)
        except StopIteration:
            raise EOFError from None

    return read


def test_multiple_sites_use_real_runner_and_independent_budgets(monkeypatch, tmp_path):
    connector = Connector([wire(), wire()])
    install_connector(monkeypatch, connector)
    sites = []

    async def run(url, config, *, output_dir):
        clock = VirtualTime()
        timing = clock.services()
        budget = Budget(config, timing=timing)
        client = SafeHttpClient(config, resolver=Resolver(), timing=timing)
        site = await validate_site(
            url, config, client, budget, output_dir=output_dir, timing=timing
        )
        sites.append(site)
        return site

    monkeypatch.setattr(interactive, "validate_site", run)
    output = []
    assert interactive.run_interactive(
        Step0Config(),
        tmp_path,
        read_line=scripted_input(
            "https://example.com/?token=do-not-print", "http://example.com/", "exit"
        ),
        write_line=output.append,
    ) == 0
    assert len(sites) == len(connector.calls) == 2
    assert sites[0].run_id != sites[1].run_id
    assert all(site.budget.total_attempts == 1 for site in sites)
    assert all((site._run_directory / "site.json").is_file() for site in sites)
    rendered = "\n".join(output)
    assert rendered.count("Результат: ok") == 2
    assert "do-not-print" not in rendered and "token=" not in rendered


def test_known_input_error_does_not_end_interactive_session(monkeypatch, tmp_path):
    calls = []

    async def fail(url, config, *, output_dir):
        calls.append(url)
        raise GeoAuditError(error_detail("INPUT_URL_INVALID"))

    monkeypatch.setattr(interactive, "validate_site", fail)
    output = []
    assert interactive.run_interactive(
        Step0Config(), tmp_path,
        read_line=scripted_input("bad-one", "bad-two", ""), write_line=output.append,
    ) == 0
    assert calls == ["bad-one", "bad-two"]
    assert sum("INPUT_URL_INVALID" in line for line in output) == 2


def test_programming_error_propagates_instead_of_becoming_site_failure(monkeypatch, tmp_path):
    async def broken(*args, **kwargs):
        raise RuntimeError("controlled implementation defect")

    monkeypatch.setattr(interactive, "validate_site", broken)
    with pytest.raises(RuntimeError, match="controlled implementation defect"):
        interactive.run_interactive(
            Step0Config(), tmp_path,
            read_line=scripted_input("example.com"), write_line=lambda _: None,
        )


def test_eof_exits_without_network_or_output_directory(tmp_path):
    assert interactive.run_interactive(
        Step0Config(), tmp_path / "unused",
        read_line=scripted_input(), write_line=lambda _: None,
    ) == 0
    assert not (tmp_path / "unused").exists()


def test_cli_interactive_configuration_matches_single_run(monkeypatch, tmp_path):
    received = []

    def run(config, output_dir):
        received.append((config, output_dir))
        return 0

    monkeypatch.setattr(cli, "run_interactive", run)
    assert cli.main(
        ["try-step0", "--no-http-fallback", "--allow-host", "Other.COM",
         "--output-dir", str(tmp_path)]
    ) == 0
    config, directory = received[0]
    assert config.allow_http_fallback is False
    assert config.allowed_hosts == ("other.com",)
    assert directory == Path(tmp_path)
