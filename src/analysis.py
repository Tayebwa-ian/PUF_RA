"""Analysis pipeline for the PUF_RA study (statistics + plots + persistence).

This module is what the dedicated *analysis agent* uses to turn the literature
database into the study's statistics and figures. Every database read goes
through the MCP server tools (``execute_select`` / ``get_paper_provenance`` /
``store_analysis`` / ``list_analysis``) exposed by :mod:`src.mcp_server`, so the
agent always queries the DB the same way a human would: over a read-only,
guarded, stdio MCP channel.

Two transports are supported:

* **Primary (MCP over stdio)** — launches ``python -m src.mcp_server`` **once**
  per client and drives it through the ``mcp`` SDK's ``ClientSession`` (one
  long-lived session, not one subprocess per query). All reads are funnelled
  through the server's read-only guard.
* **Fallback (direct SQLite)** — when the MCP client cannot be created/used (no
  SDK, subprocess blocked, ...), the client opens the database file directly and
  reuses the very same guard/queries. The module stays fully usable offline.

Pipeline:

* :class:`AnalysisClient` — thin transport over the MCP tools (or the fallback).
* Stat builders (``corpus_by_source``, ``relevance_distribution``, ...) issue
  *efficient aggregate* ``SELECT`` statements through ``client.select``.
* Plot builders (``plot_corpus_bars``, ``plot_confusion``, ...) render matplotlib
  figures (Agg backend) to ``data/analysis/<name>.png``.
* :func:`run_analysis` ties it together: compute -> write JSON -> render plots
  -> persist the result via ``client.store``.

Usage:
    from src.analysis import AnalysisClient, run_analysis

    client = AnalysisClient("results.db")            # prefers MCP, falls back
    summary = run_analysis("corpus_overview", client)
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # headless / CI safe; no GUI backend required
import matplotlib.pyplot as plt  # noqa: E402

from src.db import DEFAULT_DB_PATH, get_connection  # noqa: E402
from src import eval_store  # noqa: E402
from src.mcp_server import assert_select_only  # noqa: E402


DATA_ANALYSIS_DIR = Path("data/analysis")

#: Repository root: the MCP server subprocess is started there so that
#: ``python -m src.mcp_server`` can import the ``src`` package.
_REPO_ROOT = Path(__file__).resolve().parent.parent

#: Default timeout (seconds) for session start-up and for one MCP round-trip.
MCP_TIMEOUT = 60.0

#: Text prefixes the MCP tools use for in-band failures (read-only guard
#: rejections, tool exceptions). Never the start of a JSON payload.
_FAILURE_PREFIXES = ("rejected", "error", "valueerror", "typeerror", "keyerror", "sqlite3")


# ---------------------------------------------------------------------------
# Transport: AnalysisClient (MCP stdio primary, direct SQLite fallback)
# ---------------------------------------------------------------------------


def _result_texts(result: Any) -> list[str]:
    """Return the text of every content block of an MCP ``CallToolResult``."""
    blocks = getattr(result, "content", None) or []
    return [b.text for b in blocks if isinstance(getattr(b, "text", None), str)]


def _loads(text: str) -> Any:
    """Parse one content block; non-JSON text is returned unchanged."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def parse_tool_result(name: str, result: Any) -> Any:
    """Turn an ``mcp`` ``CallToolResult`` into a plain Python payload.

    The SDK model exposes **snake_case** fields (``is_error``, ``content``,
    ``structured_content``); the camelCase names are only the wire aliases, so
    reading ``isError`` / ``structuredContent`` off the model always yields
    ``None``/``False``. Both spellings are probed here for safety.

    Content handling:

    * ``structured_content == {"result": ...}`` is authoritative when present.
    * Otherwise every text block is JSON-decoded. A tool that returns a *list*
      (e.g. ``execute_select``) emits **one text block per row**, so all blocks
      are collected — reading only ``content[0]`` would silently drop rows.

    Raises:
        ValueError: If ``is_error`` is set or a text block is an in-band failure
            message (``Rejected: ...`` from the read-only guard, ``Error ...``),
            so a rejection never surfaces as a ``JSONDecodeError``.
    """
    texts = _result_texts(result)
    is_error = bool(getattr(result, "is_error", False) or getattr(result, "isError", False))
    first = texts[0].strip() if texts else ""
    if is_error:
        raise ValueError(f"MCP tool {name!r} failed: {first or result!r}")
    for text in texts:
        stripped = text.strip()
        if stripped.lower().startswith(_FAILURE_PREFIXES):
            raise ValueError(f"MCP tool {name!r} rejected the request: {stripped}")

    structured = getattr(result, "structured_content", None)
    if structured is None:
        structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and "result" in structured:
        return structured["result"]

    if not texts:
        return None
    if len(texts) == 1:
        return _loads(texts[0])
    return [_loads(t) for t in texts]


def _as_rows(name: str, payload: Any) -> list[dict[str, Any]]:
    """Coerce a tool payload into a list of row dicts."""
    if payload is None:
        return []
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        if all(isinstance(row, dict) for row in payload):
            return payload
    raise ValueError(f"MCP tool {name!r} returned a non-tabular payload: {payload!r}")


class _McpStdioSession:
    """One long-lived MCP stdio session, owned by a private event-loop thread.

    The ``mcp`` SDK is asynchronous while :class:`AnalysisClient` is synchronous.
    Rather than spawning a fresh ``python -m src.mcp_server`` per query, the
    session is opened once (on a dedicated event loop running in a daemon
    thread) and every call is dispatched onto that loop, so an ``analyze all``
    run uses a single server process.
    """

    def __init__(self, db_path: str | Path, timeout: float = MCP_TIMEOUT) -> None:
        self._db_path = str(Path(db_path).resolve())
        self._timeout = timeout
        self._loop = asyncio.new_event_loop()
        self._session: Any = None
        self._stop: asyncio.Event | None = None
        self._ready: concurrent.futures.Future = concurrent.futures.Future()
        self._thread = threading.Thread(
            target=self._thread_main, name="analysis-mcp", daemon=True
        )
        self._thread.start()
        try:
            self._ready.result(timeout=timeout)
        except BaseException:
            self.close()
            raise

    # -- event-loop thread ---------------------------------------------------

    def _thread_main(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._own_session())
        finally:
            try:
                self._loop.close()
            except Exception:  # pragma: no cover - defensive
                pass

    async def _own_session(self) -> None:
        """Open the stdio session and keep it alive until :meth:`close`."""
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "src.mcp_server", "--db", self._db_path],
            cwd=str(_REPO_ROOT),
        )
        self._stop = asyncio.Event()
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._session = session
                    if not self._ready.done():
                        self._ready.set_result(True)
                    await self._stop.wait()
        except BaseException as exc:  # start-up failure, or the server died
            if not self._ready.done():
                self._ready.set_exception(exc)
        finally:
            self._session = None

    # -- public API ----------------------------------------------------------

    @property
    def alive(self) -> bool:
        return self._session is not None and self._thread.is_alive()

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        """Call one MCP tool on the shared session and return the raw result."""
        session = self._session
        if session is None or not self._thread.is_alive():
            raise RuntimeError("MCP stdio session is not available")
        future = asyncio.run_coroutine_threadsafe(
            session.call_tool(name, arguments), self._loop
        )
        return future.result(timeout=self._timeout)

    def close(self) -> None:
        """Stop the session, the server subprocess and the event-loop thread."""
        if self._stop is not None and not self._loop.is_closed():
            try:
                self._loop.call_soon_threadsafe(self._stop.set)
            except RuntimeError:  # pragma: no cover - loop already gone
                pass
        if self._thread.is_alive():
            self._thread.join(timeout=self._timeout)


class AnalysisClient:
    """Query the PUF_RA database through the MCP server, with a direct fallback.

    Args:
        db_path: Path to the SQLite database. Defaults to ``results.db``.
        mcp_mode: ``"auto"`` (try MCP, fall back to direct on any failure),
            ``"mcp"`` (require MCP; raise if unavailable), or ``"direct"``
            (skip MCP entirely and use the local connection).
        timeout: Seconds allowed for the session hand-shake and each call.

    The MCP session (when used) lives for as long as the client; call
    :meth:`close` (or use the client as a context manager) to shut the server
    subprocess down.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        mcp_mode: str = "auto",
        timeout: float = MCP_TIMEOUT,
    ):
        self._db_path = str(db_path) if db_path is not None else DEFAULT_DB_PATH
        if mcp_mode not in ("auto", "mcp", "direct"):
            raise ValueError(f"Unknown mcp_mode: {mcp_mode!r}")
        self._mode = mcp_mode
        self._timeout = timeout
        self._use_mcp = False
        self._mcp: _McpStdioSession | None = None
        self._mcp_error: str | None = None

        # Guarantee the schema (incl. analysis_runs) exists for either
        # transport; ensure_schema is idempotent.
        from src.db_schema import ensure_schema

        with get_connection(self._db_path) as conn:
            ensure_schema(conn)

        if mcp_mode != "direct":
            try:
                self._mcp = _McpStdioSession(self._db_path, timeout=timeout)
                self._use_mcp = True
                # A real round-trip proves the stdio server is usable.
                self.list_analyses()
            except Exception as exc:
                self._use_mcp = False
                self._mcp_error = f"{type(exc).__name__}: {exc}"
                if self._mcp is not None:
                    self._mcp.close()
                    self._mcp = None

        if mcp_mode == "mcp" and not self._use_mcp:
            raise RuntimeError(
                "MCP transport requested but the stdio server is unavailable: "
                f"{self._mcp_error or 'unknown error'}"
            )

    # -- MCP stdio transport -------------------------------------------------

    def _mcp_call(self, name: str, arguments: dict[str, Any]) -> Any:
        """Invoke one MCP tool on the client's long-lived stdio session."""
        if self._mcp is None:
            raise RuntimeError("No MCP session on this client")
        return parse_tool_result(name, self._mcp.call(name, arguments))

    # -- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        """Shut the MCP session (and its server subprocess) down."""
        if self._mcp is not None:
            self._mcp.close()
            self._mcp = None
        self._use_mcp = False

    def __enter__(self) -> "AnalysisClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - best-effort cleanup
        try:
            self.close()
        except Exception:
            pass

    # -- direct (fallback) transport ----------------------------------------

    def _direct_select(self, sql: str) -> list[dict[str, Any]]:
        statement = assert_select_only(sql)  # reuse the server's read-only guard
        uri = f"file:{Path(self._db_path).as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(statement).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    def _direct_provenance(self, paper_id: int) -> dict[str, Any]:
        with get_connection(self._db_path) as conn:
            paper = conn.execute(
                "SELECT id, title, year, doi FROM papers WHERE id = ?", (paper_id,)
            ).fetchone()
            sources = conn.execute(
                "SELECT s.id, s.name, s.description "
                "FROM paper_sources ps JOIN sources s ON s.id = ps.source_id "
                "WHERE ps.paper_id = ? ORDER BY s.name",
                (paper_id,),
            ).fetchall()
            queries = conn.execute(
                "SELECT q.id, q.platform, q.query_text, q.run_at "
                "FROM paper_queries pq JOIN queries q ON q.id = pq.query_id "
                "WHERE pq.paper_id = ? ORDER BY q.id",
                (paper_id,),
            ).fetchall()
            parents = conn.execute(
                "SELECT e.parent_paper_id AS paper_id, p.title, e.depth, e.discovered_at "
                "FROM snowball_edges e LEFT JOIN papers p ON p.id = e.parent_paper_id "
                "WHERE e.child_paper_id = ? ORDER BY e.parent_paper_id",
                (paper_id,),
            ).fetchall()
            children = conn.execute(
                "SELECT e.child_paper_id AS paper_id, p.title, e.depth, e.discovered_at "
                "FROM snowball_edges e LEFT JOIN papers p ON p.id = e.child_paper_id "
                "WHERE e.parent_paper_id = ? ORDER BY e.child_paper_id",
                (paper_id,),
            ).fetchall()
        return {
            "paper_id": paper_id,
            "found": paper is not None,
            "title": paper["title"] if paper is not None else None,
            "sources": [dict(r) for r in sources],
            "queries": [dict(r) for r in queries],
            "snowball_parents": [dict(r) for r in parents],
            "snowball_children": [dict(r) for r in children],
        }

    def _direct_store(self, name: str, result_json: str) -> int:
        # Write directly to THIS client's database (not the server's module
        # global), so the fallback path persists to the correct file.
        with get_connection(self._db_path) as conn:
            cursor = conn.execute(
                "INSERT OR REPLACE INTO analysis_runs (name, result_json) "
                "VALUES (?, ?) RETURNING id",
                (name, result_json),
            )
            return int(cursor.fetchone()["id"])

    def _direct_list_analysis(self) -> list[dict[str, Any]]:
        with get_connection(self._db_path) as conn:
            rows = conn.execute(
                "SELECT id, name, generated_at FROM analysis_runs ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    # -- public API (transport-agnostic) -------------------------------------

    @property
    def db_path(self) -> str:
        return self._db_path

    @property
    def uses_mcp(self) -> bool:
        return self._use_mcp

    @property
    def mcp_error(self) -> str | None:
        """Why the MCP probe failed (``None`` when MCP is in use / not tried)."""
        return self._mcp_error

    def select(self, sql: str) -> list[dict[str, Any]]:
        """Run a read-only aggregate SELECT (through MCP or the fallback).

        Raises:
            ValueError: If the statement is rejected by the read-only guard
                (on both transports).
        """
        if self._use_mcp:
            return _as_rows(
                "execute_select", self._mcp_call("execute_select", {"sql": sql})
            )
        return self._direct_select(sql)

    def provenance(self, paper_id: int) -> dict[str, Any]:
        """Return provenance for ``paper_id`` (through MCP or the fallback)."""
        if self._use_mcp:
            payload = self._mcp_call("get_paper_provenance", {"paper_id": paper_id})
            if isinstance(payload, list) and len(payload) == 1:
                payload = payload[0]
            if not isinstance(payload, dict):
                raise ValueError(f"get_paper_provenance returned {payload!r}")
            return payload
        return self._direct_provenance(paper_id)

    def store(self, name: str, data: Any) -> int:
        """Persist an analysis result (``data`` is JSON-serialised if needed)."""
        payload = data if isinstance(data, str) else json.dumps(data, default=str)
        if self._use_mcp:
            row_id = self._mcp_call(
                "store_analysis", {"name": name, "result_json": payload}
            )
            if isinstance(row_id, bool) or not isinstance(row_id, (int, float, str)):
                raise ValueError(f"store_analysis returned {row_id!r}")
            return int(row_id)
        return self._direct_store(name, payload)

    def list_analyses(self) -> list[dict[str, Any]]:
        """List all stored analyses (through MCP or the fallback)."""
        if self._use_mcp:
            return _as_rows("list_analysis", self._mcp_call("list_analysis", {}))
        return self._direct_list_analysis()


# ---------------------------------------------------------------------------
# Stat builders (efficient aggregate queries)
# ---------------------------------------------------------------------------


def corpus_by_source(client: AnalysisClient) -> list[dict[str, Any]]:
    """Papers per ``sources.name`` (deduped) via the ``paper_sources`` junction."""
    return client.select(
        "SELECT s.name AS source, COUNT(DISTINCT ps.paper_id) AS n_papers "
        "FROM sources s JOIN paper_sources ps ON ps.source_id = s.id "
        "GROUP BY s.name ORDER BY n_papers DESC"
    )


#: SQL CASE mirroring :func:`_categorise` so the distinct-paper counts use the
#: exact same category definition as the per-source sums.
_CATEGORY_CASE = (
    "CASE WHEN lower(s.name) LIKE '%snowball%' THEN 'snowball' "
    "WHEN lower(s.name) LIKE 'query%' "
    "  OR lower(s.name) IN ('acm', 'ieee', 'springer', 'semantic scholar') "
    "THEN 'database_query' ELSE 'other' END"
)


def _categorise(name: str) -> str:
    """Collapse a source name into ``snowball`` / ``database_query`` / ``other``."""
    n = (name or "").lower()
    if "snowball" in n:
        return "snowball"
    if n.startswith("query") or n in ("acm", "ieee", "springer", "semantic scholar"):
        return "database_query"
    return "other"


def corpus_by_method(client: AnalysisClient) -> dict[str, Any]:
    """Discovery-method breakdown, counted per **paper-source link**.

    ``per_source`` and ``by_category`` count *paper-source links*, **not** distinct
    papers: a paper found by several methods (e.g. a database query *and*
    backward snowballing) is counted once **in each** method, so the category
    counts can sum to more than the size of the corpus.

    The complementary distinct-paper view is reported alongside so the two are
    never confused:

    * ``by_category_distinct_papers`` — ``COUNT(DISTINCT paper_id)`` per category
      (a multi-method paper still appears in each of its categories, but only
      once per category).
    * ``n_papers_distinct`` — distinct papers with at least one source.
    * ``n_multi_method_papers`` — papers linked to more than one source, i.e. the
      overlap that makes the link counts exceed ``n_papers_distinct``.
    * ``counting`` — a one-line description of the semantics, carried into the
      persisted JSON.

    Categories: ``snowball`` (snowball-derived), ``database_query`` (query1/query2
    and similar platform queries), ``other``.
    """
    rows = corpus_by_source(client)

    by_category: dict[str, int] = {}
    for r in rows:
        cat = _categorise(r["source"])
        by_category[cat] = by_category.get(cat, 0) + int(r["n_papers"])

    distinct_rows = client.select(
        f"SELECT {_CATEGORY_CASE} AS category, COUNT(DISTINCT ps.paper_id) AS n_papers "
        "FROM sources s JOIN paper_sources ps ON ps.source_id = s.id "
        "GROUP BY category ORDER BY n_papers DESC"
    )
    distinct_total = client.select(
        "SELECT COUNT(DISTINCT paper_id) AS n FROM paper_sources"
    )
    multi = client.select(
        "SELECT COUNT(*) AS n FROM (SELECT paper_id FROM paper_sources "
        "GROUP BY paper_id HAVING COUNT(DISTINCT source_id) > 1)"
    )

    return {
        "per_source": rows,
        "by_category": by_category,
        "counting": (
            "paper-source links: a paper found by multiple methods is counted in "
            "each method, so category counts may exceed n_papers_distinct"
        ),
        "by_category_distinct_papers": {
            r["category"]: int(r["n_papers"]) for r in distinct_rows
        },
        "n_papers_distinct": int(distinct_total[0]["n"]) if distinct_total else 0,
        "n_multi_method_papers": int(multi[0]["n"]) if multi else 0,
    }


def provenance_mix(client: AnalysisClient, sample_size: int = 5) -> dict[str, Any]:
    """Provenance of the most multiply-sourced papers (via ``get_paper_provenance``).

    Picks the ``sample_size`` papers with the most distinct sources and resolves
    each one through the MCP ``get_paper_provenance`` tool (or its direct
    equivalent), reporting which sources / queries / snowball links produced it.
    This is the provenance half of the read-only MCP surface, and it makes the
    multi-method overlap reported by :func:`corpus_by_method` inspectable.
    """
    sample_size = max(0, int(sample_size))
    if sample_size == 0:
        return {"sample_size": 0, "papers": []}
    rows = client.select(
        "SELECT paper_id, COUNT(DISTINCT source_id) AS n_sources "
        "FROM paper_sources GROUP BY paper_id "
        f"ORDER BY n_sources DESC, paper_id LIMIT {sample_size}"
    )
    papers: list[dict[str, Any]] = []
    for r in rows:
        prov = client.provenance(int(r["paper_id"]))
        papers.append(
            {
                "paper_id": prov.get("paper_id"),
                "title": prov.get("title"),
                "sources": [s.get("name") for s in prov.get("sources") or []],
                "n_queries": len(prov.get("queries") or []),
                "n_snowball_parents": len(prov.get("snowball_parents") or []),
                "n_snowball_children": len(prov.get("snowball_children") or []),
            }
        )
    return {"sample_size": len(papers), "papers": papers}


def corpus_by_year(client: AnalysisClient) -> list[dict[str, Any]]:
    """Publication-year histogram (COUNT per ``year``)."""
    return client.select(
        "SELECT year, COUNT(*) AS n_papers FROM papers GROUP BY year ORDER BY year"
    )


def relevance_distribution(client: AnalysisClient) -> list[dict[str, Any]]:
    """Count of papers per 3-class ``relevance_class`` (``unlabeled`` if NULL)."""
    return client.select(
        "SELECT COALESCE(relevance_class, 'unlabeled') AS relevance_class, "
        "COUNT(*) AS n_papers FROM papers GROUP BY relevance_class ORDER BY n_papers DESC"
    )


def snowball_status(client: AnalysisClient) -> dict[str, Any]:
    """Reference-list status breakdown + edge / discovered-paper counts."""
    status_rows = client.select(
        "SELECT status, COUNT(*) AS n FROM reference_lists GROUP BY status ORDER BY n DESC"
    )
    edges = client.select("SELECT COUNT(*) AS n FROM snowball_edges")
    discovered = client.select(
        "SELECT COUNT(DISTINCT child_paper_id) AS n FROM snowball_edges"
    )
    return {
        "status_counts": status_rows,
        "edges": int(edges[0]["n"]) if edges else 0,
        "papers_discovered_via_snowball": int(discovered[0]["n"]) if discovered else 0,
    }


def evaluation_summary(client: AnalysisClient) -> dict[str, Any]:
    """List eval runs and, if data exists, per-run metrics vs the consensus.

    The run *list* is read through the MCP channel (read-only). The per-run
    metric computation uses :func:`eval_store.compute_metrics` against a local
    connection, because it is a pure aggregation over the consensus gold
    standard rather than an ad-hoc query.
    """
    runs_rows = client.select(
        "SELECT id, method, model, prompt_id, temperature, run_index "
        "FROM eval_runs ORDER BY id"
    )
    has_gt = client.select("SELECT COUNT(*) AS n FROM ground_truth_consensus")
    has_evals = client.select("SELECT COUNT(*) AS n FROM evals")
    gt_n = int(has_gt[0]["n"]) if has_gt else 0
    ev_n = int(has_evals[0]["n"]) if has_evals else 0

    if not (ev_n > 0 and gt_n > 0):
        return {
            "available": False,
            "note": "no evaluation data ingested yet",
            "n_runs": len(runs_rows),
        }

    with get_connection(client.db_path) as conn:
        runs = eval_store.list_runs(conn)
        per_run: list[dict[str, Any]] = []
        for r in runs:
            try:
                metrics = eval_store.compute_metrics(conn, r["id"])
            except Exception as exc:  # keep one bad run from breaking the rest
                metrics = {"error": str(exc)}
            per_run.append(
                {
                    "run_id": r["id"],
                    "method": r["method"],
                    "model": r["model"],
                    "prompt_id": r["prompt_id"],
                    "temperature": r["temperature"],
                    "metrics": metrics,
                }
            )
    return {"available": True, "n_runs": len(per_run), "runs": per_run}


# ---------------------------------------------------------------------------
# Plot builders (matplotlib, Agg backend)
# ---------------------------------------------------------------------------


def _ensure_dir(out_dir: str | Path) -> Path:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save(fig: "plt.Figure", out_dir: str | Path, name: str) -> str:
    d = _ensure_dir(out_dir)
    path = d / f"{name}.png"
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return str(path)


def plot_corpus_bars(
    source_stats: list[dict[str, Any]], out_dir: str | Path, name: str = "corpus_by_source"
) -> str:
    """Horizontal bar of papers per source."""
    rows = list(source_stats)
    labels = [r["source"] for r in rows][::-1]
    values = [int(r["n_papers"]) for r in rows][::-1]
    fig, ax = plt.subplots(figsize=(8, max(2.5, 0.4 * len(labels) + 1)))
    ax.barh(labels, values)
    ax.set_xlabel("Papers")
    ax.set_title("Corpus size by source")
    for i, v in enumerate(values):
        ax.text(v, i, f" {v}", va="center", fontsize=8)
    return _save(fig, out_dir, name)


def plot_years(
    year_stats: list[dict[str, Any]], out_dir: str | Path, name: str = "corpus_by_year"
) -> str:
    """Bar of papers per publication year."""
    rows = sorted(year_stats, key=lambda r: r["year"])
    years = [r["year"] for r in rows]
    values = [int(r["n_papers"]) for r in rows]
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.bar([str(y) for y in years], values)
    ax.set_xlabel("Year")
    ax.set_ylabel("Papers")
    ax.set_title("Corpus size by publication year")
    plt.xticks(rotation=45, ha="right")
    return _save(fig, out_dir, name)


def plot_snowball_status(
    snowball_stats: dict[str, Any], out_dir: str | Path, name: str = "snowball_status"
) -> str:
    """Bar of reference-list status counts (plus edge / discovered annotations)."""
    rows = snowball_stats.get("status_counts", [])
    labels = [r["status"] for r in rows]
    values = [int(r["n"]) for r in rows]
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(labels, values)
    ax.set_ylabel("References")
    ax.set_title("Snowball reference-list status")
    plt.xticks(rotation=45, ha="right")
    note = (
        f"edges={snowball_stats.get('edges', 0)}  "
        f"discovered={snowball_stats.get('papers_discovered_via_snowball', 0)}"
    )
    ax.text(0.0, 1.02, note, transform=ax.transAxes, fontsize=9)
    return _save(fig, out_dir, name)


def plot_eval_metrics(
    eval_summary: dict[str, Any], out_dir: str | Path, name: str = "eval_metrics"
) -> str:
    """Grouped bar of macro Precision / Recall / F1 across eval runs."""
    runs = eval_summary.get("runs", [])
    if not runs:
        fig, ax = plt.subplots(figsize=(6, 3))
        ax.text(0.5, 0.5, "no eval runs", ha="center")
        return _save(fig, out_dir, name)

    labels = [f"run{r['run_id']}:{r['method']}" for r in runs]
    p = [float(r["metrics"].get("macro_precision", 0.0) or 0.0) for r in runs]
    rc = [float(r["metrics"].get("macro_recall", 0.0) or 0.0) for r in runs]
    f1 = [float(r["metrics"].get("macro_f1", 0.0) or 0.0) for r in runs]
    import numpy as np

    x = np.arange(len(labels))
    width = 0.25
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.bar(x - width, p, width, label="Precision")
    ax.bar(x, rc, width, label="Recall")
    ax.bar(x + width, f1, width, label="F1")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
    ax.set_ylim(0, 1)
    ax.set_ylabel("Score")
    ax.set_title("Per-run macro metrics vs consensus")
    ax.legend()
    return _save(fig, out_dir, name)


def plot_confusion(
    eval_summary: dict[str, Any], out_dir: str | Path, name: str = "eval_confusion"
) -> str:
    """Per-run 3x3 confusion-matrix heatmaps (gold consensus vs predicted).

    This is **not** a ROC curve (it used to be misnamed ``plot_roc``):
    :func:`eval_store.compute_metrics` exposes a 3x3 confusion matrix and the
    aggregate ``auc_in_scope`` scalar, but not the per-threshold points a ROC
    curve needs, so the confusion matrix is what is rendered — and the file is
    named ``<name>_confusion.png`` accordingly.
    """
    runs = [r for r in eval_summary.get("runs", []) if "confusion" in r.get("metrics", {})]
    if not runs:
        fig, ax = plt.subplots(figsize=(6, 3))
        ax.text(0.5, 0.5, "no confusion data", ha="center")
        return _save(fig, out_dir, name)

    labels = ["in-scope", "out-of-scope", "hybrid"]
    n = len(runs)
    cols = min(n, 3)
    rows_n = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows_n, cols, figsize=(4 * cols, 3.5 * rows_n))
    axes = [axes] if n == 1 else axes.flatten()
    for i, r in enumerate(runs):
        ax = axes[i]
        conf = r["metrics"]["confusion"]
        matrix = [[int(conf[g][p]) for p in labels] for g in labels]
        im = ax.imshow(matrix, cmap="Blues")
        ax.set_xticks(range(3))
        ax.set_yticks(range(3))
        ax.set_xticklabels(labels, fontsize=8)
        ax.set_yticklabels(labels, fontsize=8)
        ax.set_xlabel("predicted", fontsize=8)
        ax.set_ylabel("gold", fontsize=8)
        ax.set_title(f"run {r['run_id']}", fontsize=9)
        for yy in range(3):
            for xx in range(3):
                ax.text(xx, yy, matrix[yy][xx], ha="center", va="center", fontsize=8)
        fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    for j in range(n, len(axes)):
        axes[j].axis("off")
    return _save(fig, out_dir, name)


# ---------------------------------------------------------------------------
# Orchestration: run_analysis
# ---------------------------------------------------------------------------


def run_analysis(
    name: str,
    client: AnalysisClient,
    kinds: tuple[str, ...] = ("corpus", "relevance", "snowball", "evaluation"),
    out_dir: str | Path = DATA_ANALYSIS_DIR,
) -> dict[str, Any]:
    """Compute the requested statistics, write JSON + plots, and persist.

    Args:
        name: Analysis name (also the JSON/plot file stem and the persisted key).
        client: An :class:`AnalysisClient`.
        kinds: Subset of ``corpus``, ``relevance``, ``snowball``, ``evaluation``.
        out_dir: Directory for the JSON + PNG outputs (default ``data/analysis``).

    Returns:
        A summary dict with ``name``, ``json``, ``plots`` and the ``stats``.
    """
    kinds = tuple(kinds)
    out_dir = _ensure_dir(out_dir)
    stats: dict[str, Any] = {}

    if "corpus" in kinds:
        stats["corpus_by_source"] = corpus_by_source(client)
        stats["corpus_by_method"] = corpus_by_method(client)
        stats["corpus_by_year"] = corpus_by_year(client)
        stats["provenance_mix"] = provenance_mix(client)
    if "relevance" in kinds:
        stats["relevance_distribution"] = relevance_distribution(client)
    if "snowball" in kinds:
        stats["snowball_status"] = snowball_status(client)
    if "evaluation" in kinds:
        stats["evaluation"] = evaluation_summary(client)

    json_path = out_dir / f"{name}.json"
    json_path.write_text(json.dumps(stats, indent=2, default=str), encoding="utf-8")

    plots: list[str] = []
    if "corpus" in kinds and stats.get("corpus_by_source") is not None:
        plots.append(plot_corpus_bars(stats["corpus_by_source"], out_dir, f"{name}_corpus"))
        plots.append(plot_years(stats["corpus_by_year"], out_dir, f"{name}_years"))
    if "snowball" in kinds and stats.get("snowball_status") is not None:
        plots.append(plot_snowball_status(stats["snowball_status"], out_dir, f"{name}_snowball"))
    if "evaluation" in kinds and stats.get("evaluation", {}).get("available"):
        plots.append(plot_eval_metrics(stats["evaluation"], out_dir, f"{name}_eval_metrics"))
        plots.append(plot_confusion(stats["evaluation"], out_dir, f"{name}_confusion"))

    client.store(name, stats)

    return {
        "name": name,
        "json": str(json_path),
        "plots": plots,
        "stats": stats,
    }


# Re-export for convenience / testability of the MCP call path.
__all__ = [
    "AnalysisClient",
    "parse_tool_result",
    "corpus_by_source",
    "corpus_by_method",
    "corpus_by_year",
    "provenance_mix",
    "relevance_distribution",
    "snowball_status",
    "evaluation_summary",
    "plot_corpus_bars",
    "plot_years",
    "plot_snowball_status",
    "plot_eval_metrics",
    "plot_confusion",
    "run_analysis",
]
