#!/usr/bin/env python3
"""Tests for platform-specific export hints."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "engine"))

from utils import export_professional


def test_pdf_install_hint_windows(monkeypatch):
    monkeypatch.setattr(export_professional.platform, "system", lambda: "Windows")
    hint = export_professional._pdf_install_hint()
    assert "Pandoc + MiKTeX/TeX Live" in hint
    assert "sudo apt install" not in hint
