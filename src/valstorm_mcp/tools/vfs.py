"""Virtual File System (VFS) tools for Valstorm MCP Server delegating to valstorm-client SDK."""

import json
from typing import Any

import httpx

import valstorm_mcp.proxy as proxy_module
from valstorm.exceptions import ValstormHttpError
from valstorm_mcp.auth import get_active_auth_context, require_scope
from valstorm_mcp.config import config
from valstorm_mcp.proxy import ProxyClientError


async def vfs_search(query: str, limit: int = 10, vault_id: str | None = None) -> str:
    """Search files, metadata, and document vector chunks across the Valstorm workspace.

    Args:
        query: Search keywords, question, or filename (e.g. 'quarterly report', 'NDA template').
        limit: Maximum number of blended hits to return (default: 10, max: 50).
        vault_id: Optional specific Vault ID to restrict search scope.

    Returns:
        JSON string containing matched documents, snippets, scores, and file IDs.
    """
    require_scope("vfs:read")

    payload = {
        "query": query.strip(),
        "limit": min(max(1, limit), 50),
        "vault_id": vault_id,
        "mode": "hybrid",
    }

    try:
        data = await proxy_module.proxy_client.post("search", json_data=payload)
        return json.dumps(data, indent=2)
    except (ValstormHttpError, ProxyClientError) as e:
        status_code = getattr(e, "status_code", 500)
        return json.dumps({"error": str(e), "status_code": status_code})
    except Exception as e:
        return json.dumps({"error": f"Unexpected error during VFS search: {e!s}"})


async def vfs_ask(query: str, vault_id: str | None = None, limit: int = 5) -> str:
    """Ask natural language questions answered by workspace documents with server-side AI RAG synthesis.

    Args:
        query: Clear question to answer from workspace knowledge.
        vault_id: Optional Vault ID to restrict knowledge domain.
        limit: Number of document excerpts to feed into synthesis (default: 5).

    Returns:
        Synthesized markdown answer with source citations.
    """
    require_scope("vfs:read")

    vault_ids = [vault_id] if vault_id else None
    accumulated_text = ""
    citations: list[dict[str, Any]] = []

    try:
        async for msg in proxy_module.proxy_client.stream_rag(query=query.strip(), vault_ids=vault_ids, limit=limit):
            event = msg.get("event")
            data = msg.get("data", {})

            if event == "ai_chunk":
                delta = data.get("delta", "") if isinstance(data, dict) else str(data)
                accumulated_text += delta
            elif event == "citations":
                citations = data.get("citations", []) if isinstance(data, dict) else []
            elif event == "error":
                err_msg = data.get("message", "Unknown RAG error") if isinstance(data, dict) else str(data)
                return f"**Error executing RAG search:** {err_msg}"

        if not accumulated_text.strip():
            return "No relevant information found in your workspace documents to answer this question."

        output = accumulated_text.strip()
        if citations:
            output += "\n\n### Sources & Citations\n"
            for c in citations:
                fname = c.get("filename") or c.get("file_id") or "Document"
                fid = c.get("file_id", "")
                output += f"- **{fname}** (File ID: `{fid}`)\n"

        return output
    except Exception as e:
        return f"**Error during RAG synthesis:** {e!s}"


async def vfs_read_text(file_id: str, max_bytes: int = 100_000) -> str:
    """Reads and inspects the text/markdown/CSV content of a VFS file directly.

    Args:
        file_id: The unique ID of the file (e.g. 'file_12345678').
        max_bytes: Maximum characters/bytes to return to prevent context window overflow (default: 100,000).

    Returns:
        The text content of the document or metadata with download URL if binary.
    """
    require_scope("vfs:read")

    try:
        file_info = await proxy_module.proxy_client.vfs.get_file_details(file_id.strip())
        delivery_type = file_info.get("delivery_type")

        if delivery_type == "inline_text":
            text = file_info.get("text_content", "")
            if len(text) > max_bytes:
                truncated = text[:max_bytes]
                return f"{truncated}\n\n[... Truncated: document exceeded {max_bytes} bytes limit ...]"
            return text

        # If it returned a presigned URL, attempt fetching if it's text-like
        presigned_url = file_info.get("url")
        content_type = file_info.get("content_type", "")
        extension = str(file_info.get("extension", "")).lower()

        text_exts = {"md", "txt", "csv", "json", "yaml", "yml", "py", "ts", "js", "html", "css", "xml", "sql"}
        if presigned_url and (extension in text_exts or "text/" in content_type or "json" in content_type):
            async with httpx.AsyncClient(timeout=30.0) as http:
                res = await http.get(presigned_url)
                if res.is_success:
                    text = res.text
                    if len(text) > max_bytes:
                        return f"{text[:max_bytes]}\n\n[... Truncated: document exceeded {max_bytes} bytes limit ...]"
                    return text

        return json.dumps({
            "message": "File is binary or large. Use vfs_get_download_url to retrieve binary download recipe.",
            "metadata": file_info
        }, indent=2)

    except (ValstormHttpError, ProxyClientError) as e:
        status_code = getattr(e, "status_code", 500)
        return json.dumps({"error": str(e), "status_code": status_code})
    except Exception as e:
        return json.dumps({"error": f"Failed to read file '{file_id}': {e!s}"})


async def vfs_prepare_upload(
    filename: str,
    vault_id: str | None = None,
    content_type: str = "application/octet-stream",
) -> str:
    """Prepares an upload recipe and execution instructions for pushing files to Valstorm VFS.

    Args:
        filename: Local or target filename (e.g. 'financial_q3.pdf').
        vault_id: Destination Vault ID (optional, defaults to root or organization inbox).
        content_type: MIME type of the file (default: 'application/octet-stream').

    Returns:
        JSON instructions and curl recipe for out-of-band upload.
    """
    require_scope("vfs:write")

    auth_ctx = get_active_auth_context()
    token = auth_ctx.token if auth_ctx else "$VALSTORM_TOKEN"
    target_vault = vault_id or "root"
    upload_url = f"{config.api_base_url}/vfs/upload"

    curl_command = (
        f'curl -X POST "{upload_url}" '
        f'-H "Authorization: Bearer {token}" '
        f'-F "vault_id={target_vault}" '
        f'-F "file=@{filename}"'
    )

    recipe = {
        "status": "ready",
        "instructions": (
            f"Execute the terminal recipe below to upload '{filename}' into vault '{target_vault}'. "
            "Large binary transfers are completed directly via HTTP multipart to preserve MCP context window."
        ),
        "target_endpoint": upload_url,
        "vault_id": target_vault,
        "filename": filename,
        "content_type": content_type,
        "terminal_recipe": curl_command,
    }
    return json.dumps(recipe, indent=2)


async def vfs_get_download_url(file_id: str) -> str:
    """Retrieves metadata and a download recipe / presigned URL for a VFS file.

    Args:
        file_id: The unique ID of the file to download (e.g. 'file_12345678').

    Returns:
        JSON object with presigned download URL, filename, size, and terminal curl recipe.
    """
    require_scope("vfs:read")

    try:
        file_info = await proxy_module.proxy_client.vfs.get_file_details(file_id.strip())
        name = file_info.get("name") or f"downloaded_{file_id}"
        url = file_info.get("url") or f"{config.api_base_url}/vfs/download/{file_id}"

        auth_ctx = get_active_auth_context()
        token = auth_ctx.token if auth_ctx else "$VALSTORM_TOKEN"

        if file_info.get("url"):
            curl_recipe = f'curl -s -L "{url}" -o "{name}"'
        else:
            curl_recipe = f'curl -s -L -H "Authorization: Bearer {token}" "{url}" -o "{name}"'

        response_data = {
            "file_id": file_id,
            "filename": name,
            "file_size": file_info.get("file_size", 0),
            "content_type": file_info.get("content_type"),
            "vfs_path": file_info.get("vfs_path"),
            "download_url": url,
            "terminal_recipe": curl_recipe,
        }
        return json.dumps(response_data, indent=2)
    except (ValstormHttpError, ProxyClientError) as e:
        status_code = getattr(e, "status_code", 500)
        return json.dumps({"error": str(e), "status_code": status_code})
    except Exception as e:
        return json.dumps({"error": f"Failed to get download URL for '{file_id}': {e!s}"})


async def vfs_delete_file(file_id: str, confirm: bool = False) -> str:
    """Deletes a file or item from the Virtual File System.

    Requires explicit confirmation (`confirm=True`) to prevent accidental data loss.

    Args:
        file_id: The unique ID of the file or vault to delete.
        confirm: Must be set to True to execute the deletion.

    Returns:
        Confirmation of deletion or error message.
    """
    require_scope("vfs:delete")

    if not confirm:
        return json.dumps({
            "status": "confirmation_required",
            "warning": f"Potentially destructive action: Deleting file '{file_id}'. Set 'confirm=True' to proceed.",
            "file_id": file_id
        }, indent=2)

    try:
        data = await proxy_module.proxy_client.delete(f"vfs/{file_id.strip()}")
        return json.dumps({"status": "deleted", "file_id": file_id, "result": data}, indent=2)
    except (ValstormHttpError, ProxyClientError) as e:
        status_code = getattr(e, "status_code", 500)
        return json.dumps({"error": str(e), "status_code": status_code})
    except Exception as e:
        return json.dumps({"error": f"Failed to delete file '{file_id}': {e!s}"})


async def vfs_browse(target: str = "root", browse_type: str = "vault") -> str:
    """Explores Virtual File Service folders, file lists, and directory trees without downloading binary blobs.

    Args:
        target: The Vault ID (e.g. 'root', 'vaul_123') or string path (e.g. 'Finance/2026') to inspect.
        browse_type: 'vault' to inspect by Vault ID, 'path' to resolve human folder path, 'tree' for directory tree, or 'snapshot' for full hierarchy snapshot.

    Returns:
        JSON string representing the directory listing, child folders, and files.
    """
    require_scope("vfs:read")

    clean_target = target.strip()
    b_type = browse_type.strip().lower()

    try:
        if b_type == "tree":
            data = await proxy_module.proxy_client.vfs.tree()
        elif b_type == "snapshot":
            data = await proxy_module.proxy_client.vfs.snapshot()
        elif b_type == "path":
            clean_path = clean_target.strip("/")
            data = await proxy_module.proxy_client.vfs.resolve_path(clean_path)
        else:
            data = await proxy_module.proxy_client.vfs.get_vault(clean_target)

        return json.dumps(data, indent=2)
    except (ValstormHttpError, ProxyClientError) as e:
        status_code = getattr(e, "status_code", 500)
        return json.dumps({"error": str(e), "status_code": status_code})
    except Exception as e:
        return json.dumps({"error": f"Failed to browse VFS target '{target}': {e!s}"})


async def vfs_move(item_id: str, to_vault_id: str) -> str:
    """Moves a file or vault folder to another vault destination.

    Args:
        item_id: The unique ID of the file or vault to move.
        to_vault_id: The target destination Vault ID.

    Returns:
        JSON confirmation of move operation.
    """
    require_scope("vfs:write")

    try:
        data = await proxy_module.proxy_client.vfs.move(
            item_id=item_id.strip(),
            to_vault_id=to_vault_id.strip(),
        )
        return json.dumps({"status": "moved", "item_id": item_id, "to_vault_id": to_vault_id, "result": data}, indent=2)
    except (ValstormHttpError, ProxyClientError) as e:
        status_code = getattr(e, "status_code", 500)
        return json.dumps({"error": str(e), "status_code": status_code})
    except Exception as e:
        return json.dumps({"error": f"Failed to move VFS item '{item_id}': {e!s}"})
