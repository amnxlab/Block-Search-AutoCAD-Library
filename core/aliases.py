"""
Alias resolver — loads the aliases.json file and the user-specific aliases
stored in the database, then expands search queries into a list of related terms.
"""
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


class AliasResolver:
    """
    Maintains a flat alias map: term → [alias1, alias2, …]

    The map is bidirectional: if "mccb" → ["breaker"], then searching for
    "breaker" will also return "mccb" as an expansion.
    """

    def __init__(self) -> None:
        self._map: Dict[str, List[str]] = {}

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_from_file(self, json_path: str) -> None:
        """Load from the flat or nested aliases.json shipped with the app."""
        try:
            with open(json_path, "r", encoding="utf-8") as fh:
                data: Dict[str, Any] = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not load aliases file %s: %s", json_path, exc)
            return

        # Support both flat (term→list) and nested (category→{term→list}) formats
        for key, value in data.items():
            if isinstance(value, list):
                self._add(key, value)
            elif isinstance(value, dict):
                for term, aliases in value.items():
                    if isinstance(aliases, list):
                        self._add(term, aliases)

        log.debug("Loaded aliases: %d terms", len(self._map))

    def load_from_db(self, db_aliases: Dict[str, List[str]]) -> None:
        """Merge aliases stored in the SQLite database."""
        for term, aliases in db_aliases.items():
            self._add(term, aliases)

    def _add(self, term: str, aliases: List[str]) -> None:
        t = term.lower().strip()
        if not t:
            return
        self._map.setdefault(t, [])
        for alias in aliases:
            a = alias.lower().strip()
            if a and a not in self._map[t]:
                self._map[t].append(a)
            # Reverse mapping
            self._map.setdefault(a, [])
            if t not in self._map[a]:
                self._map[a].append(t)

    # ------------------------------------------------------------------
    # Expansion
    # ------------------------------------------------------------------

    def expand(self, query: str) -> List[str]:
        """
        Return the original query tokens PLUS all alias expansions.
        Duplicates removed, original terms preserved.
        """
        q = query.lower().strip()
        if not q:
            return []

        tokens = q.split()
        expanded: List[str] = []

        def _push(term: str) -> None:
            t = term.strip()
            if t and t not in expanded:
                expanded.append(t)

        # Keep the original full query first for highest-intent matching.
        _push(q)

        # Add individual tokens.
        for token in tokens:
            _push(token)

        # Single-token aliases.
        for token in tokens:
            for alias in self._map.get(token, []):
                _push(alias)

        # Phrase aliases (2-4 token windows).
        n = len(tokens)
        max_window = min(4, n)
        for window in range(2, max_window + 1):
            for start in range(0, n - window + 1):
                phrase = " ".join(tokens[start : start + window])
                _push(phrase)
                for alias in self._map.get(phrase, []):
                    _push(alias)

        return expanded

    def get_all_terms(self) -> Dict[str, List[str]]:
        """Return a copy of the full alias map for display in settings."""
        return {k: list(v) for k, v in self._map.items()}
