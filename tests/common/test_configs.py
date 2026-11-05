import pytest

from common.configs import ReminderConfig, ReminderMessages

VALID = {"scope": "thread", "origin_channel": "C1", "origin_thread_ts": "1.0", "mentions": ["U1", "U2"]}


def test_reminder_config_round_trips_through_its_dict():
    config = ReminderConfig("channel", "C1", "1.0", ("U1", "U2"), lookback_hours=72, created_by="U1")

    assert ReminderConfig.from_dict(config.to_dict()) == config


def test_reminder_config_round_trips_its_progress():
    config = ReminderConfig("thread", "C1", "1.0", ("U1",), expires_at=1730100000.0, last_reminded={"C1:1.0": 1730000000.5})

    assert ReminderConfig.from_dict(config.to_dict()) == config


def test_a_config_saved_without_progress_starts_with_none():
    config = ReminderConfig.from_dict(VALID)

    assert config.expires_at is None
    assert config.last_reminded == {}


def test_a_config_saved_by_an_older_version_with_has_reminded_still_loads():
    config = ReminderConfig.from_dict(VALID | {"has_reminded": True})

    assert config.expires_at is None
    assert "has_reminded" not in config.to_dict()


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
        VALID | {"expires_at": "tomorrow"},
        VALID | {"expires_at": True},
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
        "expires-at-string",
        "expires-at-bool",
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


@pytest.mark.parametrize("header", ReminderMessages.HEADERS, ids=lambda header: header[:30])
def test_every_header_mentions_the_people_and_names_the_prs(header):
    assert "{who}" in header
    assert "{subject}" in header or "{Subject}" in header


def test_there_are_several_headers_to_rotate_through():
    assert len(ReminderMessages.HEADERS) >= 2


def test_every_template_can_be_filled_with_the_values_message_py_passes():
    ReminderMessages.SUBJECT_MANY.format(count=3)
    ReminderMessages.AGE_MINUTES.format(n=5)
    ReminderMessages.AGE_HOURS.format(n=5)
    ReminderMessages.AGE_DAYS.format(n=5)
    ReminderMessages.WAITING_LINE.format(age="5 phút", flair="")
    ReminderMessages.SCHEDULE_FOOTER.format(schedule_id="3f2a", hours="07:00–22:00", offset=7)
    ReminderMessages.EXPIRED.format(schedule_id="3f2a")
    ReminderMessages.UNREADABLE.format(schedule_id="3f2a", error_code="not_in_channel")
    ReminderMessages.ACTIVE_HOURS_FOR_AGENT.format(hours="07:00–22:00", offset=7)
    for header in ReminderMessages.HEADERS:
        header.format(who="<@U1>", subject="cái PR này", Subject="Cái PR này")


def test_the_quip_thresholds_are_in_order_so_every_age_gets_at_most_one_quip():
    fresh_hours = ReminderMessages.FLAIR_FRESH_UNDER_HOURS
    stale_hours = ReminderMessages.FLAIR_STALE_AFTER_DAYS * 24
    ancient_hours = ReminderMessages.FLAIR_ANCIENT_AFTER_DAYS * 24

    assert 0 < fresh_hours < stale_hours < ancient_hours
