"""magview.osdeps: the OS-specific paths and lookups (Linux/WSL behavior)."""

from pathlib import Path

from magview import osdeps


def test_cache_dir_follows_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    assert osdeps.cache_dir() == tmp_path / "cache" / "magview"
    monkeypatch.setenv("XDG_CACHE_HOME", "not/absolute")
    assert osdeps.cache_dir() == tmp_path / "home" / ".cache" / "magview"


def test_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert osdeps.data_dir() == tmp_path / ".magview"


def test_has_display(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert not osdeps.has_display()
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")  # WSLg
    assert osdeps.has_display()


def test_vesta_candidates_walk_ancestors_once():
    got = list(osdeps.vesta_candidates([Path("/a/b"), Path("/a/c")]))
    tail = Path("vesta_tools") / osdeps.VESTA_DIR_NAME
    assert got == [Path("/a/b") / tail, Path("/a") / tail, Path("/") / tail, Path("/a/c") / tail]
