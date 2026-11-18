from unittest.mock import create_autospec

import pytest
from redis import Redis, RedisError

from characters.core.errors import CharacterStorageError
from characters.infra.redis_thread_characters import RedisThreadCharacterStore


@pytest.fixture
def client():
    return create_autospec(Redis, instance=True)


@pytest.fixture
def store(client):
    return RedisThreadCharacterStore(client, 600)


def test_the_name_is_stored_per_channel_and_thread_with_the_ttl(store, client):
    store.set("C1", "1.5", "em-gai-mua")

    client.set.assert_called_once_with("thread_character:C1:1.5", "em-gai-mua", ex=600)


def test_the_name_is_read_back_decoded(store, client):
    client.get.return_value = b"em-gai-mua"

    assert store.get("C1", "1.5") == "em-gai-mua"
    client.get.assert_called_once_with("thread_character:C1:1.5")


def test_a_thread_without_a_character_gives_none(store, client):
    client.get.return_value = None

    assert store.get("C1", "1.5") is None


@pytest.mark.parametrize("call", ["get", "set"])
def test_a_redis_error_becomes_a_storage_error(store, client, call):
    client.get.side_effect = client.set.side_effect = RedisError("down")

    with pytest.raises(CharacterStorageError):
        store.get("C1", "1.5") if call == "get" else store.set("C1", "1.5", "a")
