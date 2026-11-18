class DatasetNotSyncedError(Exception):
    """A dataset is unknown or has never been synced successfully, so there is nothing to read."""


class DatasetBusyError(Exception):
    """Another sync of the same dataset is already running."""


class DatasetFetchError(Exception):
    """The source query failed; the message is fit to show an admin."""


class DatasetSyncError(Exception):
    """A sync didn't replace the stored dataset; the message is fit to show an admin and the previous copy is intact."""


class DatasetSyncBusyError(DatasetSyncError):
    """Another sync of the same dataset is running, so this one did nothing."""
