"""Running the extraction model over the chunks of an analysis and counting what comes back. Knows nothing of Slack or access."""
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from channel_analysis.core import extraction, limits
from channel_analysis.core.errors import ExtractionError, ExtractionShapeError
from channel_analysis.core.models import ExtractionItem, ExtractionSpec, FailedChunk, GroupCount, Record
from channel_analysis.core.ports import ExtractionModel


@dataclass(frozen=True)
class ChunkOutcome:
    """What extracting one chunk gave: its records, or why it failed; the cost either way."""

    items: list[ExtractionItem]
    records: list[Record]
    rejected: int
    failure: str | None
    prompt_tokens: int
    completion_tokens: int
    seconds: float


@dataclass(frozen=True)
class Totals:
    """What the extracted records come to, before they are put in an Analysis."""

    failed: tuple[FailedChunk, ...]
    records: int
    rejected: int
    groups: tuple[GroupCount, ...]
    groups_omitted: int
    records_omitted: int
    sample: tuple[Record, ...]


def run_extraction(extractor: ExtractionModel, spec: ExtractionSpec, chunks: list[list[ExtractionItem]]) -> list[ChunkOutcome]:
    """Extract every chunk, `EXTRACT_PARALLELISM` at a time, and return the outcomes in the chunks' order."""
    if not chunks:
        return []
    with ThreadPoolExecutor(max_workers=min(limits.EXTRACT_PARALLELISM, len(chunks))) as pool:
        futures = [pool.submit(extract_chunk, extractor, spec, chunk) for chunk in chunks]
        return [future.result() for future in futures]


def extract_chunk(extractor: ExtractionModel, spec: ExtractionSpec, chunk: list[ExtractionItem]) -> ChunkOutcome:
    """Ask the model for the records of one chunk. An answer of the wrong shape is asked for once more; a failed call, or a
    second wrong answer, makes the chunk a failed one (reported, not raised) so the other chunks still count."""
    system, user = extraction.build_system_prompt(spec), extraction.render_chunk(chunk)
    started = time.monotonic()
    prompt_tokens = completion_tokens = 0
    failure = "no answer"
    for _ in range(2):
        try:
            answer = extractor.complete_json(system, user, limits.CHUNK_TIMEOUT_SECONDS)
            prompt_tokens += answer.prompt_tokens
            completion_tokens += answer.completion_tokens
            records, rejected = extraction.read_records(answer.data, chunk, spec)
            return ChunkOutcome(chunk, records, rejected, None, prompt_tokens, completion_tokens, time.monotonic() - started)
        except ExtractionShapeError as exc:
            failure = str(exc)
        except ExtractionError as exc:
            failure = str(exc)
            break
    return ChunkOutcome(chunk, [], 0, failure, prompt_tokens, completion_tokens, time.monotonic() - started)


def count_outcomes(outcomes: list[ChunkOutcome], group_by: tuple[str, ...]) -> Totals:
    """Merge the chunks' records, drop duplicates, and count them by group."""
    failed = tuple(
        # The items run newest first, so a chunk's last item is its oldest message.
        FailedChunk(o.items[-1].ts, o.items[0].ts, len({item.ts for item in o.items}), o.failure)
        for o in outcomes
        if o.failure
    )
    records = extraction.drop_duplicates([record for o in outcomes for record in o.records])
    groups = extraction.count_groups(records, group_by)
    cap = limits.MAX_GROUPS_LISTED
    sample = sorted(records, key=lambda r: float(r.ts), reverse=True)[: limits.SAMPLE_RECORDS]
    return Totals(failed, len(records), sum(o.rejected for o in outcomes), groups[:cap], len(groups[cap:]), sum(g.records for g in groups[cap:]), tuple(sample))
