# Mocking external services

For code that talks to Kafka, cloud storage, databases, HTTP APIs, schedulers or the environment, and is tested with `unittest.mock`.

## Contents
- Environment variables and config
- Context managers
- Sequences and errors with side_effect
- Files and config text
- Asserting calls
- Reducing patch noise
- Coverage gates

## Environment variables and config

Replace the whole environment so the test never depends on the developer's shell:

```python
@patch.dict(os.environ, {"KAFKA_BROKERS": "b1,b2", "SASL_USER": "u"}, clear=True)
```

`monkeypatch.setenv` does the same per test. Use the exact variable names the code reads. A misspelled name (`"DB_PASSWORD)"`) silently makes the code get `None`, and a test that then asserts `password=None` locks in the bug. After writing the test, check that every key you set is read by the code.

Patch config lookups (`Variable.get`, `Config.get`) at the module that calls them, with `return_value` when every call gets the same answer.

## Context managers

A `with client() as c:` block uses `__enter__`. Mock the inner object through it:

```python
mock_client.return_value.__enter__.return_value.get_page.side_effect = [...]
inner = mock_client.return_value.__enter__.return_value
```

`MagicMock` supports `__enter__`, `__exit__` and `__iter__` out of the box. For iteration use `mock.__iter__.return_value = [msg1, msg2]`. Use `namedtuple` or `SimpleNamespace` for message objects instead of nested mocks.

## Sequences and errors with side_effect

- A list returns one item per call: `side_effect=[page1, page2]`.
- An exception class or instance in the list is raised on that call: `side_effect=[page1, NotFound("x")]`.
- A callable computes the answer from the arguments, which is clearer than a long positional list: `side_effect=lambda key: {"a": 1}[key]`.
- Avoid multiplied lists (`[...] * 10`) that line up with an internal call order you do not control, for example patching `os.getenv` with ten identical blocks. Patch at a higher seam (the function that reads the settings) or use a dict-backed callable.
- If the list runs out, the mock raises `StopIteration`. Treat that as the code calling more times than you expected.

## Files and config text

- For code that opens a file, prefer `tmp_path` and write a real file. Use `mock_open(read_data=...)` only when the path is hard-wired.
- A large YAML or JSON string pasted into several tests belongs in a module constant or a fixture. Change it once.
- Patch `builtins.open` only for the module under test: `patch("pkg.module.open", mock_open(read_data=...), create=True)`. A global patch also affects imports, loggers and the test runner.

## Asserting calls

- Assert arguments, not just that a call happened:
  - `mock.assert_called_once_with(...)` for one call.
  - `mock.call_args_list == [call(...), call(...)]` when the sequence matters.
  - `mock.assert_has_calls([...], any_order=True)` when it does not.
- `mock.called`, `mock.call_count` and `mock.assert_called()` are real checks, but weak.
- `mock.is_called`, `mock.is_not_called` and `mock.called_once` are not Mock features. Any unknown attribute on a `Mock` returns a new truthy child mock, so `assert mock.is_called` always passes. Misspelled `assert_*` helpers did the same on old Python versions. Use `spec`/`autospec` to make such typos raise.
- When a test asserts one very long list of expected calls (dozens of near-identical entries), build the expectation with a comprehension or helper so the shape is readable. Keep a short literal list for the cases that matter.
- Assert on what the code sends out (the payload, the SQL, the rows), because that is the behavior. Do not assert how many internal helpers it called.

## Reducing patch noise

- Patch the lowest seam that removes the external call. Patch `KafkaProducer`, not the five helpers around it.
- Group repeated patches into a fixture:

```python
@pytest.fixture
def fake_env(monkeypatch):
    monkeypatch.setenv("KAFKA_BROKERS", "b1")
    monkeypatch.setenv("SASL_USER", "u")

@pytest.fixture
def producer():
    with patch("pkg.kafka.KafkaProducer") as m:
        yield m
```

- Name each mock argument for what it replaces. Use `_` for patches you only need for isolation, and place them last with `*_`.
- Silence a pylint `too-many-arguments` warning by reducing mocks, not by disabling the check, unless the repo already does that.

## Coverage gates

Some repos fail CI under a coverage threshold (`--cov-fail-under=100`). Coverage proves that lines ran, not that behavior was checked.

- A test with no assert, written only to execute lines, passes coverage and protects nothing. Add at least one assertion on a return value or a call argument.
- Cover every branch with its own case (`if`, `except`, early return), and assert something different in each.
- Keep exclusions (`omit`, `# pragma: no cover`) for genuinely untestable glue, such as DAG definitions and entry points, and keep logic out of those files.
- Run `pytest --cov=<package> --cov-report=term-missing` to see the missing lines, then write a test for the behavior, not for the line.
