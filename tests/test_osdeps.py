"""OS defaults, including Windows locks and registry-free test settings."""

from pathlib import Path
import pytest

from magview import osdeps


@pytest.mark.parametrize("platform,native,scale", [
    ("darwin", True, 2.0), ("win32", False, 1.0), ("linux", False, 1.0),
])
def test_native_gui_policy(monkeypatch, platform, native, scale):
    monkeypatch.setattr(osdeps, "PLATFORM", platform)
    assert osdeps.native_gui_workarounds() is native
    assert osdeps.gui_pixel_ratio(2.0) == scale
    assert osdeps.gui_pixel_ratio(1.0) == 1.0


def test_cache_dir_follows_xdg(tmp_path, monkeypatch):
    monkeypatch.setattr(osdeps, "PLATFORM", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    assert osdeps.cache_dir() == tmp_path / "cache" / "magview"
    monkeypatch.setenv("XDG_CACHE_HOME", "not/absolute")
    assert osdeps.cache_dir() == tmp_path / "home" / ".cache" / "magview"


def test_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(osdeps, "PLATFORM", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert osdeps.data_dir() == tmp_path / ".magview"


def test_has_display(monkeypatch):
    monkeypatch.setattr(osdeps, "PLATFORM", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert not osdeps.has_display()
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")  # WSLg
    assert osdeps.has_display()


def test_vesta_candidates_walk_ancestors_once(tmp_path, monkeypatch):
    monkeypatch.setattr(osdeps, "PLATFORM", "linux")
    roots = [tmp_path / "a" / "b", tmp_path / "a" / "c"]
    ancestors = list(dict.fromkeys(p for r in roots for p in [r, *r.parents]))
    assert list(osdeps.vesta_candidates(roots)) == [
        p / "vesta_tools" / osdeps.VESTA_DIR_NAME for p in ancestors]


def test_windows_user_folders(tmp_path, monkeypatch):
    monkeypatch.setattr(osdeps, "PLATFORM", "win32")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "wsl-cache"))
    assert osdeps.cache_dir() == tmp_path / "local" / "MagView" / "Cache"
    assert osdeps.data_dir() == tmp_path / "roaming" / "MagView"
    monkeypatch.delenv("LOCALAPPDATA")
    monkeypatch.setenv("APPDATA", "relative")
    assert osdeps.cache_dir() == tmp_path / "home" / "AppData" / "Local" / "MagView" / "Cache"
    assert osdeps.data_dir() == tmp_path / "home" / "AppData" / "Roaming" / "MagView"


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_native_display(monkeypatch, platform):
    monkeypatch.setattr(osdeps, "PLATFORM", platform)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert osdeps.has_display()


def test_windows_vesta_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(osdeps, "PLATFORM", "win32")
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "programs"))
    candidates = list(osdeps.vesta_candidates([tmp_path / "project"]))
    assert tmp_path / "Apps" / "Others" / "VESTA-win64" in candidates
    assert tmp_path / "programs" / "VESTA" in candidates


def test_settings_override(tmp_path, monkeypatch):
    monkeypatch.delenv("MAGVIEW_SETTINGS_DIR", raising=False)
    assert osdeps.settings_file() is None
    monkeypatch.setenv("MAGVIEW_SETTINGS_DIR", str(tmp_path / "settings"))
    path = osdeps.settings_file()
    assert path == tmp_path / "settings" / "MagView.ini" and path.parent.is_dir()


@pytest.mark.parametrize("platform,failures", [("win32", 2), ("linux", 0)])
def test_atomic_replace(tmp_path, monkeypatch, platform, failures):
    monkeypatch.setattr(osdeps, "PLATFORM", platform)
    source, target = tmp_path / "tmp.json", tmp_path / "保存.json"
    source.write_text("new", encoding="utf-8")
    target.write_text("old", encoding="utf-8")
    replace = osdeps.os.replace
    calls = []

    def locked(src, dst):
        calls.append(1)
        if len(calls) <= failures:
            assert target.read_text(encoding="utf-8") == "old"
            raise PermissionError("temporary scanner lock")
        replace(src, dst)

    monkeypatch.setattr(osdeps.os, "replace", locked)
    monkeypatch.setattr(osdeps.time, "sleep", lambda _: None)
    osdeps.atomic_replace(source, target)
    assert len(calls) == failures + 1
    assert target.read_text(encoding="utf-8") == "new" and not source.exists()


@pytest.mark.parametrize("platform,attempts", [("win32", 3), ("linux", 1)])
def test_atomic_replace_propagates_persistent_lock(tmp_path, monkeypatch, platform, attempts):
    monkeypatch.setattr(osdeps, "PLATFORM", platform)
    calls = []

    def locked(*_):
        calls.append(1)
        raise PermissionError("locked")

    monkeypatch.setattr(osdeps.os, "replace", locked)
    monkeypatch.setattr(osdeps.time, "sleep", lambda _: None)
    with pytest.raises(PermissionError):
        osdeps.atomic_replace(tmp_path / "tmp", tmp_path / "target")
    assert len(calls) == attempts
