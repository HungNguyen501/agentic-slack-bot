from functools import partial

import pytest

from bots.core.errors import BotNotFoundError
from bots.infra.supabase_bots import SupabaseBotRepository
from tests.support.db import fake_connect as shared_fake_connect

repo = SupabaseBotRepository()

ROW = {"id": "b1", "enabled_skills": None, "admin_users": ["U1"], "app_id": "A1", "bot_token": "xoxb-1", "signing_secret": "sig"}


fake_connect = partial(shared_fake_connect, "bots.infra.supabase_bots.connect")


def test_get_by_app_id_only_finds_active_bots_and_maps_the_row():
    with fake_connect(fetchone=ROW) as cur:
        bot = repo.get_by_app_id("A1")

    sql, params = cur.execute.call_args.args
    assert "b.app_id = %s AND b.active = TRUE" in sql and params == ("A1",)
    assert (bot.bot_id, bot.bot_token, bot.signing_secret, bot.enabled_skills, bot.admin_users, bot.app_id) == ("b1", "xoxb-1", b"sig", [], frozenset({"U1"}), "A1")


def test_get_by_app_id_returns_none_for_an_unknown_app():
    with fake_connect(fetchone=None):
        assert repo.get_by_app_id("nope") is None


def test_get_by_id_raises_a_core_error_for_an_unknown_or_inactive_bot():
    with fake_connect(fetchone=None), pytest.raises(BotNotFoundError, match="b9"):
        repo.get_by_id("b9")
