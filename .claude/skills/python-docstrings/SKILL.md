---
name: python-docstrings
description: Writes, fixes and finds missing Python docstrings in Google (default), NumPy or Sphinx style, including what to document and what to skip. Use when asked to document, add docstrings to, find missing docstrings in, or improve docstrings of a Python module, class, function, method or package. Do NOT use for general Python naming, imports or formatting (use python-code-style).
metadata:
  always: 'false'
---

# Python docstrings

Source: Google Python Style Guide, sections 3.8 (comments and docstrings) and 3.10 (TODOs).

## Workflow

1. **Choose the style**, in this order: the style the user names, the style of existing docstrings in the same file or package, then Google.
   - NumPy: read [references/numpy.md](references/numpy.md) before writing.
   - Sphinx (`:param:` fields): read [references/sphinx.md](references/sphinx.md) before writing.
   - Google: the rules below.
2. **Find the gaps** when asked to document a file or package: run `scripts/find_undocumented.py PATH...`. It prints one line per public module, class, function or method without a docstring and a summary line. Add `--all` to include private names. It skips `@overload` stubs and `@override` methods imported from `typing` or `typing_extensions` (aliases included) and property setters and deleters, which take no docstring of their own. Use `--all` to see everything.
3. **Write docstrings** for the listed definitions. Rewrite existing docstrings only when asked.
4. **Re-run the script** after editing and repeat step 3 until it reports `0 undocumented`. Report the final summary line and anything it skipped.
5. Change only docstrings. Leave code, signatures and comments as they are, then show the updated code, not a description of the changes.

For a single named function or class, skip steps 2 and 4.

The count only proves docstrings exist. Read the result for accuracy before finishing.

# Google style rules

## Format

- Use triple double quotes (`"""`), also for one-liners.
- Start with a one-line summary: at most 80 characters, ends with a period, `?` or `!`, fits on the first line.
- If more is needed, add a blank line, then the body, indented like the opening quotes.
- Use descriptive voice: "Fetches rows from the table.", not "Fetch rows...". If the file or package already uses imperative mood (PEP 257), match it. Consistency within a file wins.
- Put the closing quotes on their own line for multi-line docstrings.

## Functions and methods

A docstring is mandatory for public API, anything non-obvious and anything longer than about 5 lines. Skip it for tiny, obviously named private helpers.

Sections, in this order, only when they apply:

```python
def fetch_rows(table: str, limit: int = 100) -> list[dict]:
    """Fetches rows from a table.

    Longer explanation of behavior, only if the summary is not enough.

    Args:
        table: Name of the table to read.
        limit: Maximum number of rows to return. Defaults to 100.

    Returns:
        A list of dicts mapping column name to value. Empty if the table
        has no rows.

    Raises:
        ConnectionError: The database is unreachable.
    """
```

- `Args:` one entry per parameter as `name: description.` Include `*args` and `**kwargs` by those names. Wrapped lines get a 2 or 4 space hanging indent, consistent within the file.
- Add the type in parentheses (`name (int): ...`) only when the signature has no annotations, and then also name the type in `Returns:`. Do not add annotations to the code to avoid this.
- `Returns:` describes the value, not just its type. Omit it when the function returns `None` or the summary already says what it returns and the annotation gives the type. For tuples, describe each element.
- Generators use `Yields:` instead of `Returns:`.
- `Raises:` lists only exceptions that are part of the interface. Do not list exceptions raised for violating the documented contract (for example a `TypeError` on a wrong argument type).
- Properties: document like an attribute (`"""The Bigtable path."""`), not "Returns the path".
- A method decorated with `@override` needs no docstring unless its behavior differs materially from the parent. Then document what changed.
- Do not describe the implementation. Describe what callers can rely on.

## Classes

```python
class Reader:
    """Reads text files line by line.

    Attributes:
        path: Location of the file.
        encoding: Text encoding used when opening the file.
    """
```

- The class docstring says what an instance represents. Document public attributes under `Attributes:`.
- Put constructor arguments in the class docstring or in `__init__`, not both.

## Modules

Start each file with a docstring: summary line, then usage notes or examples when helpful. Test files and files with only a trivial `__main__` may omit it. Place license boilerplate before the docstring.

## Comments

- Comment tricky logic, not obvious code. Explain why, never restate what the code does.
- Block comments precede the code, at its indentation, with `# `. Inline comments sit at least 2 spaces after the code and start with `# `.
- TODO format: `# TODO: https://bug-link - Short explanation.`
- Never describe code to a reader assumed to be unfamiliar with Python.

## Gotchas

- Do not mix styles. One file uses one style, and an existing style beats the Google default.
- Do not repeat annotated types in `Args:`.
- `Returns:` for a generator is wrong. Use `Yields:`.
- A docstring that only restates the function name ("Gets the user.") adds nothing. Say what is returned, when, and any side effects.
- Update the docstring in the same change as the signature.
