"""
Simple file-based persistence for MRC bridge service.
Stores user profiles and session data.
"""
import asyncio
import json
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional


class BridgeDB:
    def __init__(self, data_dir: str = 'data'):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.profiles_file = self.data_dir / 'profiles.json'
        self.sessions_file = self.data_dir / 'sessions.json'

        self._profiles = self._load_json(self.profiles_file) or {}
        self._sessions = self._load_json(self.sessions_file) or {}

    def _load_json(self, filepath: Path) -> Optional[dict]:
        if filepath.exists():
            try:
                with open(filepath, 'r') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return {}
        return {}

    def _save_json(self, filepath: Path, data: dict):
        # Real gap found in a security/performance audit: this used
        # to write directly to filepath, not atomically -- a crash or
        # kill mid-write leaves a truncated/invalid JSON file behind,
        # which _load_json() above silently treats as {} on the NEXT
        # start, losing every user's session/profile state at once
        # rather than just the one write in progress. Writing to a
        # sibling temp file first and os.replace()-ing it into place
        # means the real file is only ever fully-written JSON or the
        # previous good version -- never a partial write, since
        # os.replace() is atomic on both POSIX and Windows.
        tmp_path = filepath.with_suffix(filepath.suffix + '.tmp')
        with open(tmp_path, 'w') as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, filepath)

    def save_profile(self, handle: str, data: dict):
        """Synchronous -- blocks the calling thread on disk I/O. Only
        call this from a genuinely synchronous context (e.g. a script,
        or tests); async code (main.py's aiohttp handlers) must use
        save_profile_async() instead -- see its docstring."""
        self._profiles[handle] = {**data, 'updated_at': datetime.utcnow().isoformat()}
        self._save_json(self.profiles_file, self._profiles)

    async def save_profile_async(self, handle: str, data: dict):
        """Real gap found in a security/performance audit: every save
        here is a FULL rewrite of the entire profiles.json file (cost
        grows with the total number of stored profiles, not just the
        one being changed), and main.py's aiohttp WebSocket handlers
        used to call the synchronous save_profile() directly -- a
        blocking disk write executed right on the asyncio event loop
        stalls EVERY other concurrently-connected MRC client for its
        duration, and that duration only grows as more profiles
        accumulate over the bridge's lifetime. The in-memory dict
        mutation happens immediately (cheap, GIL-atomic); only the
        actual file write is offloaded to a thread pool executor so
        the event loop stays responsive. A snapshot (shallow copy) is
        handed to the executor rather than the live dict, since
        another coroutine could otherwise mutate self._profiles
        concurrently while the executor thread is still iterating it
        for json.dump() (a real "dictionary changed size during
        iteration" hazard, not just a style preference)."""
        self._profiles[handle] = {**data, 'updated_at': datetime.utcnow().isoformat()}
        snapshot = dict(self._profiles)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._save_json, self.profiles_file, snapshot)

    def get_profile(self, handle: str) -> Optional[dict]:
        return self._profiles.get(handle)

    def save_session(self, session_id: str, data: dict):
        """Synchronous -- see save_profile()'s docstring; async code
        must use save_session_async() instead."""
        self._sessions[session_id] = {**data, 'updated_at': datetime.utcnow().isoformat()}
        self._save_json(self.sessions_file, self._sessions)

    async def save_session_async(self, session_id: str, data: dict):
        """Async, executor-offloaded write -- see save_profile_async()'s
        docstring for the full rationale (identical shape, sessions.json
        instead of profiles.json)."""
        self._sessions[session_id] = {**data, 'updated_at': datetime.utcnow().isoformat()}
        snapshot = dict(self._sessions)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._save_json, self.sessions_file, snapshot)

    def get_session(self, session_id: str) -> Optional[dict]:
        return self._sessions.get(session_id)

    def delete_session(self, session_id: str):
        """Synchronous -- see save_profile()'s docstring; async code
        must use delete_session_async() instead."""
        if session_id in self._sessions:
            del self._sessions[session_id]
            self._save_json(self.sessions_file, self._sessions)

    async def delete_session_async(self, session_id: str):
        """Async, executor-offloaded write -- see save_profile_async()'s
        docstring for the full rationale."""
        if session_id in self._sessions:
            del self._sessions[session_id]
            snapshot = dict(self._sessions)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._save_json, self.sessions_file, snapshot)

    def list_profiles(self) -> Dict[str, dict]:
        return self._profiles.copy()

    def list_sessions(self) -> Dict[str, dict]:
        return self._sessions.copy()