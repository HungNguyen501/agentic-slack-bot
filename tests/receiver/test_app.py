from unittest.mock import patch

import pytest

from agent.forms.access_request_options import FormOptions
from receiver import app

OPTIONS = FormOptions(locations_by_company={"Eaze": ["Eaze"]}, market_codes=["CA"], company_keys=["eaze_com"], tags=["transaction"])


@pytest.fixture
def redis_conn():
    with patch("receiver.app.redis_conn", autospec=True) as redis_conn:
        yield redis_conn


def test_a_button_with_a_snapshot_loads_its_options(redis_conn):
    redis_conn.get.return_value = OPTIONS.to_json().encode()

    assert app._load_form_options({"options_token": "tok"}) == OPTIONS
    redis_conn.get.assert_called_once_with("access_form_options:tok")


def test_a_button_without_a_token_has_no_options_and_never_touches_redis(redis_conn):
    assert app._load_form_options({}) is None
    redis_conn.get.assert_not_called()


def test_an_expired_snapshot_counts_as_no_options(redis_conn):
    redis_conn.get.return_value = None

    assert app._load_form_options({"options_token": "tok"}) is None


def test_an_unreadable_snapshot_counts_as_no_options_instead_of_failing_the_click(redis_conn):
    redis_conn.get.return_value = b'{"tags": []}'

    assert app._load_form_options({"options_token": "tok"}) is None
