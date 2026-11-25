"""Tests for the on-disk ETag cache (sources/github/cache.py)."""

from second_source.sources.github.cache import DiskCache, default_cache_dir


def test_round_trip(tmp_path):
    cache = DiskCache(tmp_path)
    cache.put("https://api.github.com/a", '"etag-1"', {"items": [1, 2]})
    assert cache.get("https://api.github.com/a") == ('"etag-1"', {"items": [1, 2]})


def test_missing_entry_is_a_miss(tmp_path):
    assert DiskCache(tmp_path).get("https://api.github.com/none") is None


def test_corrupt_file_is_a_miss(tmp_path):
    cache = DiskCache(tmp_path)
    cache.put("https://api.github.com/a", '"e"', {})
    cache._path("https://api.github.com/a").write_text("{not json")
    assert cache.get("https://api.github.com/a") is None


def test_entry_for_a_different_url_is_ignored(tmp_path):
    cache = DiskCache(tmp_path)
    cache.put("https://api.github.com/a", '"e"', {"n": 1})
    # Simulate a collision: copy a's file to b's location.
    b_path = cache._path("https://api.github.com/b")
    b_path.parent.mkdir(parents=True, exist_ok=True)
    b_path.write_text(cache._path("https://api.github.com/a").read_text())
    assert cache.get("https://api.github.com/b") is None


def test_unwritable_directory_does_not_raise(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    DiskCache(blocker).put("https://api.github.com/a", '"e"', {})  # must not raise


def test_default_dir_respects_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert default_cache_dir() == tmp_path / "second-source" / "github"


def test_responses_with_code_are_never_cached(tmp_path):
    # Security review 1, M5: a PR's file list carries diff patches (code).
    cache = DiskCache(tmp_path)
    cache.put("https://api/files", '"e1"', [{"filename": "a.py", "patch": "+secret()"}])
    assert cache.get("https://api/files") is None
    assert not any(tmp_path.rglob("*.json"))


def test_cache_files_are_private_to_the_owner(tmp_path):
    cache = DiskCache(tmp_path / "c")
    cache.put("https://api/x", '"e1"', {"title": "private PR"})
    (entry,) = (tmp_path / "c").rglob("*.json")
    assert entry.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "c").stat().st_mode & 0o777 == 0o700
    assert entry.parent.stat().st_mode & 0o777 == 0o700
