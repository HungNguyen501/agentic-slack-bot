from unittest.mock import MagicMock, patch

import httpx
import pytest

from common.configs import GithubConfigs
from connectors import github


def test_graphql_posts_the_query_to_the_graphql_endpoint_and_returns_the_body():
    response = MagicMock()
    response.json.return_value = {"data": {"r0": None}, "errors": [{"type": "NOT_FOUND"}]}

    with (
        patch("connectors.github.httpx.post", autospec=True, return_value=response) as post,
        patch("connectors.github._headers", autospec=True, return_value={"Authorization": "Bearer t"}),
    ):
        body = github.graphql("query { viewer { login } }")

    post.assert_called_once_with(GithubConfigs.GRAPHQL_API, headers={"Authorization": "Bearer t"}, json={"query": "query { viewer { login } }"}, timeout=30.0)
    assert body == {"data": {"r0": None}, "errors": [{"type": "NOT_FOUND"}]}


def test_graphql_raises_on_an_http_error():
    response = MagicMock()
    response.raise_for_status.side_effect = httpx.HTTPStatusError("401", request=MagicMock(), response=MagicMock(status_code=401))

    with (
        patch("connectors.github.httpx.post", autospec=True, return_value=response),
        patch("connectors.github._headers", autospec=True, return_value={}),
        pytest.raises(httpx.HTTPStatusError),
    ):
        github.graphql("query { x }")
