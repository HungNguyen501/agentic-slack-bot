"""Test helpers for the characters feature, shared by its layers and by the features that use it."""
from characters.core.models import Character
from characters.core.validation import TemplateField, VoiceSpec

SPEC = VoiceSpec(
    fields={
        "headers": TemplateField(placeholders=frozenset({"who", "subject"}), required=frozenset({"who"}), one_of=frozenset({"subject"}), many=True),
        "footer": TemplateField(placeholders=frozenset({"schedule_id"}), required=frozenset({"schedule_id"})),
        "plain": TemplateField(),
    },
    mandatory=frozenset({"headers"}),
)
SPECS = {"pr_reminder": SPEC}
VOICE = {"headers": ["{who} {subject}"]}


def make_character(name="a", **overrides) -> Character:
    fields = {"display_name": name.upper(), "bindings": ("feature:pr_reminder",), "persona": "speak softly", "voices": {"pr_reminder": VOICE}} | overrides
    return Character(name=name, **fields)
