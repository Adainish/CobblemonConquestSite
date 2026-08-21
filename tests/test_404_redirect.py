import os
import sys


_ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(_ROOT, "apps"))


def test_redirect_404_routes_to_index(monkeypatch):
    from _default import controllers

    called = {}

    monkeypatch.setattr(controllers, "URL", lambda name: f"/{name}")

    def _fake_redirect(target):
        called["target"] = target
        return "redirected"

    monkeypatch.setattr(controllers, "redirect", _fake_redirect)

    result = controllers.redirect_404("missing/path")

    assert result == "redirected"
    assert called["target"] == "/index"
