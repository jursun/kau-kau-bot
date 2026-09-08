"""Scouting + chat reactions (GG) and local early win on surrender/wipe."""

from sc2.bot_ai import BotAI
from sc2.data import race_townhalls
from s2clientprotocol import debug_pb2 as debug_pb
from s2clientprotocol import sc2api_pb2 as sc_pb

from bot.common.log import log_event

_ALL_TOWNHALLS = {th for ths in race_townhalls.values() for th in ths}


def _local_early_end_enabled() -> bool:
    """Local config only. Missing config (ladder zip) => False."""
    try:
        from config import LEAVE_ON_GG
        return bool(LEAVE_ON_GG)
    except ImportError:
        return False


class ScoutingManager:
    """Stub scouting with enemy GG / surrender handling."""

    def __init__(self, bot: BotAI):
        self.bot = bot
        self._end_handled = False
        self._enemy_townhalls_seen = False
        self._no_townhall_frames = 0

    async def execute(self) -> None:
        await self._watch_enemy_gg()
        await self._watch_computer_surrender()

    async def _declare_local_victory(self, reason: str) -> None:
        """End the match as a win locally. Ladder must never call this."""
        bot = self.bot
        if self._end_handled or not _local_early_end_enabled():
            return
        self._end_handled = True
        log_event(bot, f"LOCAL END WIN ({reason})")
        try:
            await bot.chat_send("gg")
        except Exception:
            pass
        await bot.client._execute(
            debug=sc_pb.RequestDebug(
                debug=[
                    debug_pb.DebugCommand(
                        end_game=debug_pb.DebugEndGame(
                            end_result=debug_pb.DebugEndGame.DeclareVictory
                        )
                    )
                ]
            )
        )

    async def _watch_enemy_gg(self) -> None:
        if self._end_handled or not _local_early_end_enabled():
            return
        bot = self.bot
        try:
            messages = bot.state.chat
        except Exception:
            return

        for msg in messages:
            if msg.player_id == bot.player_id:
                continue
            text = (msg.message or "").strip().lower()
            if "gg" in text or "surrender" in text:
                log_event(bot, f"ENEMY chat: {msg.message!r}")
                await self._declare_local_victory("enemy gg/surrender chat")
                return

    async def _watch_computer_surrender(self) -> None:
        """
        After expansion scouting finishes, if enemy townhalls stay gone while
        we still have an army, declare local victory.
        """
        if self._end_handled or not _local_early_end_enabled():
            return
        bot = self.bot

        enemy_ths = bot.enemy_structures.of_type(_ALL_TOWNHALLS)
        if enemy_ths:
            self._enemy_townhalls_seen = True
            self._no_townhall_frames = 0
            return

        if not self._enemy_townhalls_seen:
            return

        # Do not end while combat is still scouting for hidden townhalls
        if not getattr(bot, "expansion_scout_done", False):
            self._no_townhall_frames = 0
            return

        if bot.army_count <= 0:
            return

        self._no_townhall_frames += 1
        if self._no_townhall_frames >= 16:
            await self._declare_local_victory(
                f"enemy townhalls gone after scout (frames={self._no_townhall_frames})"
            )
