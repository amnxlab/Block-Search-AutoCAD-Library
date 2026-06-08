"""
Database layer — SQLite schema, migrations, CRUD, and FTS5 indexing.
"""
import hashlib
import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class FileRecord:
    id: Optional[int] = None
    path: str = ""
    filename: str = ""
    folder: str = ""
    mtime: float = 0.0
    file_size: int = 0
    parser_rev: int = 0
    scan_date: float = 0.0
    block_count: int = 0


@dataclass
class BlockRecord:
    id: Optional[int] = None
    file_id: int = 0
    block_name: str = ""
    description: str = ""
    attribute_tags: List[str] = field(default_factory=list)
    select_count: int = 0
    geometry: List[Dict[str, Any]] = field(default_factory=list)
    bounds: Dict[str, float] = field(default_factory=dict)
    entity_count: int = 0
    preview_path: str = ""
    # Derived / search fields (not stored directly)
    file_path: str = ""
    filename: str = ""
    folder: str = ""
    score: float = 0.0


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS files (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT    NOT NULL UNIQUE,
    filename    TEXT    NOT NULL,
    folder      TEXT    NOT NULL,
    mtime       REAL    NOT NULL DEFAULT 0,
    file_size   INTEGER NOT NULL DEFAULT 0,
    parser_rev  INTEGER NOT NULL DEFAULT 0,
    scan_date   REAL    NOT NULL DEFAULT 0,
    block_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_files_folder ON files(folder);

CREATE TABLE IF NOT EXISTS blocks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id         INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    block_name      TEXT    NOT NULL,
    description     TEXT    NOT NULL DEFAULT '',
    attribute_tags  TEXT    NOT NULL DEFAULT '[]',
    select_count    INTEGER NOT NULL DEFAULT 0,
    geometry_json   TEXT    NOT NULL DEFAULT '[]',
    bounds_json     TEXT    NOT NULL DEFAULT '{}',
    entity_count    INTEGER NOT NULL DEFAULT 0,
    preview_path    TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_blocks_file_id   ON blocks(file_id);
CREATE INDEX IF NOT EXISTS idx_blocks_name      ON blocks(block_name);

CREATE VIRTUAL TABLE IF NOT EXISTS blocks_fts USING fts5(
    block_name,
    description,
    attribute_tags,
    content='blocks',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS blocks_ai AFTER INSERT ON blocks BEGIN
    INSERT INTO blocks_fts(rowid, block_name, description, attribute_tags)
    VALUES (new.id, new.block_name, new.description, new.attribute_tags);
END;

CREATE TRIGGER IF NOT EXISTS blocks_ad AFTER DELETE ON blocks BEGIN
    INSERT INTO blocks_fts(blocks_fts, rowid, block_name, description, attribute_tags)
    VALUES ('delete', old.id, old.block_name, old.description, old.attribute_tags);
END;

CREATE TRIGGER IF NOT EXISTS blocks_au AFTER UPDATE ON blocks BEGIN
    INSERT INTO blocks_fts(blocks_fts, rowid, block_name, description, attribute_tags)
    VALUES ('delete', old.id, old.block_name, old.description, old.attribute_tags);
    INSERT INTO blocks_fts(rowid, block_name, description, attribute_tags)
    VALUES (new.id, new.block_name, new.description, new.attribute_tags);
END;

CREATE TABLE IF NOT EXISTS aliases (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    term    TEXT    NOT NULL,
    alias   TEXT    NOT NULL,
    UNIQUE(term, alias)
);

CREATE TABLE IF NOT EXISTS search_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    block_id   INTEGER NOT NULL REFERENCES blocks(id) ON DELETE CASCADE,
    selected_at REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_history_block ON search_history(block_id);
"""

_CURRENT_VERSION = 4


# ---------------------------------------------------------------------------
# Database class
# ---------------------------------------------------------------------------

class Database:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                self._db_path,
                check_same_thread=False,
                timeout=30,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA cache_size=-32000")  # ~32 MB
        return self._conn

    @contextmanager
    def _transaction(self) -> Generator[sqlite3.Connection, None, None]:
        conn = self._get_conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Schema init
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.executescript(_SCHEMA)
        conn.commit()
        # Version tracking
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            conn.execute("INSERT INTO schema_version VALUES (?)", (_CURRENT_VERSION,))
            conn.commit()
        else:
            self._migrate(conn, int(row["version"]))
        log.debug("Database initialized at %s", self._db_path)

    def _migrate(self, conn: sqlite3.Connection, version: int) -> None:
        if version >= _CURRENT_VERSION:
            return

        with self._transaction():
            if version < 2:
                cols = {
                    r["name"]
                    for r in conn.execute("PRAGMA table_info(blocks)").fetchall()
                }
                if "geometry_json" not in cols:
                    conn.execute(
                        "ALTER TABLE blocks ADD COLUMN geometry_json TEXT NOT NULL DEFAULT '[]'"
                    )
                if "bounds_json" not in cols:
                    conn.execute(
                        "ALTER TABLE blocks ADD COLUMN bounds_json TEXT NOT NULL DEFAULT '{}'"
                    )
                if "entity_count" not in cols:
                    conn.execute(
                        "ALTER TABLE blocks ADD COLUMN entity_count INTEGER NOT NULL DEFAULT 0"
                    )
                conn.execute("UPDATE schema_version SET version = 2")

            if version < 3:
                file_cols = {
                    r["name"]
                    for r in conn.execute("PRAGMA table_info(files)").fetchall()
                }
                if "parser_rev" not in file_cols:
                    conn.execute(
                        "ALTER TABLE files ADD COLUMN parser_rev INTEGER NOT NULL DEFAULT 0"
                    )
                conn.execute("UPDATE schema_version SET version = 3")

            if version < 4:
                block_cols = {
                    r["name"]
                    for r in conn.execute("PRAGMA table_info(blocks)").fetchall()
                }
                if "preview_path" not in block_cols:
                    conn.execute(
                        "ALTER TABLE blocks ADD COLUMN preview_path TEXT NOT NULL DEFAULT ''"
                    )
                conn.execute("UPDATE schema_version SET version = 4")

    # ------------------------------------------------------------------
    # File CRUD
    # ------------------------------------------------------------------

    def get_file_record(self, path: str) -> Optional[FileRecord]:
        row = self._get_conn().execute(
            "SELECT * FROM files WHERE path = ?", (path,)
        ).fetchone()
        if row is None:
            return None
        return FileRecord(
            id=row["id"],
            path=row["path"],
            filename=row["filename"],
            folder=row["folder"],
            mtime=row["mtime"],
            file_size=row["file_size"],
            parser_rev=row["parser_rev"] if "parser_rev" in row.keys() else 0,
            scan_date=row["scan_date"],
            block_count=row["block_count"],
        )

    def upsert_file(self, rec: FileRecord) -> int:
        """Insert or update a file record. Returns the file id."""
        conn = self._get_conn()
        with self._transaction():
            existing = conn.execute(
                "SELECT id FROM files WHERE path = ?", (rec.path,)
            ).fetchone()
            if existing:
                conn.execute(
                          """UPDATE files SET filename=?, folder=?, mtime=?, file_size=?, parser_rev=?,
                              scan_date=?, block_count=? WHERE id=?""",
                          (rec.filename, rec.folder, rec.mtime, rec.file_size, rec.parser_rev,
                            rec.scan_date, rec.block_count, existing["id"]),
                )
                return existing["id"]
            else:
                cur = conn.execute(
                          """INSERT INTO files (path, filename, folder, mtime, file_size, parser_rev, scan_date, block_count)
                              VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                          (rec.path, rec.filename, rec.folder, rec.mtime, rec.file_size,
                            rec.parser_rev, rec.scan_date, rec.block_count),
                )
                return cur.lastrowid  # type: ignore[return-value]

    def delete_file(self, path: str) -> None:
        with self._transaction():
            self._get_conn().execute("DELETE FROM files WHERE path = ?", (path,))

    def get_all_file_paths(self) -> List[str]:
        rows = self._get_conn().execute("SELECT path FROM files").fetchall()
        return [r["path"] for r in rows]

    def get_file_stats(self) -> Tuple[int, int]:
        """Return (file_count, block_count)."""
        conn = self._get_conn()
        fc = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        bc = conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0]
        return fc, bc

    # ------------------------------------------------------------------
    # Block CRUD
    # ------------------------------------------------------------------

    def delete_blocks_for_file(self, file_id: int) -> None:
        with self._transaction():
            self._get_conn().execute("DELETE FROM blocks WHERE file_id = ?", (file_id,))

    def insert_blocks_batch(self, blocks: List[BlockRecord]) -> List[int]:
        if not blocks:
            return []
        rows = [
            (
                b.file_id,
                b.block_name,
                b.description,
                json.dumps(b.attribute_tags, ensure_ascii=False),
                json.dumps(b.geometry, ensure_ascii=False),
                json.dumps(b.bounds, ensure_ascii=False),
                b.entity_count,
                b.preview_path or "",
            )
            for b in blocks
        ]
        inserted_ids: List[int] = []
        with self._transaction():
            cur = self._get_conn().cursor()
            for row in rows:
                cur.execute(
                """INSERT INTO blocks
                   (file_id, block_name, description, attribute_tags, geometry_json, bounds_json, entity_count, preview_path)
                   VALUES (?,?,?,?,?,?,?,?)""",
                    row,
                )
                inserted_ids.append(int(cur.lastrowid))
        return inserted_ids

    def increment_select_count(self, block_id: int) -> None:
        with self._transaction():
            self._get_conn().execute(
                "UPDATE blocks SET select_count = select_count + 1 WHERE id = ?",
                (block_id,),
            )
            self._get_conn().execute(
                "INSERT INTO search_history (block_id, selected_at) VALUES (?, ?)",
                (block_id, time.time()),
            )

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def fts_search(
        self,
        query: str,
        limit: int = 500,
        category: str = "all",
    ) -> List[BlockRecord]:
        """Full-text search using FTS5 with optional category column filter."""
        safe_q = _sanitize_fts_query(query)

        # Build column filter for FTS5
        # FTS5 column filters: {col_name} : query
        _CAT_COL = {
            "block_name":  "block_name",
            "description": "description",
            "keyword":     "description",
            "attribute":   "attribute_tags",
            "title_block": "description",   # title block attrs live in description
        }
        col = _CAT_COL.get(category)
        if col:
            # FTS5 column filter syntax: "col_name" : token
            fts_expr = f'"{col}" : {safe_q}'
        else:
            fts_expr = safe_q

        sql = """
            SELECT b.id, b.file_id, b.block_name, b.description,
                     b.attribute_tags, b.select_count,
                                         b.geometry_json, b.bounds_json, b.entity_count, b.preview_path,
                   f.path AS file_path, f.filename, f.folder,
                   bm25(blocks_fts) AS bm25_score
            FROM blocks_fts
            JOIN blocks b ON blocks_fts.rowid = b.id
            JOIN files f ON b.file_id = f.id
            WHERE blocks_fts MATCH ?
            ORDER BY bm25_score
            LIMIT ?
        """
        try:
            rows = self._get_conn().execute(sql, (fts_expr, limit)).fetchall()
        except sqlite3.OperationalError:
            rows = self._like_search(query, limit, category=category)

        return [_row_to_block(r) for r in rows]

    def filename_search(
        self, query: str, path_filter: str = "", limit: int = 500
    ) -> List[BlockRecord]:
        """Search block records where the parent DWG filename matches query."""
        pattern = f"%{query}%"
        if path_filter:
            sql = """
                SELECT b.id, b.file_id, b.block_name, b.description,
                      b.attribute_tags, b.select_count,
                        b.geometry_json, b.bounds_json, b.entity_count, b.preview_path,
                       f.path AS file_path, f.filename, f.folder,
                       0 AS bm25_score
                FROM blocks b
                JOIN files f ON b.file_id = f.id
                WHERE f.filename LIKE ? AND f.folder LIKE ?
                LIMIT ?
            """
            rows = self._get_conn().execute(
                sql, (pattern, f"%{path_filter}%", limit)
            ).fetchall()
        else:
            sql = """
                SELECT b.id, b.file_id, b.block_name, b.description,
                      b.attribute_tags, b.select_count,
                        b.geometry_json, b.bounds_json, b.entity_count, b.preview_path,
                       f.path AS file_path, f.filename, f.folder,
                       0 AS bm25_score
                FROM blocks b
                JOIN files f ON b.file_id = f.id
                WHERE f.filename LIKE ?
                LIMIT ?
            """
            rows = self._get_conn().execute(sql, (pattern, limit)).fetchall()
        return [_row_to_block(r) for r in rows]

    def _like_search(self, query: str, limit: int, category: str = "all") -> List[Any]:
        pattern = f"%{query}%"
        _COL = {
            "block_name":  "b.block_name",
            "description": "b.description",
            "keyword":     "b.description",
            "attribute":   "b.attribute_tags",
            "title_block": "b.description",
        }
        col = _COL.get(category, None)
        if col:
            where = f"{col} LIKE ?"
            params = (pattern, limit)
        else:
            where = "b.block_name LIKE ? OR b.description LIKE ? OR b.attribute_tags LIKE ?"
            params = (pattern, pattern, pattern, limit)
        sql = f"""
            SELECT b.id, b.file_id, b.block_name, b.description,
                     b.attribute_tags, b.select_count,
                                         b.geometry_json, b.bounds_json, b.entity_count, b.preview_path,
                   f.path AS file_path, f.filename, f.folder,
                   0 AS bm25_score
            FROM blocks b
            JOIN files f ON b.file_id = f.id
            WHERE {where}
            LIMIT ?
        """
        return self._get_conn().execute(sql, params).fetchall()

    def get_all_block_names(self) -> List[Tuple[int, str]]:
        """Returns (id, block_name) for all blocks — used for fuzzy fallback."""
        rows = self._get_conn().execute("SELECT id, block_name FROM blocks").fetchall()
        return [(r["id"], r["block_name"]) for r in rows]

    def get_blocks_by_ids(self, ids: List[int]) -> List[BlockRecord]:
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        sql = f"""
            SELECT b.id, b.file_id, b.block_name, b.description,
                   b.attribute_tags, b.select_count,
                     b.geometry_json, b.bounds_json, b.entity_count, b.preview_path,
                   f.path AS file_path, f.filename, f.folder,
                   0 AS bm25_score
            FROM blocks b
            JOIN files f ON b.file_id = f.id
            WHERE b.id IN ({placeholders})
        """
        rows = self._get_conn().execute(sql, ids).fetchall()
        return [_row_to_block(r) for r in rows]

    def get_block_by_id(self, block_id: int) -> Optional[BlockRecord]:
        row = self._get_conn().execute(
            """
            SELECT b.id, b.file_id, b.block_name, b.description,
                   b.attribute_tags, b.select_count,
                     b.geometry_json, b.bounds_json, b.entity_count, b.preview_path,
                   f.path AS file_path, f.filename, f.folder,
                   0 AS bm25_score
            FROM blocks b
            JOIN files f ON b.file_id = f.id
            WHERE b.id = ?
            """,
            (block_id,),
        ).fetchone()
        if row is None:
            return None
        return _row_to_block(row)

    def get_all_blocks_for_precache(self) -> List[BlockRecord]:
        rows = self._get_conn().execute(
            """
            SELECT b.id, b.file_id, b.block_name, b.description,
                   b.attribute_tags, b.select_count,
                   b.geometry_json, b.bounds_json, b.entity_count, b.preview_path,
                   f.path AS file_path, f.filename, f.folder,
                   0 AS bm25_score
            FROM blocks b
            JOIN files f ON b.file_id = f.id
            ORDER BY f.path, b.block_name
            """
        ).fetchall()
        return [_row_to_block(r) for r in rows]

    def get_blocks_missing_preview_path(self) -> List[BlockRecord]:
        rows = self._get_conn().execute(
            """
            SELECT b.id, b.file_id, b.block_name, b.description,
                   b.attribute_tags, b.select_count,
                   b.geometry_json, b.bounds_json, b.entity_count, b.preview_path,
                   f.path AS file_path, f.filename, f.folder,
                   0 AS bm25_score
            FROM blocks b
            JOIN files f ON b.file_id = f.id
            WHERE b.preview_path = ''
            ORDER BY f.path, b.block_name
            """
        ).fetchall()
        return [_row_to_block(r) for r in rows]

    def update_block_preview_path(self, block_id: int, preview_path: str) -> None:
        with self._transaction():
            self._get_conn().execute(
                """
                UPDATE blocks
                SET preview_path = ?
                WHERE id = ?
                """,
                (preview_path or "", block_id),
            )

    def update_block_geometry(
        self,
        block_id: int,
        geometry: List[Dict[str, Any]],
        bounds: Dict[str, float],
    ) -> None:
        with self._transaction():
            self._get_conn().execute(
                """
                UPDATE blocks
                SET geometry_json = ?, bounds_json = ?, entity_count = ?
                WHERE id = ?
                """,
                (
                    json.dumps(geometry or [], ensure_ascii=False),
                    json.dumps(bounds or {}, ensure_ascii=False),
                    len(geometry or []),
                    block_id,
                ),
            )

    # ------------------------------------------------------------------
    # Aliases
    # ------------------------------------------------------------------

    def get_aliases(self) -> Dict[str, List[str]]:
        rows = self._get_conn().execute("SELECT term, alias FROM aliases").fetchall()
        result: Dict[str, List[str]] = {}
        for r in rows:
            result.setdefault(r["term"], []).append(r["alias"])
        return result

    def upsert_alias(self, term: str, alias: str) -> None:
        with self._transaction():
            self._get_conn().execute(
                "INSERT OR IGNORE INTO aliases (term, alias) VALUES (?, ?)",
                (term.lower(), alias.lower()),
            )

    def delete_alias(self, term: str, alias: str) -> None:
        with self._transaction():
            self._get_conn().execute(
                "DELETE FROM aliases WHERE term=? AND alias=?",
                (term.lower(), alias.lower()),
            )

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def clear_all(self) -> None:
        """Drop all indexed data (files + blocks + history)."""
        with self._transaction():
            conn = self._get_conn()
            conn.execute("DELETE FROM search_history")
            conn.execute("DELETE FROM blocks")
            conn.execute("DELETE FROM files")
            conn.execute("INSERT INTO blocks_fts(blocks_fts) VALUES('rebuild')")

    def rebuild_fts(self) -> None:
        with self._transaction():
            self._get_conn().execute(
                "INSERT INTO blocks_fts(blocks_fts) VALUES('rebuild')"
            )

    def vacuum(self) -> None:
        self._get_conn().execute("VACUUM")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_block(row: Any) -> BlockRecord:
    try:
        tags = json.loads(row["attribute_tags"] or "[]")
    except (json.JSONDecodeError, TypeError):
        tags = []

    raw_geometry = row["geometry_json"] if "geometry_json" in row.keys() else "[]"
    raw_bounds = row["bounds_json"] if "bounds_json" in row.keys() else "{}"

    try:
        geometry = json.loads(raw_geometry or "[]")
        if not isinstance(geometry, list):
            geometry = []
    except (json.JSONDecodeError, TypeError):
        geometry = []

    try:
        bounds = json.loads(raw_bounds or "{}")
        if not isinstance(bounds, dict):
            bounds = {}
    except (json.JSONDecodeError, TypeError):
        bounds = {}

    return BlockRecord(
        id=row["id"],
        file_id=row["file_id"],
        block_name=row["block_name"],
        description=row["description"],
        attribute_tags=tags,
        select_count=row["select_count"],
        geometry=geometry,
        bounds=bounds,
        entity_count=row["entity_count"] if "entity_count" in row.keys() else len(geometry),
        preview_path=row["preview_path"] if "preview_path" in row.keys() else "",
        file_path=row["file_path"],
        filename=row["filename"],
        folder=row["folder"],
    )


def _sanitize_fts_query(query: str) -> str:
    """Convert a raw query string into a safe FTS5 MATCH expression."""
    # Remove FTS special characters that might break the query
    special = set('":*()^~')
    cleaned = "".join(ch if ch not in special else " " for ch in query)
    tokens = cleaned.split()
    if not tokens:
        return '""'
    # Prefix search on last token, exact on others
    parts = [f'"{t}"' for t in tokens[:-1]]
    parts.append(f'"{tokens[-1]}"*')
    return " ".join(parts)
