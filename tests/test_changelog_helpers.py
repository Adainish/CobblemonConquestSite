import datetime
import os
import sys
from types import SimpleNamespace


_ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(_ROOT, "apps"))


def test_slugify_normalizes_titles():
    from _default import controllers

    assert controllers._slugify("Balance Update: Phase 2!") == "balance-update-phase-2"


def test_render_markdown_readme_escapes_unsafe_html():
    from _default import controllers

    rendered = controllers._render_markdown_readme(
        "## Added\n- New quest board\n- Visit [the wiki](https://example.com)\n\n<script>alert(1)</script>"
    )

    assert "<h2>Added</h2>" in rendered
    assert "<li>New quest board</li>" in rendered
    assert 'href="https://example.com"' in rendered
    assert "<script>" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered


def test_changelog_url_uses_date_and_slug(monkeypatch):
    from _default import controllers

    monkeypatch.setattr(controllers, "URL", lambda path: f"/{path}")
    row = SimpleNamespace(
        id=12,
        title="Bug Fix Roundup",
        content="Fixed bugs",
        created_on=datetime.datetime(2026, 9, 4, 21, 10, tzinfo=datetime.timezone.utc),
    )

    assert controllers._changelog_url(row) == "/changelog/2026/09/04/12/bug-fix-roundup"
