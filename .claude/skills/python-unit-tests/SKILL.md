---
name: python-unit-tests
description: Writes and reviews Python unit tests with pytest - layout, naming, parametrize, pytest.raises, fixtures, mocking and patching of external services, time and filesystem isolation, coverage gates, plus PySpark/Delta tests. Use when asked to add, fix, review or structure unit tests for Python code, or to raise test coverage. Do NOT use for integration or end-to-end test design, or for general Python style (use python-code-style).
metadata:
  always: 'false'
---

# Python unit tests (pytest)

## Workflow

1. **Match the repo first.** Read the pytest config (`pyproject.toml`, `pytest.ini`), `conftest.py` files and two or three existing test files. Follow their layout, fixtures and docstring habits over the defaults below.
2. **List behaviors before writing:** happy path, edge cases (empty, boundary, `None`), and each way it can fail. One test per behavior.
3. **Write the tests.** Use the rules below.
   - PySpark or Delta code: read [references/pyspark.md](references/pyspark.md) first.
   - Code that calls Kafka, cloud storage, databases, HTTP APIs, Airflow variables or the environment through `unittest.mock`: read [references/mocking-external-services.md](references/mocking-external-services.md) first.
4. **Run only the new tests** (`pytest path/to/test_file.py -k name`). Then break the code under test on purpose, or invert an assertion, and confirm the test fails. A test that cannot fail is worthless.
5. **Run the surrounding test directory** to catch shared-state leaks.

Never change the code under test to make a test pass, unless the test found a real bug. Say so when it does.

## Layout and naming

- Mirror the source tree: `src/billing/invoice.py` is tested in `tests/billing/test_invoice.py`.
- A mirrored tree repeats basenames (`tests/jobs/hubspot/test_etl.py`, `tests/jobs/qlik/test_etl.py`). Default `prepend` import mode imports both as `test_etl` and aborts with "import file mismatch". Set `addopts = ["--import-mode=importlib"]` under `[tool.pytest.ini_options]`: no `__init__.py` files and no per-directory upkeep. Test files then cannot import each other, so shared helpers go in `conftest.py` or fixtures. If the repo instead uses `__init__.py` markers, they must cover `tests/` and every subdirectory. Markers only below `tests/` create a top-level package that can shadow a same-named package in `src/`. Keep the repo's working setup.
- Name tests `test_<function>_<scenario>`: `test_parse_duration_rejects_zero`. The name must say what breaks when the test fails.
- Group related tests in a plain function list. Use a class only to share a fixture or marker.
- Put shared fixtures in `conftest.py`. Do not import fixtures from helper modules into test files, because linters flag the unused import and the coupling is hidden.
- One-line docstring per test is optional. If the repo uses `Happy path - ...` / `Sad path - ...` labels, keep doing so.

## Structure of a test

Arrange, act, assert, in that order, separated by blank lines.

- One behavior per test. Several asserts are fine when they check one outcome.
- No loops, conditionals or `try/except` inside tests. Use `parametrize` for tables.
- Assert on observable behavior (return value, raised error, written output), not on internals.
- Floats: `pytest.approx`. Unordered results: compare sorted lists or sets.

```python
@pytest.mark.parametrize("value, seconds", [("15m", 900), ("2h", 7200), (" 1D ", 86400)])
def test_parse_duration(value, seconds):
    assert parse_duration(value) == seconds


@pytest.mark.parametrize("value", ["", "15", "0h", "1w", "-1h"])
def test_parse_duration_rejects_invalid(value):
    with pytest.raises(ValueError, match="Invalid duration"):
        parse_duration(value)
```

- Add `ids=[...]` to `parametrize` when the values do not explain themselves.
- Valid and invalid inputs go in separate parametrized tests.

## Errors

- `pytest.raises(SomeError, match="regex")`. `match` uses `re.search`, so wrap literal text in `re.escape(...)` if it contains `(`, `.`, `[` and similar.
- Keep only the failing call inside the `with` block.
- Never write `try: ... except Exception: pass` in a test. It passes when nothing is raised.

## Mocks and patches

- Patch where the name is looked up, not where it is defined. If `reports/mailer.py` does `from smtp_client import send`, patch `reports.mailer.send`.
- Prefer `monkeypatch` or `unittest.mock.patch` as a context manager or decorator, so the patch always unwinds.
- Use `autospec=True` (or `create_autospec`) so a call with the wrong signature fails the test.
- Decorator `@patch` arguments arrive bottom-up: the decorator closest to the function is the first argument.
- Assert the interaction that matters: `mock.assert_called_once_with(...)`. Avoid asserting chains like `m.return_value.filter.return_value.select.call_args`. They break on any refactor. Inject a small fake or restructure the code instead.
- More than about three patches in one test means the code under test has too many collaborators. Inject dependencies or test a smaller unit.
- Do not mock the thing under test or simple value objects.

## Time, files, environment

- Time: inject the clock or use `freezegun.freeze_time`. Never compare with the real `datetime.now()`.
- Files: use `tmp_path` (or `tmp_path_factory` for session fixtures). Never write to a hardcoded `/tmp/...` or the working directory, and never hand-write `shutil.rmtree` cleanup in each test.
- Environment variables, cwd, attributes: `monkeypatch.setenv`, `monkeypatch.chdir`, `monkeypatch.setattr`.
- No real network, cloud or database calls in unit tests.
- Config files shipped in the repo can get one test that loads and validates all of them. That is a cheap guard against bad edits.

## Fixtures

- Default scope is function. Use `session` or `module` only for expensive, read-only resources (a Spark session). A shared fixture that tests mutate causes order-dependent failures.
- Put setup and teardown in one fixture with `yield`, not copy-pasted at the top of each test.
- Name a fixture for what it provides (`spark`, `sample_invoice`). Use `@pytest.fixture(name="x")` when the function name would shadow the argument.

## Review checklist

When reviewing tests, flag: loops or ifs in the body, swallowed exceptions, vague names, `exc.value.args[0] == "..."` instead of `match=`, patches at the wrong path, hardcoded paths, real clock, unsorted `collect()` comparisons, one test covering several unrelated behaviors, tests that pass with the code under test deleted.

## Gotchas

- Two test functions with the same name in one module silently shadow each other. Only the last one runs.
- A test function without the `test_` prefix, or a file not named `test_*.py` or `*_test.py`, is never collected.
- An `assert` on a tuple (`assert (a, b)`) is always true.
- `assert mock.is_called` is always true. It is not a Mock feature, so the attribute is auto-created and truthy. The same holds for any misspelled helper. Use `assert_called_once_with(...)`, and `autospec` or `spec=True` to make typos raise.
- A test with no assertion, written to satisfy a coverage threshold, protects nothing.
- Check that a test locks in correct behavior. If expected values look wrong (`password=None` after setting a password variable), the code or the test setup has a bug.
- A test that reaches for production config or credentials is not a unit test. Pass the values in.
