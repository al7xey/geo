"""Offline PSL matching including private, wildcard and exception rules."""

import hashlib
import ipaddress
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files

from .urls import normalize_host

PSL_SHA256 = "617d1b276660e5a01209d7b58159450e400cf636634a7a9c2cfd50a1c6675d01"


@dataclass(frozen=True)
class PublicSuffixList:
    exact: frozenset[str]
    wildcard: frozenset[str]
    exceptions: frozenset[str]
    sha256: str

    @classmethod
    def from_bytes(cls, data: bytes) -> "PublicSuffixList":
        text = data.decode("utf-8")
        if (
            "// ===BEGIN PRIVATE DOMAINS===" not in text
            or "// ===END PRIVATE DOMAINS===" not in text
        ):
            raise ValueError("PSL must include private section")
        exact: set[str] = set()
        wildcard: set[str] = set()
        exceptions: set[str] = set()
        for line in text.splitlines():
            if not line.strip() or line.lstrip().startswith("//"):
                continue
            rule = line.split()[0]
            target = (
                exceptions if rule.startswith("!") else wildcard if rule.startswith("*.") else exact
            )
            target.add(normalize_host(rule.removeprefix("!").removeprefix("*.")))
        return cls(
            frozenset(exact),
            frozenset(wildcard),
            frozenset(exceptions),
            hashlib.sha256(data).hexdigest(),
        )

    def boundary(self, value: str) -> tuple[str, str | None, bool]:
        host = normalize_host(value)
        try:
            ipaddress.ip_address(host)
            return host, None, False
        except ValueError:
            pass
        labels = host.split(".")
        length, known = 1, False
        for index in range(len(labels)):
            suffix = ".".join(labels[index:])
            if suffix in self.exceptions:
                length, known = len(labels) - index - 1, True
                break
            if suffix in self.exact:
                length, known = max(length, len(labels) - index), True
            if index > 0 and suffix in self.wildcard:
                length, known = max(length, len(labels) - index + 1), True
        registrable = ".".join(labels[-length - 1 :]) if len(labels) > length else None
        return ".".join(labels[-length:]), registrable, known

    def is_www_pair(self, source: str, target: str) -> bool:
        try:
            source, target = normalize_host(source), normalize_host(target)
        except ValueError:
            return False
        if source != "www." + target and target != "www." + source:
            return False
        _, left, left_known = self.boundary(source)
        _, right, right_known = self.boundary(target)
        return bool(left_known and right_known and left and left == right)


@lru_cache(maxsize=1)
def load_bundled_psl() -> PublicSuffixList:
    """One immutable, integrity-checked package snapshot per process; never a DNS cache."""
    data = files("geo_audit").joinpath("data/public_suffix_list.dat").read_bytes()
    if hashlib.sha256(data).hexdigest() != PSL_SHA256:
        raise ValueError("bundled PSL integrity check failed")
    return PublicSuffixList.from_bytes(data)
