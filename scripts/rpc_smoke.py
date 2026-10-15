"""Calls a running API with the three protocols: `python scripts/rpc_smoke.py http://127.0.0.1:8000`.

Checks the public status call and that a protected call without a token fails as UNAUTHENTICATED.
Native gRPC needs a server that speaks HTTP/2 (for example Hypercorn); Connect and gRPC-Web work over HTTP/1.1.
"""

from __future__ import annotations

import asyncio
import sys

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.protocol import ProtocolType
from pyqwest import Client, HTTPTransport, HTTPVersion

from app.rpc.gen.proteinforge.account.v1.account_service_connect import AccountServiceClient
from app.rpc.gen.proteinforge.account.v1.account_service_pb import GetMeRequest, GetStatusRequest

PROTOCOLS = {"connect": ProtocolType.CONNECT, "grpc-web": ProtocolType.GRPC_WEB, "grpc": ProtocolType.GRPC}


def http_client(name: str) -> Client:
    # Native gRPC over plain HTTP needs HTTP/2 with prior knowledge; the browser protocols use HTTP/1.1.
    return Client(HTTPTransport(http_version=HTTPVersion.HTTP2 if name == "grpc" else HTTPVersion.HTTP1))


async def check(address: str, name: str) -> str:
    async with AccountServiceClient(address, protocol=PROTOCOLS[name], http_client=http_client(name)) as client:
        status = await client.get_status(GetStatusRequest())
        try:
            await client.get_me(GetMeRequest())
            protected = "UNEXPECTED SUCCESS"
        except ConnectError as error:
            protected = "UNAUTHENTICATED" if error.code == Code.UNAUTHENTICATED else f"{error.code}: {error.message}"
    return f"{name}: status={status.status} version={status.version} identity={status.identity_configured} | GetMe without token -> {protected}"


async def main(address: str, names: list[str]) -> None:
    for name in names:
        try:
            print(await check(address, name))
        except Exception as error:  # noqa: BLE001 - report every protocol, even when one fails
            print(f"{name}: FAILED {type(error).__name__}: {error}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2:] or list(PROTOCOLS)))
