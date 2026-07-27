"""Textual TUI for browsing and managing the PUF research database.

Screens:
  - Dashboard: summary statistics
  - PaperList: browse papers with filters
  - PaperDetail: view a single paper
  - SnowballView: view snowball expansion graph

Run with:
    python -m cli.tui
    or
    puf tui
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Label,
    ListItem,
    ListView,
    Markdown,
    Static,
    TabbedContent,
    TabPane,
)
from textual.coordinate import Coordinate

from src.db import get_connection


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DB_PATH = "results.db"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------

class DashboardScreen(Screen):
    """Dashboard showing summary statistics."""

    BINDINGS = [
        Binding("p", "show_papers", "Papers"),
        Binding("s", "show_snowball", "Snowball"),
        Binding("q", "app.quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Label("PUF Research Pipeline — Dashboard", classes="title"),
            id="dashboard-container",
        )
        yield Footer()

    def on_mount(self) -> None:
        self._refresh()

    def action_show_papers(self) -> None:
        self.app.push_screen("papers")

    def action_show_snowball(self) -> None:
        self.app.push_screen("snowball")

    def _refresh(self) -> None:
        with _get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
            relevant = conn.execute("SELECT COUNT(*) FROM papers WHERE is_relevant = 1").fetchone()[0]
            evaluated = conn.execute("SELECT COUNT(*) FROM papers WHERE is_relevant IS NOT NULL").fetchone()[0]
            sources = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
            queries = conn.execute("SELECT COUNT(*) FROM queries").fetchone()[0]
            edges = conn.execute("SELECT COUNT(*) FROM snowball_edges").fetchone()[0]

        container = self.query_one("#dashboard-container")
        container.remove_children()
        container.mount(
            Static(f"Total papers: {total}", classes="stat"),
            Static(f"Evaluated: {evaluated}", classes="stat"),
            Static(f"Relevant: {relevant}", classes="stat"),
            Static(f"Sources: {sources}", classes="stat"),
            Static(f"Queries: {queries}", classes="stat"),
            Static(f"Snowball edges: {edges}", classes="stat"),
            Button("Browse Papers", variant="primary", id="btn-papers"),
            Button("Snowball View", variant="primary", id="btn-snowball"),
        )

        self.query_one("#btn-papers").focus()
        self.query_one("#btn-papers").on_click = lambda _: self.action_show_papers()
        self.query_one("#btn-snowball").on_click = lambda _: self.action_show_snowball()


class PaperListScreen(Screen):
    """Browse papers with filters."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("r", "filter_relevant", "Relevant"),
        Binding("a", "filter_all", "All"),
        Binding("enter", "view_paper", "View"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Horizontal(
            Vertical(
                Label("Filters"),
                Button("All Papers", id="filter-all"),
                Button("Relevant Only", id="filter-relevant"),
                Button("Irrelevant Only", id="filter-irrelevant"),
                Button("Unevaluated Only", id="filter-unevaluated"),
                id="filters",
            ),
            Vertical(
                DataTable(id="paper-table"),
                id="table-container",
            ),
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#paper-table", DataTable)
        table.add_column("ID", width=6)
        table.add_column("Title", width=60)
        table.add_column("Year", width=6)
        table.add_column("Relevant", width=10)
        table.add_column("Score", width=10)
        self._load_papers()

    def _load_papers(self, relevance_filter: Optional[str] = None) -> None:
        table = self.query_one("#paper-table", DataTable)
        table.clear()

        with _get_conn() as conn:
            if relevance_filter == "relevant":
                rows = conn.execute(
                    "SELECT id, title, year, is_relevant, relevance_score FROM papers WHERE is_relevant = 1 ORDER BY id"
                ).fetchall()
            elif relevance_filter == "irrelevant":
                rows = conn.execute(
                    "SELECT id, title, year, is_relevant, relevance_score FROM papers WHERE is_relevant = 0 ORDER BY id"
                ).fetchall()
            elif relevance_filter == "unevaluated":
                rows = conn.execute(
                    "SELECT id, title, year, is_relevant, relevance_score FROM papers WHERE is_relevant IS NULL ORDER BY id"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, title, year, is_relevant, relevance_score FROM papers ORDER BY id"
                ).fetchall()

        for row in rows:
            rel = "Yes" if row["is_relevant"] == 1 else ("No" if row["is_relevant"] == 0 else "?")
            score = f"{row['relevance_score']:.3f}" if row["relevance_score"] is not None else "-"
            table.add_row(
                str(row["id"]),
                row["title"][:80],
                str(row["year"]),
                rel,
                score,
            )

    def action_filter_all(self) -> None:
        self._load_papers()

    def action_filter_relevant(self) -> None:
        self._load_papers("relevant")

    def action_view_paper(self) -> None:
        table = self.query_one("#paper-table", DataTable)
        if table.row_count == 0:
            return
        row = table.coordinate_to_cell_coordinate(table.cursor_coordinate)
        paper_id = int(table.get_row(row.row)[0])
        self.app.push_screen(PaperDetailScreen(paper_id))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "filter-all":
            self._load_papers()
        elif event.button.id == "filter-relevant":
            self._load_papers("relevant")
        elif event.button.id == "filter-irrelevant":
            self._load_papers("irrelevant")
        elif event.button.id == "filter-unevaluated":
            self._load_papers("unevaluated")


class PaperDetailScreen(Screen):
    """View a single paper's details."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
    ]

    def __init__(self, paper_id: int) -> None:
        super().__init__()
        self.paper_id = paper_id

    def compose(self) -> ComposeResult:
        yield Header()
        yield Markdown(id="paper-md")
        yield Footer()

    def on_mount(self) -> None:
        with _get_conn() as conn:
            row = conn.execute(
                """
                SELECT p.*, GROUP_CONCAT(DISTINCT s.name) as sources,
                       GROUP_CONCAT(DISTINCT q.platform || ':' || q.query_text) as queries
                FROM papers p
                LEFT JOIN paper_sources ps ON p.id = ps.paper_id
                LEFT JOIN sources s ON ps.source_id = s.id
                LEFT JOIN paper_queries pq ON p.id = pq.paper_id
                LEFT JOIN queries q ON pq.query_id = q.id
                WHERE p.id = ?
                GROUP BY p.id
                """,
                (self.paper_id,),
            ).fetchone()

            if not row:
                self.query_one("#paper-md", Markdown).update("# Paper not found")
                return

            # Get snowball edges
            edges = conn.execute(
                """
                SELECT se.depth, p.title, p.id
                FROM snowball_edges se
                JOIN papers p ON se.parent_paper_id = p.id
                WHERE se.child_paper_id = ?
                UNION
                SELECT se.depth, p.title, p.id
                FROM snowball_edges se
                JOIN papers p ON se.child_paper_id = p.id
                WHERE se.parent_paper_id = ?
                """,
                (self.paper_id, self.paper_id),
            ).fetchall()

            # Get relevance evals
            evals = conn.execute(
                "SELECT method, score, is_relevant, evaluated_at FROM relevance_evals WHERE paper_id = ? ORDER BY evaluated_at DESC",
                (self.paper_id,),
            ).fetchall()

            # Get LLM decisions
            decisions = conn.execute(
                """
                SELECT d.decision, d.justification, r.model, d.excerpt_verified
                FROM decisions d
                JOIN runs r ON d.run_id = r.id
                WHERE d.paper_id = ?
                ORDER BY d.id DESC
                """,
                (self.paper_id,),
            ).fetchall()

        md = f"""# {row['title']}

**Authors:** {row['authors']}
**Year:** {row['year']}
**Publication:** {row['publication_title']}
**DOI:** {row['doi'] or 'N/A'}
**Keywords:** {row['keywords'] or 'N/A'}

## Abstract
{row['abstract']}

## Relevance
- **Is Relevant:** {row['is_relevant']}
- **Score:** {row['relevance_score']}

## Sources
{row['sources'] or 'N/A'}

## Queries
{row['queries'] or 'N/A'}

## Snowball Edges
"""
        if edges:
            for edge in edges:
                md += f"- Depth {edge['depth']}: {'parent' if edge['id'] != self.paper_id else 'child'} → {edge['title'][:60]}\n"
        else:
            md += "None\n"

        md += "\n## Relevance Evaluations\n"
        if evals:
            for ev in evals:
                md += f"- {ev['method']}: score={ev['score']:.4f}, relevant={ev['is_relevant']} ({ev['evaluated_at']})\n"
        else:
            md += "None\n"

        md += "\n## LLM Decisions\n"
        if decisions:
            for dec in decisions:
                md += f"- {dec['model']}: {dec['decision']} (verified={dec['excerpt_verified']})\n  > {dec['justification'][:200]}\n"
        else:
            md += "None\n"

        self.query_one("#paper-md", Markdown).update(md)


class SnowballScreen(Screen):
    """View snowball expansion."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Label("Snowball / Backward Search Expansion", classes="title"),
            DataTable(id="snowball-table"),
            id="snowball-container",
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#snowball-table", DataTable)
        table.add_column("Child ID", width=8)
        table.add_column("Child Title", width=50)
        table.add_column("Parent ID", width=8)
        table.add_column("Parent Title", width=50)
        table.add_column("Depth", width=6)

        with _get_conn() as conn:
            rows = conn.execute(
                """
                SELECT se.child_paper_id, se.parent_paper_id, se.depth,
                       cp.title as child_title, pp.title as parent_title
                FROM snowball_edges se
                JOIN papers cp ON se.child_paper_id = cp.id
                JOIN papers pp ON se.parent_paper_id = pp.id
                ORDER BY se.depth, se.child_paper_id
                """
            ).fetchall()

        for row in rows:
            table.add_row(
                str(row["child_paper_id"]),
                row["child_title"][:50],
                str(row["parent_paper_id"]),
                row["parent_title"][:50],
                str(row["depth"]),
            )


# ---------------------------------------------------------------------------
# Main App
# ---------------------------------------------------------------------------

class PufTUI(App):
    """Main TUI application."""

    CSS = """
    Screen {
        background: $surface;
    }
    .title {
        text-style: bold;
        color: $accent;
        padding: 1;
    }
    .stat {
        padding: 0 1;
    }
    #filters {
        width: 30;
        dock: left;
        padding: 1;
    }
    #table-container {
        height: 100%;
    }
    #dashboard-container {
        padding: 1;
    }
    #dashboard-container Button {
        margin: 1;
        width: 50%;
    }
    #snowball-container {
        padding: 1;
    }
    """

    BINDINGS = [
        Binding("q", "app.quit", "Quit"),
        Binding("d", "show_dashboard", "Dashboard"),
    ]

    SCREENS = {
        "dashboard": DashboardScreen,
        "papers": PaperListScreen,
        "snowball": SnowballScreen,
    }

    def on_mount(self) -> None:
        self.push_screen("dashboard")

    def action_show_dashboard(self) -> None:
        self.pop_screen()
        self.push_screen("dashboard")


if __name__ == "__main__":
    app = PufTUI()
    app.run()
