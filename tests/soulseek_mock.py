"""Minimal local Soulseek server for tests.

Implements just enough of the server protocol for two aioslsk clients on
localhost to log in, search each other and transfer files directly.
Nothing here talks to the real Soulseek network.
"""
from __future__ import annotations

import asyncio
import struct

from aioslsk.protocol.messages import (AddUser, CannotConnect, ConnectToPeer, FileSearch, GetPeerAddress,
                                       GetUserStats, GetUserStatus, Login, ServerMessage, SetListenPort)
from aioslsk.protocol.primitives import UserStats

ONLINE = 2
BAD_PASSWORD = "wrong-password"


class MockServer:
    def __init__(self):
        self.users: dict[str, dict] = {}
        self.searches: list[tuple[str, str]] = []
        self.server: asyncio.base_events.Server | None = None
        self.port = 0

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self):
        for u in list(self.users.values()):
            u["writer"].close()
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    @staticmethod
    async def _send(writer: asyncio.StreamWriter, msg) -> None:
        writer.write(msg.serialize())
        await writer.drain()

    def _stats(self) -> UserStats:
        return UserStats(avg_speed=500_000, uploads=10, shared_file_count=1, shared_folder_count=1)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        name = None
        try:
            while True:
                hdr = await reader.readexactly(4)
                body = await reader.readexactly(struct.unpack("<I", hdr)[0])
                try:
                    msg = ServerMessage.deserialize_request(hdr + body)
                except Exception:
                    continue  # messages we don't implement are ignored
                if isinstance(msg, Login.Request):
                    if msg.password == BAD_PASSWORD:
                        await self._send(writer, Login.Response(success=False, reason="INVALIDPASS"))
                        continue
                    name = msg.username
                    self.users[name] = {"writer": writer, "port": 0}
                    await self._send(writer, Login.Response(success=True, greeting="mock", ip="127.0.0.1",
                                                            md5hash="0" * 32, privileged=False))
                elif isinstance(msg, SetListenPort.Request) and name:
                    self.users[name]["port"] = msg.port
                elif isinstance(msg, GetPeerAddress.Request):
                    u = self.users.get(msg.username)
                    await self._send(writer, GetPeerAddress.Response(
                        msg.username, "127.0.0.1" if u else "0.0.0.0", u["port"] if u else 0))
                elif isinstance(msg, FileSearch.Request) and name:
                    self.searches.append((name, msg.query))
                    for other, u in list(self.users.items()):
                        if other != name:
                            await self._send(u["writer"], FileSearch.Response(name, msg.ticket, msg.query))
                elif isinstance(msg, AddUser.Request):
                    exists = msg.username in self.users
                    await self._send(writer, AddUser.Response(
                        msg.username, exists, ONLINE if exists else None, self._stats() if exists else None))
                elif isinstance(msg, GetUserStatus.Request):
                    await self._send(writer, GetUserStatus.Response(
                        msg.username, ONLINE if msg.username in self.users else 0, False))
                elif isinstance(msg, GetUserStats.Request):
                    await self._send(writer, GetUserStats.Response(msg.username, self._stats()))
                elif isinstance(msg, ConnectToPeer.Request) and name:
                    target = self.users.get(msg.username)
                    if target:
                        await self._send(target["writer"], ConnectToPeer.Response(
                            name, msg.typ, "127.0.0.1", self.users[name]["port"], msg.ticket, False))
                    else:
                        await self._send(writer, CannotConnect.Response(msg.ticket))
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            if name and self.users.get(name, {}).get("writer") is writer:
                del self.users[name]
            writer.close()


if __name__ == "__main__":
    async def _main():
        s = MockServer()
        print("mock Soulseek server on port", await s.start())
        await asyncio.Event().wait()

    asyncio.run(_main())
