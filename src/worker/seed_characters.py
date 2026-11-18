"""Seed the characters table from the files shipped in the image: `python -m worker.seed_characters`, run when a worker starts.

It only inserts characters whose name is missing, so it never changes a row an admin added or edited, and several workers
starting together is harmless. A character an admin deleted comes back on the next deploy if it has a seed file; disable it
(`enabled = false`) to keep it out.
"""
import logging

from characters.core.errors import CharacterStorageError, InvalidSeedError
from worker import wiring

log = logging.getLogger("worker.seed_characters")


def main() -> None:
    """Seed the table and log the outcome. A problem is logged, never raised: the worker must start without characters."""
    try:
        result = wiring.character_service().seed()
    except InvalidSeedError as exc:
        log.error("Character seed files are invalid, nothing was seeded:\n%s", exc)
    except CharacterStorageError as exc:
        log.error("Could not seed the characters: %s", exc)
    else:
        log.info("Seeded characters: added %s, skipped %d already stored", list(result.added) or "none", len(result.skipped))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
