# -*- coding: utf-8 -*-
import threading
import logging
import asyncio
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger("DotaGC")

# Integer values from EServerRegion protobuf
SERVER_REGIONS: dict[str, int] = {
    "sa":  7,   # South America
    "us":  2,   # US East
    "eu":  3,   # Europe
    "aus": 6,   # Australia
    "sg":  5,   # Singapore
    "cl":  16,  # Chile
    "pe":  17,  # Peru
}

# Integer values from DOTA_GameMode protobuf
GAME_MODES: dict[str, int] = {
    "ap":       1,  # All Pick
    "captains": 2,  # Captain's Mode
    "rd":       3,  # Random Draft
    "sd":       4,  # Single Draft
    "ar":       5,  # All Random
}

GAME_MODE_LABELS: dict[str, str] = {
    "ap":       "All Pick",
    "captains": "Captain's Mode",
    "rd":       "Random Draft",
    "sd":       "Single Draft",
    "ar":       "All Random",
}

REGION_LABELS: dict[str, str] = {
    "sa":  "South America",
    "us":  "US East",
    "eu":  "Europe",
    "aus": "Australia",
    "sg":  "Singapore",
    "cl":  "Chile",
    "pe":  "Peru",
}


@dataclass
class ActiveLobby:
    lobby_id: int
    name: str
    password: str
    server_region: str
    game_mode: str
    members: list = field(default_factory=list)


class DotaGCManager:
    def __init__(self, username: str, password: str, shared_secret: str | None = None):
        self.username = username
        self.password = password
        self.shared_secret = shared_secret

        self._steam = None
        self._dota = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._lobby_lock = threading.Lock()
        self.current_lobby: ActiveLobby | None = None

    # ─── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="DotaGCThread", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            from steam.client import SteamClient
            from dota2.client import Dota2Client

            self._steam = SteamClient()
            self._dota = Dota2Client(self._steam)
            self._register_handlers()

            if self.shared_secret:
                from steam.guard import SteamAuthenticator
                auth = SteamAuthenticator(secrets={"shared_secret": self.shared_secret})
                self._steam.login(
                    username=self.username,
                    password=self.password,
                    two_factor_code=auth.get_code(),
                )
            else:
                self._steam.login(username=self.username, password=self.password)

            self._steam.run_forever()

        except Exception:
            logger.exception("[DotaGC] Erro crítico na thread do GC")

    def _register_handlers(self) -> None:
        @self._steam.on("logged_on")
        def _on_logged_on():
            logger.info("[DotaGC] Steam OK — iniciando Dota GC")
            self._dota.launch()

        @self._dota.on("ready")
        def _on_ready():
            logger.info("[DotaGC] Dota GC pronto")
            self._ready.set()

        @self._dota.on("notready")
        def _on_not_ready():
            logger.warning("[DotaGC] Dota GC desconectado")
            self._ready.clear()

        @self._dota.on("lobby_new")
        def _on_lobby_new(lobby, *_):
            logger.info(f"[DotaGC] Lobby criado: id={lobby.lobby_id}")

        @self._dota.on("lobby_removed")
        def _on_lobby_removed(*_):
            logger.info("[DotaGC] Lobby encerrado")
            with self._lobby_lock:
                self.current_lobby = None

        @self._dota.on("lobby_changed")
        def _on_lobby_changed(lobby, *_):
            with self._lobby_lock:
                if self.current_lobby:
                    self.current_lobby.members = [m.name for m in lobby.members]

    # ─── properties ───────────────────────────────────────────────────────────

    @property
    def is_ready(self) -> bool:
        return self._ready.is_set()

    # ─── sync operations (called from thread pool) ────────────────────────────

    def _create_lobby_sync(
        self, name: str, password: str, game_mode_key: str, region_key: str
    ) -> ActiveLobby | None:
        if not self._ready.is_set() or self._dota is None:
            return None

        region_val = SERVER_REGIONS.get(region_key.lower(), SERVER_REGIONS["sa"])
        game_mode_val = GAME_MODES.get(game_mode_key.lower(), GAME_MODES["ap"])

        result_event = threading.Event()
        result: dict = {}

        def _on_lobby_new(lobby, *_):
            result["lobby"] = lobby
            result_event.set()

        self._dota.once("lobby_new", _on_lobby_new)
        self._dota.create_practice_lobby(
            password=password,
            options={
                "game_name": name,
                "server_region": region_val,
                "game_mode": game_mode_val,
                "allow_spectating": True,
                "visibility": 0,  # public
            },
        )

        if not result_event.wait(timeout=15):
            # Timed out — remove the handler to avoid leaks
            self._dota.off("lobby_new", _on_lobby_new)
            return None

        raw = result.get("lobby")
        if not raw:
            return None

        with self._lobby_lock:
            self.current_lobby = ActiveLobby(
                lobby_id=raw.lobby_id,
                name=name,
                password=password,
                server_region=region_key.lower(),
                game_mode=game_mode_key.lower(),
            )
            return self.current_lobby

    def _destroy_lobby_sync(self) -> bool:
        with self._lobby_lock:
            if not self._ready.is_set() or not self.current_lobby or self._dota is None:
                return False
            self._dota.destroy_lobby()
            self.current_lobby = None
            return True

    # ─── async wrappers ───────────────────────────────────────────────────────

    async def create_lobby(
        self,
        name: str,
        password: str,
        game_mode: str = "ap",
        region: str = "sa",
    ) -> ActiveLobby | None:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._create_lobby_sync, name, password, game_mode, region
        )

    async def destroy_lobby(self) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._destroy_lobby_sync)


# ─── module-level singleton ────────────────────────────────────────────────────

_manager: DotaGCManager | None = None


def get_gc_manager() -> DotaGCManager | None:
    return _manager


def init_gc_manager(
    username: str, password: str, shared_secret: str | None = None
) -> DotaGCManager:
    global _manager
    _manager = DotaGCManager(username, password, shared_secret)
    _manager.start()
    return _manager


def can_use_gc() -> bool:
    return bool(_manager and _manager.is_ready)
