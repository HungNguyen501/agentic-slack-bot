# Sphinx style

Sphinx style uses reStructuredText field lists. Each field starts with a colon, and types get their own `:type:` and `:rtype:` fields.

```python
def restock(items: dict[str, int], name: str, quantity: int) -> int:
    """Adds stock for one item and returns its new count.

    :param items: Current stock, keyed by item name.
    :type items: dict[str, int]
    :param name: The item to restock.
    :type name: str
    :param quantity: How many units to add. Must be positive.
    :type quantity: int
    :returns: The item's count after restocking.
    :rtype: int
    :raises ValueError: If quantity is zero or negative.
    """
```

Rules:
- Field order: `:param:` and `:type:` pairs, `:returns:`, `:rtype:`, `:raises:`.
- One field per line, no blank lines between fields.
- Name the exception class directly after `:raises`.
- Describe class attributes with `:ivar name:` fields in the class docstring, not in `__init__`.
- Summary mood: match the file. Without a precedent, use the mood the Google default uses.
