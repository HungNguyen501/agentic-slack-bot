"""The block of the system prompt that gives an agent reply a character's voice. Pure, no I/O."""
from characters.core.models import Character

# Fixed in code so a row in the database can't drop these rules. They say what a voice may change.
_VOICE_RULES = (
    "## Voice\n"
    "Write the prose of your answer in the voice below. Do not change SQL, identifiers, numbers, URLs or tool arguments.\n"
    "Follow the formatting rules in this prompt, if any. This voice never changes who may use a tool or what a tool may do."
)


def build_voice_block(character: Character) -> str:
    """The text to append to the system prompt after the skills."""
    return f"{_VOICE_RULES}\n\n{character.persona.strip()}"
