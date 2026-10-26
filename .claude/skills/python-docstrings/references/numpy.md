# NumPy style

Section headings are underlined with hyphens of the same length. Each parameter has its own `name : type` line, with the description indented below. Put one space on each side of the colon.

```python
def restock(items: dict[str, int], name: str, quantity: int) -> int:
    """Adds stock for one item and returns its new count.

    Parameters
    ----------
    items : dict[str, int]
        Current stock, keyed by item name.
    name : str
        The item to restock.
    quantity : int
        How many units to add. Must be positive.

    Returns
    -------
    int
        The item's count after restocking.

    Raises
    ------
    ValueError
        If quantity is zero or negative.
    """
```

Rules:
- Section order: `Parameters`, `Returns` (or `Yields` for generators), `Raises`.
- Keep the types in the section even when the signature is annotated. That is the NumPy convention.
- Describe class attributes in an `Attributes` section of the class docstring, not in `__init__`.
- Summary mood: match the file. Without a precedent, use the mood the Google default uses.
