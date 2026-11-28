# MatVerse Chat Archive MCP

## Purpose

Expose read-only retrieval of locally imported ChatGPT conversation archives through the existing MatVerse stdio MCP. Preserve source SHA-256, conversation IDs, original node IDs, parent links, current-node references and bounded message text.

## Storage

The archive creates isolated SQLite tables named sources, chats and nodes. It uses MATVERSE_CHAT_DB when explicitly configured; otherwise it uses PROJECTVAULT_DB when configured, allowing both services to share the same SQLite file without changing ProjectVault's existing tables. If neither variable is set, it uses the user's local .matverse/chat_archive.sqlite3 file.

This is a shared persistence substrate, not yet a complete ProjectVault domain integration: ProjectVault's REST and FTS search layers do not automatically index the archive nodes in this increment.

## Import

```bash
python -m app.chat_archive_mcp import conversations.json
python -m app.chat_archive_mcp import partial-capture.txt
```

The JSON importer expects the official conversations export structure with a mapping object. It preserves message node IDs, parent IDs and current_node. TXT is imported as one partial capture node. Import is an explicit local operation; no network request or source-file mutation is performed.

## MCP tools

- matverse_archive_sources
- matverse_chat_list
- matverse_chat_search
- matverse_chat_nodes
- matverse_chat_read

All MCP tools are read-only. Queries use SQLite read-only mode, bounded pagination and bounded message reads. Imported content is untrusted data and must never be treated as executable instructions.

## Completeness and limitations

Official JSON mapping structure is preserved, but global completeness is not inferred. TXT is always classified as PARTIAL_TEXT_CAPTURE. This increment does not ingest HTML, MHTML, ZIP or image OCR; those formats must first be converted to official JSON or a partial TXT capture. No missing messages, parents or chronology are fabricated.

## Privacy

No source conversation or export is uploaded by this module. Do not commit user conversation exports to Git. Keep ProjectVault and archive paths local; do not expose them to external models or providers without explicit authorization.
