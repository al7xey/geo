"""Incremental bounded content decoding; never call HTTPX automatic decompression."""

import zlib

from .errors import problem


class LimitedBody:
    def __init__(self, encoding: str, encoded_limit: int, decoded_limit: int):
        encoding = encoding.strip().lower()
        if encoding not in {"", "identity", "gzip", "deflate"}:
            raise problem(
                "CONTENT_ENCODING_UNSUPPORTED", "content", "Unsupported content encoding."
            )
        self.decoder = (
            zlib.decompressobj(31 if encoding == "gzip" else 15)
            if encoding in {"gzip", "deflate"}
            else None
        )
        self.encoded_limit, self.decoded_limit = encoded_limit, decoded_limit
        self.encoded = bytearray()
        self.decoded = (
            self.encoded if self.decoder is None and encoded_limit == decoded_limit else bytearray()
        )
        self.received_bytes = 0

    def feed(self, data: bytes) -> None:
        self.received_bytes += len(data)
        remaining = self.encoded_limit - len(self.encoded)
        self.encoded.extend(data[:remaining])
        if len(data) > remaining:
            raise problem("CONTENT_TOO_LARGE", "content", "Encoded body limit exceeded.")
        if self.decoded is self.encoded:
            return  # identity with equal bounds needs one buffer and no transformation.
        try:
            chunk = (
                self.decoder.decompress(data, self.decoded_limit - len(self.decoded) + 1)
                if self.decoder
                else data
            )
            room = self.decoded_limit - len(self.decoded)
            self.decoded.extend(chunk[:room])
            if len(chunk) > room or self.decoder and self.decoder.unconsumed_tail:
                raise problem("CONTENT_TOO_LARGE", "content", "Decoded body limit exceeded.")
            if self.decoder and self.decoder.unused_data:
                raise problem(
                    "CONTENT_ENCODING_UNSUPPORTED",
                    "content",
                    "Multiple/trailing compressed members are unsupported.",
                )
        except zlib.error:
            raise problem("CONTENT_INCOMPLETE", "content", "Corrupt compressed body.") from None

    def finish(self) -> None:
        if self.decoder is not None and not self.decoder.eof:
            raise problem("CONTENT_INCOMPLETE", "content", "Truncated compressed body.")
