# anetbbs/core/protocols.py
from typing import Protocol, Dict, Any
import asyncio

class SessionProtocol(Protocol):
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    user: Dict[str, Any]

    async def read_line(self, prompt: str = '') -> str:
        ...

    async def write(self, data: str) -> None:
        ...

    async def clear_screen(self) -> None:
        ...
