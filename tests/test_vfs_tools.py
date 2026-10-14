"""Unit tests for VFS tools."""

import json

import httpx
import pytest

from valstorm_mcp import proxy
from valstorm_mcp.tools.vfs import (
    vfs_ask,
    vfs_browse,
    vfs_delete_file,
    vfs_get_download_url,
    vfs_move,
    vfs_prepare_upload,
    vfs_read_text,
    vfs_search,
)


@pytest.mark.asyncio
async def test_vfs_search_success(mock_auth_context, monkeypatch):
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/search"
        return httpx.Response(200, json={
            "results": [
                {"id": "file_123", "name": "doc.md", "snippet": "important context", "score": 0.95}
            ]
        })

    monkeypatch.setattr(proxy, "proxy_client", proxy.ProxyClient(transport=httpx.MockTransport(mock_handler)))

    res = await vfs_search(query="important context", limit=5)
    parsed = json.loads(res)
    assert len(parsed["results"]) == 1
    assert parsed["results"][0]["id"] == "file_123"


@pytest.mark.asyncio
async def test_vfs_ask_rag_synthesis(mock_auth_context, monkeypatch):
    async def mock_stream_rag(query: str, vault_ids=None, limit=5):
        yield {"event": "intent", "data": {"intent": "rag_query"}}
        yield {"event": "ai_chunk", "data": {"delta": "Valstorm is "}}
        yield {"event": "ai_chunk", "data": {"delta": "an enterprise platform."}}
        yield {"event": "citations", "data": {"citations": [{"file_id": "file_001", "filename": "Overview.md"}]}}
        yield {"event": "done", "data": {}}

    mock_client = proxy.ProxyClient()
    monkeypatch.setattr(mock_client, "stream_rag", mock_stream_rag)
    monkeypatch.setattr(proxy, "proxy_client", mock_client)

    answer = await vfs_ask(query="What is Valstorm?")
    assert "Valstorm is an enterprise platform." in answer
    assert "Overview.md" in answer
    assert "file_001" in answer


@pytest.mark.asyncio
async def test_vfs_read_text_inline(mock_auth_context, monkeypatch):
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/vfs/file/file_abc"
        return httpx.Response(200, json={
            "id": "file_abc",
            "name": "notes.md",
            "delivery_type": "inline_text",
            "text_content": "# Hello World\nThis is a test doc."
        })

    monkeypatch.setattr(proxy, "proxy_client", proxy.ProxyClient(transport=httpx.MockTransport(mock_handler)))

    content = await vfs_read_text(file_id="file_abc")
    assert content == "# Hello World\nThis is a test doc."


@pytest.mark.asyncio
async def test_vfs_read_text_truncation(mock_auth_context, monkeypatch):
    large_text = "x" * 500
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "id": "file_large",
            "name": "large.txt",
            "delivery_type": "inline_text",
            "text_content": large_text
        })

    monkeypatch.setattr(proxy, "proxy_client", proxy.ProxyClient(transport=httpx.MockTransport(mock_handler)))

    content = await vfs_read_text(file_id="file_large", max_bytes=100)
    assert len(content) > 100
    assert "[... Truncated: document exceeded 100 bytes limit ...]" in content


@pytest.mark.asyncio
async def test_vfs_read_text_presigned_fallback(mock_auth_context, monkeypatch):
    def mock_api_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "id": "file_remote",
            "name": "remote.md",
            "extension": "md",
            "delivery_type": "presigned_url",
            "url": "https://s3.valstorm.com/presigned_remote.md"
        })

    monkeypatch.setattr(proxy, "proxy_client", proxy.ProxyClient(transport=httpx.MockTransport(mock_api_handler)))

    orig_async_client = httpx.AsyncClient
    class MockS3Client(orig_async_client):
        async def get(self, url, **kwargs):
            if "presigned_remote.md" in str(url):
                return httpx.Response(200, text="# S3 Content Loaded")
            return await super().get(url, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", MockS3Client)

    content = await vfs_read_text(file_id="file_remote")
    assert content == "# S3 Content Loaded"


@pytest.mark.asyncio
async def test_vfs_prepare_upload(mock_auth_context):
    res = await vfs_prepare_upload(filename="document.pdf", vault_id="vaul_999")
    parsed = json.loads(res)
    assert parsed["status"] == "ready"
    assert parsed["vault_id"] == "vaul_999"
    assert "curl" in parsed["terminal_recipe"]
    assert "vaul_999" in parsed["terminal_recipe"]


@pytest.mark.asyncio
async def test_vfs_get_download_url(mock_auth_context, monkeypatch):
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "id": "file_xyz",
            "name": "report.pdf",
            "delivery_type": "presigned_url",
            "url": "https://s3.valstorm.com/presigned_download_link",
            "file_size": 2048
        })

    monkeypatch.setattr(proxy, "proxy_client", proxy.ProxyClient(transport=httpx.MockTransport(mock_handler)))

    res = await vfs_get_download_url(file_id="file_xyz")
    parsed = json.loads(res)
    assert parsed["file_id"] == "file_xyz"
    assert parsed["download_url"] == "https://s3.valstorm.com/presigned_download_link"
    assert "curl" in parsed["terminal_recipe"]


@pytest.mark.asyncio
async def test_vfs_delete_file_safety_guard_and_execution(mock_auth_context, monkeypatch):
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/vfs/file_123"
        return httpx.Response(200, json={"deleted": True})

    monkeypatch.setattr(proxy, "proxy_client", proxy.ProxyClient(transport=httpx.MockTransport(mock_handler)))

    # Without confirm=True
    res_unconfirmed = await vfs_delete_file(file_id="file_123", confirm=False)
    assert json.loads(res_unconfirmed)["status"] == "confirmation_required"

    # With confirm=True
    res_confirmed = await vfs_delete_file(file_id="file_123", confirm=True)
    assert json.loads(res_confirmed)["status"] == "deleted"


@pytest.mark.asyncio
async def test_vfs_browse_and_move(mock_auth_context, monkeypatch):
    def mock_handler(request: httpx.Request) -> httpx.Response:
        if "/vfs/vault/" in request.url.path:
            return httpx.Response(200, json={"vault_id": "vaul_1", "folders": [], "files": []})
        elif "/vfs/move" in request.url.path:
            return httpx.Response(200, json={"success": True})
        return httpx.Response(200, json={})

    monkeypatch.setattr(proxy, "proxy_client", proxy.ProxyClient(transport=httpx.MockTransport(mock_handler)))

    browse_res = await vfs_browse(target="vaul_1", browse_type="vault")
    assert json.loads(browse_res)["vault_id"] == "vaul_1"

    move_res = await vfs_move(item_id="file_1", to_vault_id="vaul_2")
    assert json.loads(move_res)["status"] == "moved"


@pytest.mark.asyncio
async def test_vfs_tools_proxy_error_handling(mock_auth_context, monkeypatch):
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "VFS Service error"})

    monkeypatch.setattr(proxy, "proxy_client", proxy.ProxyClient(transport=httpx.MockTransport(mock_handler)))

    res_search = await vfs_search(query="test")
    assert "VFS Service error" in res_search

    res_read = await vfs_read_text(file_id="f1")
    assert "VFS Service error" in res_read

    res_dl = await vfs_get_download_url(file_id="f1")
    assert "VFS Service error" in res_dl

    res_del = await vfs_delete_file(file_id="f1", confirm=True)
    assert "VFS Service error" in res_del


@pytest.mark.asyncio
async def test_vfs_scope_rejection(readonly_auth_context):
    with pytest.raises(PermissionError) as exc_info:
        await vfs_delete_file(file_id="file_123", confirm=True)
    assert "Missing required scope 'vfs:delete'" in str(exc_info.value)
