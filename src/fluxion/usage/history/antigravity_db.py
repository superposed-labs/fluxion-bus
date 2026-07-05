"""Decoding Antigravity's per-conversation SQLite + protobuf usage stores.

A self-contained, low-level concern: a minimal protobuf wire decoder (only the
wire types Antigravity's generator metadata uses) plus the SQLite read that
feeds it. It shares nothing with the rest of the pipeline beyond producing
:class:`UsageEntry`, and changes only when Antigravity's on-disk schema does.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from fluxion.usage.history.entry import UsageEntry


def _sqlite_signature(path: Path) -> list[list[int | str]]:
    signature: list[list[int | str]] = []
    for related in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            stat = related.stat()
        except OSError:
            continue
        signature.append([related.name, stat.st_mtime_ns, stat.st_size])
    return signature


def _read_varint(blob: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(blob) and shift < 70:
        byte = blob[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7
    raise ValueError("invalid protobuf varint")


def _decode_proto(blob: bytes) -> dict[int, list[int | bytes]]:
    """Decode only protobuf wire types needed by Antigravity generator metadata."""
    fields: dict[int, list[int | bytes]] = {}
    offset = 0
    while offset < len(blob):
        tag, offset = _read_varint(blob, offset)
        number, wire_type = tag >> 3, tag & 7
        if number <= 0:
            raise ValueError("invalid protobuf field number")
        if wire_type == 0:
            value, offset = _read_varint(blob, offset)
        elif wire_type == 1:
            end = offset + 8
            if end > len(blob):
                raise ValueError("truncated protobuf fixed64")
            value, offset = blob[offset:end], end
        elif wire_type == 2:
            size, offset = _read_varint(blob, offset)
            end = offset + size
            if end > len(blob):
                raise ValueError("truncated protobuf bytes")
            value, offset = blob[offset:end], end
        elif wire_type == 5:
            end = offset + 4
            if end > len(blob):
                raise ValueError("truncated protobuf fixed32")
            value, offset = blob[offset:end], end
        else:
            raise ValueError(f"unsupported protobuf wire type {wire_type}")
        fields.setdefault(number, []).append(value)
    return fields


def _proto_int(fields: dict[int, list[int | bytes]], number: int) -> int:
    values = fields.get(number)
    return int(values[0]) if values and isinstance(values[0], int) else 0


def _proto_bytes(fields: dict[int, list[int | bytes]], number: int) -> bytes:
    values = fields.get(number)
    return values[0] if values and isinstance(values[0], bytes) else b""


def _proto_message(
    fields: dict[int, list[int | bytes]], number: int
) -> dict[int, list[int | bytes]]:
    raw = _proto_bytes(fields, number)
    return _decode_proto(raw) if raw else {}


def _antigravity_entry_from_blob(
    blob: bytes, *, session_id: str, row_index: int
) -> UsageEntry | None:
    """Map Antigravity GeneratorMetadataHeader protobuf fields to UsageEntry."""
    try:
        root = _decode_proto(blob)
        chat = _proto_message(root, 1)
        usage = _proto_message(chat, 4)
        timestamp = _proto_message(_proto_message(chat, 9), 4)
    except ValueError:
        return None
    input_tokens = _proto_int(usage, 2)
    output_tokens = _proto_int(usage, 3)
    cache_read_tokens = _proto_int(usage, 5)
    seconds = _proto_int(timestamp, 1)
    if not seconds or not (input_tokens or output_tokens or cache_read_tokens):
        return None
    # Field 21 is the user-selected/display model (for example
    # "Gemini 3.5 Flash (High)"); field 19 is the backend response model (for
    # example "gemini-3-flash-a"). Stats should group by what the user selected,
    # otherwise High-mode calls are silently folded into the generic backend
    # model. Fall back to the response model for older rows without field 21.
    model_raw = _proto_bytes(chat, 21) or _proto_bytes(chat, 19)
    model = model_raw.decode("utf-8", errors="replace") if model_raw else "unknown"
    try:
        ts = datetime.fromtimestamp(seconds, tz=UTC)
    except (OSError, OverflowError, ValueError):
        return None
    return UsageEntry(
        provider="antigravity",
        ts=ts,
        model=model,
        session_id=session_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_tokens=0,
        cache_read_tokens=cache_read_tokens,
        dedup_key=f"antigravity:{session_id}:{row_index}",
        billed_input_tokens_total=input_tokens + cache_read_tokens,
    )


def _parse_antigravity_db(path: Path) -> list[UsageEntry]:
    out: list[UsageEntry] = []
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.2)
        try:
            rows = connection.execute("SELECT idx, data FROM gen_metadata ORDER BY idx")
            for row_index, blob in rows:
                if not isinstance(row_index, int) or not isinstance(blob, bytes):
                    continue
                entry = _antigravity_entry_from_blob(
                    blob, session_id=path.stem, row_index=row_index
                )
                if entry is not None:
                    out.append(entry)
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        return []
    return out
