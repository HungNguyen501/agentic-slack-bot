import pytest

from common.configs import ReminderConfig


def test_reminder_config_round_trips_through_its_dict():
    config = ReminderConfig("channel", "C1", "1.0", lookback_hours=72, created_by="U1")

    assert ReminderConfig.from_dict(config.to_dict()) == config


@pytest.mark.parametrize(
    "data",
    [
        {"origin_channel": "C1", "origin_thread_ts": "1.0"},
        {"scope": "thread", "origin_thread_ts": "1.0"},
        {"scope": "everything", "origin_channel": "C1", "origin_thread_ts": "1.0"},
        {"scope": "channel", "origin_channel": "C1", "origin_thread_ts": "1.0"},
        {"scope": "channel", "origin_channel": "C1", "origin_thread_ts": "1.0", "lookback_hours": "72"},
        {"scope": "channel", "origin_channel": "C1", "origin_thread_ts": "1.0", "lookback_hours": 72.5},
        {"scope": "channel", "origin_channel": "C1", "origin_thread_ts": "1.0", "lookback_hours": True},
        {},
    ],
    ids=["no-scope", "no-origin-channel", "unknown-scope", "channel-without-lookback", "string-lookback", "float-lookback", "bool-lookback", "empty"],
)
def test_reminder_config_from_dict_rejects_malformed_data(data):
    with pytest.raises(ValueError):
        ReminderConfig.from_dict(data)


@pytest.mark.parametrize(
    "config, label",
    [
        (ReminderConfig("thread", "C1", "1.0"), "PR review reminder · thread scope"),
        (ReminderConfig("channel", "C1", "1.0", lookback_hours=72), "PR review reminder · channel scope, last 72h"),
    ],
)
def test_reminder_config_label(config, label):
    assert config.label() == label
