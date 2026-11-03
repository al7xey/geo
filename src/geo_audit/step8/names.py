"""Pure, conservative name keys. Transliteration is only a suggestion signal."""

import re
import unicodedata


def normalize_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip()
    for left, right in (("«", "»"), ('"', '"'), ("“", "”"), ("'", "'")):
        if value.startswith(left) and value.endswith(right) and len(value) > 1:
            value = value[len(left) : -len(right)].strip()
            break
    return re.sub(r"\s+", " ", value).casefold()


TRANSLITERATION = dict(
    zip(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
        (
            "a",
            "b",
            "v",
            "g",
            "d",
            "e",
            "e",
            "zh",
            "z",
            "i",
            "i",
            "k",
            "l",
            "m",
            "n",
            "o",
            "p",
            "r",
            "s",
            "t",
            "u",
            "f",
            "kh",
            "ts",
            "ch",
            "sh",
            "shch",
            "",
            "y",
            "",
            "e",
            "yu",
            "ya",
        ),
        strict=True,
    )
)


def transliteration_key(value: str) -> str:
    return "".join(TRANSLITERATION.get(char, char) for char in normalize_name(value))
