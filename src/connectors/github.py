"""GitHub REST API client for the data-platform repo — auth, file/branch/PR primitives, no business logic."""
import base64
import json
import time

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from common.configs import Configs, GithubConfigs


_TOKEN_REFRESH_MARGIN_SECONDS = 60
_installation_token: tuple[str, float] | None = None


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _app_jwt() -> str:
    """Sign a short-lived RS256 JWT identifying the GitHub App (used only to mint installation tokens)."""
    now = int(time.time())
    # iat is backdated 60 s to tolerate clock drift, per GitHub's docs.
    claims = {"iat": now - 60, "exp": now + 9 * 60, "iss": Configs.GITHUB_APP_ID}
    signing_input = f"{_b64url(json.dumps({'alg': 'RS256', 'typ': 'JWT'}).encode())}.{_b64url(json.dumps(claims).encode())}"
    key = serialization.load_pem_private_key(Configs.GITHUB_APP_PRIVATE_KEY.replace("\\n", "\n").encode("utf-8"), password=None)
    signature = key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{_b64url(signature)}"


def _app_installation_token() -> str:
    """Return a cached GitHub App installation access token, minting a new one shortly before expiry."""
    global _installation_token
    if _installation_token and _installation_token[1] - time.time() > _TOKEN_REFRESH_MARGIN_SECONDS:
        return _installation_token[0]

    app_headers = {"Authorization": f"Bearer {_app_jwt()}", "Accept": "application/vnd.github+json"}
    installation_id = Configs.GITHUB_APP_INSTALLATION_ID
    if not installation_id:
        lookup = httpx.get(f"{GithubConfigs.API}/installation", headers=app_headers, timeout=30.0)
        lookup.raise_for_status()
        installation_id = str(lookup.json()["id"])

    resp = httpx.post(f"https://api.github.com/app/installations/{installation_id}/access_tokens", headers=app_headers, timeout=30.0)
    resp.raise_for_status()
    data = resp.json()
    expires_at = time.mktime(time.strptime(data["expires_at"], "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
    _installation_token = (data["token"], expires_at)
    return data["token"]


def _headers() -> dict:
    if Configs.GITHUB_APP_ID and Configs.GITHUB_APP_PRIVATE_KEY:
        token = _app_installation_token()
    else:
        token = Configs.GIT_REPO_PAT_DATA_PLATFORM
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


def get_file(path: str, ref: str) -> tuple[str, str]:
    """Fetch a file's decoded content and blob sha at a given ref (branch, tag, or commit)."""
    resp = httpx.get(f"{GithubConfigs.API}/contents/{path}", headers=_headers(), params={"ref": ref}, timeout=30.0)
    resp.raise_for_status()
    data = resp.json()
    return base64.b64decode(data["content"]).decode("utf-8"), data["sha"]


def ensure_branch(branch: str) -> None:
    """Create `branch` off the base branch's current commit if it doesn't already exist."""
    check = httpx.get(f"{GithubConfigs.API}/git/ref/heads/{branch}", headers=_headers(), timeout=30.0)
    if check.status_code == 200:
        return

    base = httpx.get(f"{GithubConfigs.API}/git/ref/heads/{GithubConfigs.BASE_BRANCH}", headers=_headers(), timeout=30.0)
    base.raise_for_status()
    base_sha = base.json()["object"]["sha"]

    resp = httpx.post(
        f"{GithubConfigs.API}/git/refs",
        headers=_headers(),
        json={"ref": f"refs/heads/{branch}", "sha": base_sha},
        timeout=30.0,
    )
    resp.raise_for_status()


def update_file(path: str, branch: str, content: str, message: str) -> None:
    """Create or overwrite a file on `branch` with `content` (a single commit on that branch)."""
    try:
        _, sha = get_file(path, branch)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise
        sha = None

    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha

    resp = httpx.put(f"{GithubConfigs.API}/contents/{path}", headers=_headers(), json=payload, timeout=30.0)
    resp.raise_for_status()


def find_open_pull_request(branch: str) -> tuple[str, int] | None:
    """Return the (html_url, number) of an already-open PR for this branch, if one exists."""
    resp = httpx.get(
        f"{GithubConfigs.API}/pulls",
        headers=_headers(),
        params={"head": f"{GithubConfigs.OWNER}:{branch}", "state": "open"},
        timeout=30.0,
    )
    resp.raise_for_status()
    prs = resp.json()
    return (prs[0]["html_url"], prs[0]["number"]) if prs else None


def create_pull_request(title: str, head: str, body: str) -> str:
    """Open a PR from `head` into the base branch and return its html_url."""
    resp = httpx.post(
        f"{GithubConfigs.API}/pulls",
        headers=_headers(),
        json={"title": title, "head": head, "base": GithubConfigs.BASE_BRANCH, "body": body},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()["html_url"]


def update_pull_request(number: int, title: str, body: str) -> None:
    """Overwrite an open PR's title and body."""
    resp = httpx.patch(f"{GithubConfigs.API}/pulls/{number}", headers=_headers(), json={"title": title, "body": body}, timeout=30.0)
    resp.raise_for_status()
