"""Request decoding that never echoes parser or decompressor messages.

connect-python reports a body it cannot decode as `unknown` carrying the parser's own text (HTTP 500 over
Connect). The generated applications receive these wrappers instead of the default codecs and compressions,
so a malformed body becomes `invalid_argument` with a stable code. Errors the library already types, such as
the size limit, pass through unchanged.
"""

from __future__ import annotations

from typing import Any

from connectrpc.code import Code
from connectrpc.codec import Codec, proto_binary_codec, proto_json_codec
from connectrpc.compression import Compression
from connectrpc.compression.gzip import GzipCompression
from connectrpc.errors import ConnectError

MALFORMED_CODE = "malformed-request"


def malformed(error: Exception) -> ConnectError:
    return error if isinstance(error, ConnectError) else ConnectError(Code.INVALID_ARGUMENT, MALFORMED_CODE)


class StrictCodec:
    """Delegates to a codec; any decoding failure is reported as a malformed request."""

    def __init__(self, inner: Codec) -> None:
        self._inner = inner

    def name(self) -> str:
        return self._inner.name()

    def encode(self, message: Any) -> bytes:
        return self._inner.encode(message)

    def decode(self, data: bytes | bytearray, message_class: type[Any]) -> Any:
        try:
            return self._inner.decode(data, message_class)
        except Exception as error:  # noqa: BLE001 - parser messages must not reach the caller
            raise malformed(error) from error


class StrictCompression:
    """Delegates to a compression; corrupt input is reported as a malformed request."""

    def __init__(self, inner: Compression) -> None:
        self._inner = inner

    def name(self) -> str:
        return self._inner.name()

    def compress(self, data: bytes | bytearray | memoryview) -> bytes:
        return self._inner.compress(data)

    def decompress(self, data: bytes | bytearray | memoryview, read_max_bytes: int | None = None) -> bytes:
        try:
            return self._inner.decompress(data, read_max_bytes)
        except Exception as error:  # noqa: BLE001 - decompressor messages must not reach the caller
            raise malformed(error) from error


def request_codecs() -> list[Codec]:
    """The library defaults (binary and JSON Protocol Buffers), wrapped."""
    return [StrictCodec(proto_binary_codec()), StrictCodec(proto_json_codec())]


def request_compressions() -> list[Compression]:
    """The library default (gzip; identity is always added), wrapped."""
    return [StrictCompression(GzipCompression())]
