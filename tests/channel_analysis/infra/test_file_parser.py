import json
import resource
import subprocess
from unittest.mock import patch

import pytest

from channel_analysis.core.configs import ChannelAnalysisConfigs
from channel_analysis.core.errors import FileUnreadableError
from channel_analysis.infra import file_text
from channel_analysis.infra.file_parser import extract_text_isolated
from tests.support.channel_analysis_files import pdf_with_text, xlsx_with

MAX = 10_000


def test_plain_text_is_decoded_here_without_a_child_process():
    with patch("channel_analysis.infra.file_parser.subprocess.run", autospec=True) as run:
        assert extract_text_isolated("rows.csv", "csv", b"id\n1\n", MAX) == "id\n1\n"

    run.assert_not_called()


def test_a_pdf_is_read_in_a_child_process_that_gives_the_same_text():
    assert "Hello report" in extract_text_isolated("report.pdf", "pdf", pdf_with_text("Hello report"), MAX)


def test_a_workbook_is_read_in_a_child_process_that_gives_the_same_text():
    data = xlsx_with({"Counts": [["table", "rows"], ["a", 10]]})

    assert extract_text_isolated("counts.xlsx", "xlsx", data, MAX) == "## Sheet: Counts\ntable,rows\na,10\n"


def test_what_the_parser_says_about_a_file_comes_back_as_an_unreadable_file():
    with pytest.raises(FileUnreadableError, match="couldn't read that Excel"):
        extract_text_isolated("bad.xlsx", "xlsx", b"not a zip", MAX)


def completed(stdout: bytes, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=b"")


def test_a_parser_killed_by_a_limit_leaves_no_answer_and_is_an_unreadable_file():
    killed = patch("channel_analysis.infra.file_parser.subprocess.run", autospec=True, return_value=completed(b"", returncode=-9))
    with killed, pytest.raises(FileUnreadableError, match="memory and time limits"):
        extract_text_isolated("big.pdf", "pdf", b"%PDF", MAX)


def test_a_parser_that_runs_too_long_is_stopped():
    timeout = subprocess.TimeoutExpired(cmd="parser", timeout=ChannelAnalysisConfigs.PARSE_TIMEOUT_SECONDS)
    too_slow = patch("channel_analysis.infra.file_parser.subprocess.run", autospec=True, side_effect=timeout)
    with too_slow, pytest.raises(FileUnreadableError, match="took longer than 30 s"):
        extract_text_isolated("slow.pdf", "pdf", b"%PDF", MAX)


def test_the_child_gets_the_file_on_stdin_a_timeout_and_the_parents_import_path():
    with patch("channel_analysis.infra.file_parser.subprocess.run", autospec=True, return_value=completed(json.dumps({"text": "x"}).encode())) as run:
        extract_text_isolated("a.pdf", "pdf", b"%PDF-data", 123)

    assert run.call_args.args[0][-4:] == ["channel_analysis.infra.file_text", "a.pdf", "pdf", "123"]
    assert run.call_args.kwargs["input"] == b"%PDF-data"
    assert run.call_args.kwargs["timeout"] == ChannelAnalysisConfigs.PARSE_TIMEOUT_SECONDS
    assert run.call_args.kwargs["env"]["PYTHONPATH"]


def test_the_child_gets_none_of_the_workers_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://user:password@db/x")
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY", "fernet-key")
    with patch("channel_analysis.infra.file_parser.subprocess.run", autospec=True, return_value=completed(json.dumps({"text": "x"}).encode())) as run:
        extract_text_isolated("a.pdf", "pdf", b"%PDF", 100)

    env = run.call_args.kwargs["env"]
    assert set(env) == {"PATH", "PYTHONPATH", "PYTHONDONTWRITEBYTECODE"}
    assert "sk-secret" not in str(env) and "password" not in str(env)


def test_the_child_lowers_its_memory_and_cpu_limits_before_it_reads_the_file():
    with patch("resource.setrlimit", autospec=True) as setrlimit:
        file_text._limit_resources()

    assert [c.args[0] for c in setrlimit.call_args_list] == [resource.RLIMIT_AS, resource.RLIMIT_CPU]
    assert setrlimit.call_args_list[0].args[1] == (ChannelAnalysisConfigs.PARSE_MEMORY_BYTES, ChannelAnalysisConfigs.PARSE_MEMORY_BYTES)


def test_a_platform_that_refuses_the_limits_still_parses():
    with patch("resource.setrlimit", autospec=True, side_effect=ValueError("not allowed")):
        file_text._limit_resources()


def test_the_childs_stderr_is_not_collected_so_a_warning_heavy_file_cannot_fill_the_workers_memory():
    with patch("channel_analysis.infra.file_parser.subprocess.run", autospec=True, return_value=completed(json.dumps({"text": "x"}).encode())) as run:
        extract_text_isolated("a.pdf", "pdf", b"%PDF", 100)

    kwargs = run.call_args.kwargs
    assert kwargs["stderr"] == subprocess.DEVNULL and kwargs["stdout"] == subprocess.PIPE and "capture_output" not in kwargs
