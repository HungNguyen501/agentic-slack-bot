"""The block of the system prompt that gives an agent reply a character's voice. Pure, no I/O."""
from characters.core.configs import CharactersConfigs
from characters.core.models import Character


def build_voice_block(character: Character) -> str:
    """The text to append to the system prompt after the skills."""
    return f"{CharactersConfigs.VOICE_RULES}\n\n{character.persona.strip()}"
