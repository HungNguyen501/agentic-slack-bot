import logging
from unittest.mock import patch

import pytest

from characters.core.errors import CharacterStorageError, InvalidSeedError
from characters.core.models import SeedResult
from worker import seed_characters


@pytest.fixture
def service():
    with patch("worker.seed_characters.wiring", autospec=True) as wiring:
        yield wiring.character_service.return_value


def test_main_seeds_the_missing_characters_and_logs_what_it_did(service, caplog):
    service.seed.return_value = SeedResult(added=("a", "b"), skipped=("c",))

    with caplog.at_level(logging.INFO, logger="worker.seed_characters"):
        seed_characters.main()

    service.seed.assert_called_once_with()
    assert "added ['a', 'b'], skipped 1 already stored" in caplog.text


def test_main_logs_when_nothing_was_added(service, caplog):
    service.seed.return_value = SeedResult(added=(), skipped=("a",))

    with caplog.at_level(logging.INFO, logger="worker.seed_characters"):
        seed_characters.main()

    assert "added none" in caplog.text


def test_invalid_seed_files_are_logged_and_never_stop_the_worker(service, caplog):
    service.seed.side_effect = InvalidSeedError(["a.md: name is required"])

    with caplog.at_level(logging.ERROR, logger="worker.seed_characters"):
        seed_characters.main()

    assert "invalid" in caplog.text and "a.md: name is required" in caplog.text


def test_an_unreachable_table_is_logged_and_never_stops_the_worker(service, caplog):
    service.seed.side_effect = CharacterStorageError("Could not store the characters: down")

    with caplog.at_level(logging.ERROR, logger="worker.seed_characters"):
        seed_characters.main()

    assert "Could not seed the characters" in caplog.text
