"""What kind of file a name and a Slack filetype add up to. One answer, used to read a file, to page it and to say what it is."""

PLAIN_KINDS = frozenset({"csv", "tsv", "txt", "text", "json", "log", "md", "markdown", "yaml", "yml", "xml", "sql"})
SUPPORTED_KINDS = PLAIN_KINDS | {"pdf", "xlsx"}


def kind_of(name: str, filetype: str) -> str:
    """The file's kind: its extension if that is one we read, else Slack's filetype if that is, else whichever of the two there is.

    An extension we don't know (`report.pdf.backup`) doesn't override a filetype we do.
    """
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    slack_type = filetype.lower()
    if extension in SUPPORTED_KINDS:
        return extension
    if slack_type in SUPPORTED_KINDS:
        return slack_type
    return extension or slack_type
