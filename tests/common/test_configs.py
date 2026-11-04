import pytest

from common.configs import ReminderConfig

VALID = {"scope": "thread", "origin_channel": "C1", "origin_thread_ts": "1.0", "mentions": ["U1", "U2"]}


def test_reminder_config_round_trips_through_its_dict():
    config = ReminderConfig("channel", "C1", "1.0", ("U1", "U2"), lookback_hours=72, created_by="U1")

    assert ReminderConfig.from_dict(config.to_dict()) == config


def test_reminder_config_round_trips_its_progress():
    config = ReminderConfig("thread", "C1", "1.0", ("U1",), has_reminded=True, last_reminded={"C1:1.0": 1730000000.5})

    assert ReminderConfig.from_dict(config.to_dict()) == config


def test_a_config_saved_without_progress_starts_with_none():
    config = ReminderConfig.from_dict(VALID)

    assert config.has_reminded is False
    assert config.last_reminded == {}


def test_reminder_config_dict_holds_mentions_as_a_json_list():
    assert ReminderConfig("thread", "C1", "1.0", ("U1", "U2")).to_dict()["mentions"] == ["U1", "U2"]


@pytest.mark.parametrize(
    "data",
    [
        {k: v for k, v in VALID.items() if k != "scope"},
        {k: v for k, v in VALID.items() if k != "origin_channel"},
        VALID | {"scope": "everything"},
        VALID | {"scope": "channel"},
        VALID | {"scope": "channel", "lookback_hours": "72"},
        VALID | {"scope": "channel", "lookback_hours": 72.5},
        VALID | {"scope": "channel", "lookback_hours": True},
        {k: v for k, v in VALID.items() if k != "mentions"},
        VALID | {"mentions": []},
        VALID | {"mentions": "U1"},
        VALID | {"mentions": ["U1", 7]},
        VALID | {"has_reminded": "yes"},
        VALID | {"has_reminded": 1},
        VALID | {"last_reminded": []},
        VALID | {"last_reminded": {"C1:1.0": "yesterday"}},
        VALID | {"last_reminded": {"C1:1.0": True}},
        {},
    ],
    ids=[
        "no-scope",
        "no-origin-channel",
        "unknown-scope",
        "channel-without-lookback",
        "string-lookback",
        "float-lookback",
        "bool-lookback",
        "no-mentions",
        "empty-mentions",
        "mentions-not-a-list",
        "mention-not-a-string",
        "has-reminded-string",
        "has-reminded-int",
        "last-reminded-not-a-dict",
        "last-reminded-string-time",
        "last-reminded-bool-time",
        "empty",
    ],
)
def test_reminder_config_from_dict_rejects_malformed_data(data):
    with pytest.raises(ValueError):
        ReminderConfig.from_dict(data)


@pytest.mark.parametrize(
    "config, label",
    [
        (ReminderConfig("thread", "C1", "1.0", ("U1",)), "PR review reminder · thread scope"),
        (ReminderConfig("channel", "C1", "1.0", ("U1",), lookback_hours=72), "PR review reminder · channel scope, last 72h"),
    ],
)
def test_reminder_config_label(config, label):
    assert config.label() == label
