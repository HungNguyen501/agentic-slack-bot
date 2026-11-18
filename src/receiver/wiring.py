"""Composition root of the receiver service."""
import functools

from bots.core.ports import BotRepository
from bots.infra.supabase_bots import SupabaseBotRepository


@functools.cache
def bot_repository() -> BotRepository:
    return SupabaseBotRepository()
