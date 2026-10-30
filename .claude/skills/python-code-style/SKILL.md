---
name: python-code-style
description: Applies and reviews Python code style following the Google Python Style Guide - imports, naming, formatting, exceptions, defaults, type annotations and language-feature rules. Use when writing, reviewing or refactoring Python code for style and idiom. Do NOT use for docstring and comment content (use python-docstrings) or for rules a formatter or linter already enforces automatically.
metadata:
  always: 'false'
---

# Python code style (Google)

Source: Google Python Style Guide, sections 2 (language rules) and 3 (style rules). If the repo configures a formatter or linter (ruff, black, pyink), its output wins on whitespace and line length. Follow this skill for the judgment calls.

## Imports

- Import packages and modules, not individual classes or functions: `from mypkg import parser`, then `parser.parse(...)`. Exceptions: names from `typing`, `collections.abc` and `typing_extensions`.
- One import per line (except `typing` names). No wildcard imports. Use absolute imports, never relative.
- Group in this order, blank line between groups, each sorted lexicographically: `__future__`, standard library, third party, local.
- Use `import x as y` only for standard abbreviations (`np`, `pd`) or to resolve a name clash.

## Naming

| Kind | Style |
|---|---|
| Modules, packages | `lower_with_under` |
| Classes, exceptions, type aliases | `CapWords` |
| Functions, methods, variables, parameters | `lower_with_under` |
| Constants | `CAPS_WITH_UNDER` |
| Internal | single leading `_` |

- Do not put the type in a name (`user_names`, not `user_name_list`).
- Avoid single-character names except counters, `e` for exceptions, `f` for files. Avoid dashes in module names.
- Do not use `__double_leading_underscore` mangling.

## Errors and validation

- Never use a bare `except:` or catch `Exception` unless re-raising or at an isolation boundary that logs it. Never swallow errors with `pass`.
- Keep `try` bodies minimal. Use `finally` for cleanup.
- Raise built-in exceptions where they fit (`ValueError` for a bad value, `TypeError` for a bad type). Do not use `assert` to validate inputs or API usage. `assert` is for internal invariants and tests.
- Custom exceptions inherit from an existing exception class and end in `Error`.

## Defaults and state

- Never use mutable default arguments (`[]`, `{}`). Default to `None` and create the object in the body.
- Avoid mutable module-level state. Module constants are fine.
- Use context managers (`with`) for files, locks and connections.

## Language features

- Truthiness: use `if items:` not `if len(items) > 0:`. Compare to `None` with `is None` / `is not None`. Never write `== False`. Do not use `if x:` when `x` may legitimately be `0` or empty and you mean "is set".
- Comprehensions only when simple: one `for` clause and at most one `if`. Otherwise use a loop.
- Lambdas only for one-liners. Prefer a named function otherwise.
- Conditional expressions (`a if cond else b`) only when short and on one line.
- Prefer f-strings for formatting. In logging calls pass `%s` patterns and arguments instead of an f-string.
- Avoid power features: metaclasses, `__getattr__` tricks, bytecode or import hacks.
- Use `@property` only for cheap, unsurprising access. Do not wrap trivial attributes.
- Guard scripts with `if __name__ == '__main__':`.

## Type annotations

- Annotate public APIs. Use `X | None` (or `Optional[X]` for older Pythons), built-in generics (`list[int]`), and `collections.abc` for abstract types.
- Do not annotate `self` or `cls`. Do not annotate obvious locals.
- Use `from __future__ import annotations` only if the supported Python version needs it.

## Formatting

- 4 spaces, never tabs. Maximum line length 80 (URLs and long imports excepted). No semicolons and no backslash continuation, use parentheses.
- Two blank lines between top-level definitions, one between methods.
- Trailing commas in multi-line collections, and a trailing comma forces one item per line.
- Functions longer than about 40 lines deserve a look for splitting. Do not split if it hurts readability.
- Single or double quotes are fine, but be consistent within a file.

## Gotchas

- Do not enforce 80 columns if the repo's configured limit differs (for example ruff at 88 or 100).
- Reviewing: report only real violations and keep fixes minimal. Do not rewrite unrelated code.
- `from x import y` where `y` is a module is fine. The rule is about importing classes and functions.
- Do not demand type annotations on private throwaway scripts.
