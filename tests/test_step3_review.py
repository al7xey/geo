"""Офлайн-тесты шага 3 и перехода в шаг 4. Сеть (кроме loopback) и ключ API не нужны."""

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import step3_review as R  # noqa: E402
import step4_handoff as H  # noqa: E402

PROFILE = {
    "data_status": "ok",
    "domain": "romashka.ru",
    "name_variants": [{"variant": "Ромашка", "ambiguous": False}],
    "category": {"main": "доставка цветов", "client_terms": ["доставка цветов"]},
}
ORGANIC = "Где в Москве заказать доставку свежих цветов на дом сегодня вечером"
BRANDED = (
    "Выбираю службу доставки цветов в Москве, важны свежесть и доставка за два часа, "
    "бюджет средний. Рассматриваю Ромашка. Подходит ли она по сравнению с двумя-тремя "
    "главными альтернативами?"
)


def prompts():
    return [
        {"id": "q01", "plan_position": "1.01", "level": 1, "level_name": "Базовые",
         "scenario": "s", "pair_id": None, "text": ORGANIC},
        {"id": "q26", "plan_position": "3.06", "level": 3, "level_name": "Выбор и сравнение",
         "scenario": "s", "pair_id": "P1", "text": BRANDED},
    ]  # fmt: skip


@pytest.fixture
def step2(tmp_path):
    d = tmp_path / "step2"
    d.mkdir()
    (d / "profile.json").write_text(json.dumps(PROFILE, ensure_ascii=False), encoding="utf-8")
    (d / "prompts.json").write_text(json.dumps({"prompts": prompts()}, ensure_ascii=False))
    report = {"domain": "romashka.ru", "language": "русский", "country": "Россия"}
    (d / "step2_report.json").write_text(json.dumps(report, ensure_ascii=False))
    return d


def test_unchanged_prompts_are_suggested():
    state = R.initial_state(PROFILE, prompts())
    final, errors, notices = R.check(state, PROFILE, prompts(), "romashka.ru")
    assert not errors and not notices
    assert [q["origin"] for q in final] == ["suggested", "suggested"]
    assert final[1]["has_brand"] and not final[1]["counts_in_organic_visibility"]


def test_edit_custom_brand_and_limits():
    state = R.initial_state(PROFILE, prompts())
    state["prompts"][0]["text"] = ORGANIC + " недорого"
    state["custom"] = [
        {"id": "c01", "level": 1, "text": "Стоит ли заказывать букет в Ромашке на юбилей мамы"},
        {"id": "c02", "level": None, "text": "Какие цветы дарить коллеге на день рождения"},
        {"id": "c03", "level": 1, "text": "   "},
    ]
    final, errors, notices = R.check(state, PROFILE, prompts(), "romashka.ru")
    by = {q["id"]: q for q in final}
    assert by["q01"]["origin"] == "edited" and by["c01"]["origin"] == "custom"
    assert not by["c01"]["counts_in_organic_visibility"] and "c01" in notices
    assert "вид" in errors["c02"][0] and "пустой" in errors["c03"][0]
    state["custom"] = [{"id": f"c{i:02d}", "level": 1, "text": ORGANIC} for i in range(11)]
    _, errors, _ = R.check(state, PROFILE, prompts(), "romashka.ru")
    assert "custom" in errors and any("Почти повторяет" in e for e in errors["c01"])


def test_branded_position_needs_name_and_profile_needs_names():
    state = R.initial_state(PROFILE, prompts())
    state["prompts"][1]["text"] = BRANDED.replace("Ромашка", "этот сервис")
    state["names"] = []
    _, errors, _ = R.check(state, PROFILE, prompts(), "romashka.ru")
    assert "profile" in errors
    state["names"] = ["Ромашка"]
    _, errors, _ = R.check(state, PROFILE, prompts(), "romashka.ru")
    assert any("брендовая" in e for e in errors["q26"])


def test_save_writes_step4_input_and_detects_tampering(step2, tmp_path):
    state = R.initial_state(PROFILE, prompts())
    state["names"] = ["Ромашка", "Romashka"]
    res = R.save(step2, tmp_path / "out", state)
    assert res["saved"] and res["summary"]["предложено"] == 2
    path = tmp_path / "out" / "step4_input.json"
    doc = H.load_step4_input(path)
    assert doc["name_variants"] == ["Ромашка", "Romashka"] and doc["country"] == "Россия"
    assert H.plan(doc, ["a", "b"])["total_requests"] == [12, 40]
    doc["prompts"][0]["text"] = "Подменённый вопрос после проверки шага три"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="изменены после проверки"):
        H.load_step4_input(path)


def test_local_server_requires_token(step2, tmp_path):
    server = R.create_server(step2, tmp_path / "out", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        page = urllib.request.urlopen(base + "/").read().decode()
        token = page.split("const token='", 1)[1].split("'", 1)[0]
        body = json.dumps(R.initial_state(PROFILE, prompts())).encode()
        bad = urllib.request.Request(base + "/api/save", data=body, method="POST")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(bad)
        assert e.value.code == 403
        good = urllib.request.Request(
            base + "/api/check", data=body, method="POST", headers={"X-GEO-Token": token}
        )
        assert json.loads(urllib.request.urlopen(good).read())["errors"] == {}
    finally:
        server.shutdown()
        server.server_close()
