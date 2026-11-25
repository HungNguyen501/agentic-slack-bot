from slack_sdk.errors import SlackApiError

from channel_analysis.core.configs import ChannelAnalysisConfigs
from channel_analysis.core.errors import ChannelAnalysisError, FileUnreadableError
from channel_analysis.core.models import FileMeta
from channel_analysis.infra import file_parser
from channel_analysis.infra.file_kinds import kind_of
from channel_analysis.infra.slack_history import slack_access_errors
from connectors import slack


def _shared_in(raw: dict) -> frozenset[str]:
    ids = set(raw.get("channels") or []) | set(raw.get("groups") or [])
    for kind in ("public", "private"):
        ids |= set((raw.get("shares") or {}).get(kind) or {})
    return frozenset(ids)


class SlackChannelFiles:
    def __init__(self) -> None:
        # One forked process serves one agent run, which reads a file page by page: keep the last file's text instead of
        # downloading and extracting it again for every page.
        self._last: tuple[str, str] | None = None

    def describe(self, file_id: str, token: str) -> FileMeta:
        raw = self._info(file_id, token)
        name = str(raw.get("name") or raw.get("title") or file_id)
        return FileMeta(
            name=name,
            filetype=kind_of(name, str(raw.get("filetype") or "unknown")),
            size=int(raw.get("size") or 0),
            shared_in=_shared_in(raw),
        )

    def text(self, file_id: str, token: str) -> str:
        if self._last and self._last[0] == file_id:
            return self._last[1]
        raw = self._info(file_id, token)
        is_html = kind_of(str(raw.get("name") or ""), str(raw.get("filetype") or "")) in ChannelAnalysisConfigs.HTML_KINDS
        url = raw.get("url_private_download") or raw.get("url_private")
        if not url:
            raise FileUnreadableError("Slack gave me no download link for that file.")
        try:
            data = slack.download_file(url, token=token, max_bytes=ChannelAnalysisConfigs.FILE_MAX_BYTES, allow_html=is_html)
        except slack.FileDownloadError as exc:
            raise FileUnreadableError(str(exc)) from exc
        text = file_parser.extract_text_isolated(str(raw.get("name") or ""), str(raw.get("filetype") or ""), data, ChannelAnalysisConfigs.FILE_MAX_CHARS)
        self._last = (file_id, text)
        return text

    @staticmethod
    def _info(file_id: str, token: str) -> dict:
        try:
            with slack_access_errors(files=True):
                return slack.get_file_info(file_id, token=token)
        except SlackApiError as exc:
            if exc.response and exc.response.get("error") in ("file_not_found", "file_deleted"):
                raise ChannelAnalysisError("I can't find that file.") from exc
            raise
