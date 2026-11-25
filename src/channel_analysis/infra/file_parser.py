import json
import os
import subprocess
import sys

from channel_analysis.core.configs import ChannelAnalysisConfigs
from channel_analysis.core.errors import FileUnreadableError
from channel_analysis.infra import file_text
from channel_analysis.infra.file_kinds import kind_of


def extract_text_isolated(name: str, filetype: str, data: bytes, max_chars: int) -> str:
    """Return the text of a file. A PDF or a workbook is parsed in a child process with memory, CPU and time limits, because a
    file under the download limit can still unpack to enough to exhaust the worker; plain text needs no parser and stays here.

    The child gets no secrets (only PATH and the import path), so a parser exploit finds none in its environment. It is not a
    filesystem or network sandbox: that needs the operating system (a container profile, seccomp), which these limits don't replace.

    Raises:
        FileUnreadableError: the file can't be read, or the parser went over its limits.
    """
    if kind_of(name, filetype) in ChannelAnalysisConfigs.PLAIN_KINDS:
        return file_text.extract_text(name, filetype, data, max_chars)
    try:
        done = subprocess.run(
            [sys.executable, "-m", "channel_analysis.infra.file_text", name, filetype, str(max_chars)],
            input=data,
            # The answer is on stdout (bounded by `max_chars`). stderr is never read, and a damaged PDF can make the parser warn for
            # its whole CPU budget, which would pile up in this process, outside the child's memory limit.
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=ChannelAnalysisConfigs.PARSE_TIMEOUT_SECONDS,
            env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": os.pathsep.join(sys.path), "PYTHONDONTWRITEBYTECODE": "1"},
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise FileUnreadableError(f"Reading `{name}` took longer than {ChannelAnalysisConfigs.PARSE_TIMEOUT_SECONDS} s, so I stopped.") from exc
    try:
        result = json.loads(done.stdout)
    except ValueError as exc:
        # Killed by a limit (memory, CPU) or crashed: there is no answer to read.
        raise FileUnreadableError(f"I couldn't read `{name}` within my memory and time limits.") from exc
    if "error" in result:
        raise FileUnreadableError(result["error"])
    return str(result["text"])
