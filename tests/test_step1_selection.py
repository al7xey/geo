from bs4 import BeautifulSoup

from geo_audit.step1.crawler import ACCOUNT_SEGMENTS, NON_HTML_SUFFIXES, strip_tracking
from geo_audit.step1.extraction import business_span, page_description


def test_tracking_parameters_collapse_to_one_page():
    assert strip_tracking("https://example.ru/faq?opened_from=header") == "https://example.ru/faq"
    assert (
        strip_tracking("https://example.ru/p?id=7&utm_source=mail&from=footer")
        == "https://example.ru/p?id=7"
    )
    assert strip_tracking("https://example.ru/p?id=7") == "https://example.ru/p?id=7"


def test_account_and_file_paths_are_not_page_candidates():
    assert {"signin", "register", "recovery"} <= ACCOUNT_SEGMENTS
    assert "/docs/rules.pdf".endswith(NON_HTML_SUFFIXES)
    assert not "/info/about".endswith(NON_HTML_SUFFIXES)


def test_metadata_description_for_script_rendered_page():
    soup = BeautifulSoup(
        """<html><head><title>Ромашка — доставка цветов по Москве</title>
        <meta name="description" content="Доставляем букеты за 2 часа, более 500 сортов.">
        <meta property="og:title" content="Ромашка">
        <script type="application/ld+json">
        {"@type": "Organization", "name": "Ромашка",
         "description": "Цветочный магазин с собственной оранжереей."}</script>
        </head><body><div id="app"></div></body></html>""",
        "html.parser",
    )
    lines = page_description(soup)
    assert lines[0] == "Ромашка — доставка цветов по Москве"
    assert "Доставляем букеты за 2 часа, более 500 сортов." in lines
    assert "Цветочный магазин с собственной оранжереей." in lines
    assert "Ромашка" not in lines  # too short to carry meaning


def test_broader_activity_phrases():
    assert business_span("Мы помогаем найти репетитора по любому предмету онлайн")
    assert business_span("Сервис поиска дешёвых авиабилетов по всему миру")
    assert business_span("Маркетплейс для фрилансеров и заказчиков с гарантией")
    assert not business_span("Политика конфиденциальности и условия использования")
