"""
Search engine — multi-tier search combining FTS5, fuzzy matching (RapidFuzz),
alias expansion, and frequency-based scoring.

Scoring tiers
-------------
100     Exact block name match (case-insensitive)
90–99   Prefix match
75–89   FTS5 MATCH (BM25-ranked)
50–74   RapidFuzz WRatio ≥ threshold
+0–15   Frequency bonus from select_count

Results are sorted descending by final score.
"""
import logging
import os
import re
from typing import Any, Dict, List, Optional

from core.aliases import AliasResolver
from core.database import BlockRecord, Database

log = logging.getLogger(__name__)

# Lazy import rapidfuzz so the module is importable even without it installed
try:
    from rapidfuzz import process as rf_process, fuzz as rf_fuzz, utils as rf_utils
    _HAS_RAPIDFUZZ = True
except ImportError:
    _HAS_RAPIDFUZZ = False
    log.warning("rapidfuzz not installed — fuzzy search disabled")


class SearchEngine:
    def __init__(
        self,
        db: Database,
        alias_resolver: AliasResolver,
        config: Dict[str, Any],
    ) -> None:
        self._db = db
        self._aliases = alias_resolver
        self._threshold: int = config.get("fuzzy_threshold", 60)
        self._max_results: int = config.get("max_results", 200)

    # ------------------------------------------------------------------
    # Main search entry point
    # ------------------------------------------------------------------

    # Category constants
    CAT_ALL         = "all"
    CAT_BLOCK_NAME  = "block_name"
    CAT_KEYWORD     = "keyword"
    CAT_ATTRIBUTE   = "attribute"
    CAT_FILENAME    = "filename"
    CAT_TITLE_BLOCK = "title_block"

    def search(
        self,
        query: str,
        category: str = "all",
        path_filter: str = "",
    ) -> List[BlockRecord]:
        """
        Execute a search and return sorted BlockRecord list (best first).

        category  : one of all | block_name | keyword | attribute |
                    filename | title_block
        path_filter : optional directory path — restrict results to files
                      inside this folder (or any sub-folder)
        """
        query = query.strip()
        if not query:
            return []

        category = (category or self.CAT_ALL).lower().strip()

        # Filename-only search: bypass FTS, do a DB LIKE on filename/path
        if category == self.CAT_FILENAME:
            results = self._filename_search(query, path_filter)
            return results[: self._max_results]

        # Expand via aliases (only for block/keyword/attribute/all)
        expanded_terms = self._aliases.expand(query)
        log.debug("Search cat=%r query=%r expanded=%r", category, query, expanded_terms)

        # Tier 1 + 2: FTS5 for all expanded terms
        fts_results: Dict[int, BlockRecord] = {}
        for term in expanded_terms:
            for rec in self._db.fts_search(
                term, limit=self._max_results, category=category
            ):
                if rec.id not in fts_results:
                    fts_results[rec.id] = rec

        # Tier 3: fuzzy fallback on block names when FTS returns few results
        if len(fts_results) < 10 and _HAS_RAPIDFUZZ and category in (
            self.CAT_ALL, self.CAT_BLOCK_NAME
        ):
            fuzzy_ids = self._fuzzy_search(expanded_terms)
            new_ids = [bid for bid in fuzzy_ids if bid not in fts_results]
            if new_ids:
                for rec in self._db.get_blocks_by_ids(new_ids):
                    if rec.id not in fts_results:
                        fts_results[rec.id] = rec

        if not fts_results:
            return []

        # Optional path filter
        candidates = list(fts_results.values())
        if path_filter:
            norm = os.path.normcase(path_filter)
            candidates = [
                r for r in candidates
                if os.path.normcase(r.file_path).startswith(norm)
            ]

        # Score each result
        scored = [self._score(rec, query, expanded_terms, category) for rec in candidates]

        # Filter out anything below threshold / 2 and sort
        cutoff = self._threshold / 2
        scored = [r for r in scored if r.score >= cutoff]
        scored.sort(key=lambda r: r.score, reverse=True)

        return scored[: self._max_results]

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _score(
        self,
        rec: BlockRecord,
        original_query: str,
        expanded_terms: List[str],
        category: str = "all",
    ) -> BlockRecord:
        name_lower = rec.block_name.lower()
        query_lower = original_query.lower()

        score = 0.0

        # Exact match
        if name_lower == query_lower:
            score = 100.0
        # Exact match against any expanded term
        elif name_lower in expanded_terms:
            score = 98.0
        # Prefix match
        elif name_lower.startswith(query_lower):
            score = 90.0 + min(9.0, 9.0 * len(query_lower) / max(len(name_lower), 1))
        elif any(name_lower.startswith(t) for t in expanded_terms):
            score = 88.0
        # Substring
        elif query_lower in name_lower:
            score = 80.0
        elif any(t in name_lower for t in expanded_terms):
            score = 78.0
        elif _HAS_RAPIDFUZZ:
            # RapidFuzz WRatio
            best = max(
                (rf_fuzz.WRatio(
                    name_lower,
                    t,
                    processor=rf_utils.default_process,
                ) for t in expanded_terms),
                default=0,
            )
            if best >= self._threshold:
                # Map [threshold, 100] → [50, 74]
                score = 50.0 + 24.0 * (best - self._threshold) / (100.0 - self._threshold)
            else:
                score = best * 50.0 / max(self._threshold, 1)
        else:
            # Simple containment fallback
            if any(t in name_lower for t in expanded_terms):
                score = 55.0
            else:
                score = 10.0

        # Frequency boost: +0–15
        if rec.select_count > 0:
            boost = min(15.0, 3.0 * (rec.select_count ** 0.5))
            score = min(100.0, score + boost)

        rec.score = round(score, 1)
        return rec

    def _filename_search(self, query: str, path_filter: str) -> List[BlockRecord]:
        """Search by DWG filename (not block name)."""
        results = self._db.filename_search(query, path_filter, limit=self._max_results)
        for rec in results:
            q = query.lower()
            fn = rec.filename.lower()
            if fn == q:
                rec.score = 100.0
            elif fn.startswith(q):
                rec.score = 85.0
            elif q in fn:
                rec.score = 70.0
            else:
                rec.score = 50.0
            if rec.select_count > 0:
                rec.score = min(100.0, rec.score + min(15.0, 3.0 * (rec.select_count ** 0.5)))
        results.sort(key=lambda r: r.score, reverse=True)
        return results

    # ------------------------------------------------------------------
    # Fuzzy search via RapidFuzz
    # ------------------------------------------------------------------

    def _fuzzy_search(self, terms: List[str]) -> List[int]:
        """Return block IDs whose names score above the threshold."""
        if not _HAS_RAPIDFUZZ:
            return []

        all_names = self._db.get_all_block_names()
        if not all_names:
            return []

        ids_map: Dict[int, str] = {bid: name for bid, name in all_names}
        name_list = list(ids_map.values())
        id_list = list(ids_map.keys())

        result_ids: List[int] = []
        seen: set = set()

        for term in terms:
            matches = rf_process.extractBests(
                term,
                name_list,
                scorer=rf_fuzz.WRatio,
                processor=rf_utils.default_process,
                score_cutoff=self._threshold,
                limit=100,
            )
            for _match_str, _score, index in matches:
                bid = id_list[index]
                if bid not in seen:
                    seen.add(bid)
                    result_ids.append(bid)

        return result_ids
