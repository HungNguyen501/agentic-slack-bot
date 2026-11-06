import asyncio
import time
from unittest.mock import patch

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from agent.forms.access_request_options import FormOptions
from receiver import app

OPTIONS = FormOptions(locations_by_company={"Eaze": ["Eaze"]}, market_codes=["CA"], company_keys=["eaze_com"], tags=["transaction"])


@pytest.fixture
def form_redis():
    with patch("receiver.app.form_redis", autospec=True) as form_redis:
        yield form_redis


def test_a_button_with_a_snapshot_loads_its_options(form_redis):
    form_redis.get.return_value = OPTIONS.to_json().encode()

    assert app._load_form_options({"options_token": "tok"}) == OPTIONS
    form_redis.get.assert_called_once_with("access_form_options:tok")


def test_a_button_without_a_token_has_no_options_and_never_touches_redis(form_redis):
    assert app._load_form_options({}) is None
    form_redis.get.assert_not_called()


def test_an_expired_snapshot_counts_as_no_options(form_redis):
    form_redis.get.return_value = None

    assert app._load_form_options({"options_token": "tok"}) is None


def test_an_unreadable_snapshot_counts_as_no_options_instead_of_failing_the_click(form_redis):
    form_redis.get.return_value = b'{"tags": []}'

    assert app._load_form_options({"options_token": "tok"}) is None


def test_the_form_lookup_uses_its_own_client_with_short_timeouts_and_no_retries():
    pool = app.form_redis.connection_pool.connection_kwargs

    assert pool["socket_timeout"] == app._FORM_OPTIONS_DEADLINE_SECONDS
    assert pool["socket_connect_timeout"] == app._FORM_OPTIONS_DEADLINE_SECONDS
    assert app.form_redis is not app.redis_conn
    assert app.form_redis.get_retry().get_retries() == 0


def test_a_lookup_within_the_deadline_returns_the_options():
    with patch("receiver.app._load_form_options", autospec=True, return_value=OPTIONS):
        assert asyncio.run(app._form_options_within_deadline({"options_token": "tok"})) == OPTIONS


def test_a_lookup_that_runs_past_the_deadline_fails_promptly_instead_of_waiting_for_redis():
    async def timed_lookup():
        started = time.monotonic()
        with pytest.raises(app.FormOptionsUnavailableError):
            await app._form_options_within_deadline({"options_token": "tok"})
        return time.monotonic() - started  # measured inside the loop: asyncio.run itself later waits for the sleeping thread

    with patch("receiver.app._FORM_OPTIONS_DEADLINE_SECONDS", 0.05), patch("receiver.app._load_form_options", autospec=True, side_effect=lambda value: time.sleep(0.5)):
        assert asyncio.run(timed_lookup()) < 0.4


def test_a_redis_error_becomes_a_lookup_failure_rather_than_a_server_error():
    with (
        patch("receiver.app._load_form_options", autospec=True, side_effect=RedisConnectionError("down")),
        pytest.raises(app.FormOptionsUnavailableError, match="down"),
    ):
        asyncio.run(app._form_options_within_deadline({"options_token": "tok"}))
