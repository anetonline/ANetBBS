"""
Simple file-based persistence for MRC bridge service.
Stores user profiles and session data.
"""
import json
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
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)

    def save_profile(self, handle: str, data: dict):
        self._profiles[handle] = {**data, 'updated_at': datetime.utcnow().isoformat()}
        self._save_json(self.profiles_file, self._profiles)

    def get_profile(self, handle: str) -> Optional[dict]:
        return self._profiles.get(handle)

    def save_session(self, session_id: str, data: dict):
        self._sessions[session_id] = {**data, 'updated_at': datetime.utcnow().isoformat()}
        self._save_json(self.sessions_file, self._sessions)

    def get_session(self, session_id: str) -> Optional[dict]:
        return self._sessions.get(session_id)

    def delete_session(self, session_id: str):
        if session_id in self._sessions:
            del self._sessions[session_id]
            self._save_json(self.sessions_file, self._sessions)

    def list_profiles(self) -> Dict[str, dict]:
        return self._profiles.copy()

    def list_sessions(self) -> Dict[str, dict]:
        return self._sessions.copy()