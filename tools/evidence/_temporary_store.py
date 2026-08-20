from __future__ import annotations

import sqlite3
from pathlib import Path

from agent_mem_bridge.storage import MemoryStore


class ScopedTemporaryMemoryStore(MemoryStore):
    """A temporary store that closes every connection it opens before cleanup."""

    def __init__(self, db_path: Path, log_dir: Path | None = None) -> None:
        self._connections: list[sqlite3.Connection] = []
        self._closed = False
        try:
            super().__init__(db_path=db_path, log_dir=log_dir)
        except BaseException as init_error:
            try:
                self.close()
            except BaseException as cleanup_error:
                raise cleanup_error from init_error
            raise

    @property
    def open_connection_count(self) -> int:
        return len(self._connections)

    def _connect(self) -> sqlite3.Connection:
        if self._closed:
            raise RuntimeError("temporary store is closed")
        connection = super()._connect()
        self._connections.append(connection)
        return connection

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        cleanup_error: BaseException | None = None
        try:
            for connection in self._connections:
                try:
                    connection.close()
                except BaseException as error:
                    if cleanup_error is None:
                        cleanup_error = error
        finally:
            self._connections.clear()
        if cleanup_error is not None:
            raise cleanup_error
