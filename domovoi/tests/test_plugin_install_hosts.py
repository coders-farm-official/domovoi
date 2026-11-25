"""A plugin installed from GitHub is downloaded from GitHub's hosts only:
a redirect to any other host is refused before it is requested
(domovoi/plugins_runtime/installer.py ``download_github_zip``)."""

from __future__ import annotations

import httpx
import pytest

from domovoi.plugins_runtime import installer
from domovoi.plugins_runtime.installer import InstallError


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["https://files.example.com/x.zip",
                                    "http://codeload.github.com/alex/plugin/zip/main"])
async def test_a_redirect_off_github_is_refused(monkeypatch, target: str) -> None:
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={"default_branch": "main"})
        if request.url.host == "codeload.github.com" and request.url.scheme == "https":
            return httpx.Response(302, headers={"location": target})
        return httpx.Response(200, content=b"PK")

    def client(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(answer), **kwargs)

    monkeypatch.setattr(installer.egress, "async_client", client)
    monkeypatch.setattr(installer.egress, "internet_turned_off", lambda: False)
    with pytest.raises(InstallError) as ei:
        await installer.download_github_zip("https://github.com/alex/plugin")
    assert ei.value.code == "github_download_failed"
    assert target not in seen
