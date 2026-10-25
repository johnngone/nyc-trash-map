import stat
import zipfile

import httpx
import pytest

from scripts.archive_security import extract_source_zip, validate_zip
from scripts.run_refresh import download_file


def _archive(tmp_path, entries, compression=zipfile.ZIP_DEFLATED):
    path = tmp_path / "source.zip"
    with zipfile.ZipFile(path, "w", compression) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return path


def test_extracts_regular_source_archive(tmp_path):
    source = _archive(tmp_path, [("lion.gdb/table", b"data")])
    extract_source_zip(source, tmp_path / "out")
    assert (tmp_path / "out/lion.gdb/table").read_bytes() == b"data"


@pytest.mark.parametrize("name", ["../escape", "/escape", "C:/escape", "dir\\escape"])
def test_rejects_unsafe_paths_before_writing(tmp_path, name):
    source = _archive(tmp_path, [(name, b"bad")])
    if "\\" in name:
        # Windows' ZIP writer normalizes separators; restore the hostile header.
        source.write_bytes(source.read_bytes().replace(name.replace("\\", "/").encode(), name.encode()))
    with pytest.raises(RuntimeError, match="unsafe member path"):
        extract_source_zip(source, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_rejects_links(tmp_path):
    info = zipfile.ZipInfo("link")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    source = _archive(tmp_path, [(info, b"../outside")])
    with pytest.raises(RuntimeError, match="link or special"):
        extract_source_zip(source, tmp_path / "out")


@pytest.mark.parametrize("limits,match", [
    ({"max_member_bytes": 4}, "individual member"),
    ({"max_bytes": 7}, "total extracted"),
    ({"max_members": 1}, "member count"),
])
def test_rejects_archive_bombs_before_writing(tmp_path, limits, match):
    source = _archive(tmp_path, [("one", b"x" * 5), ("two", b"y" * 5)])
    with pytest.raises(RuntimeError, match=match):
        extract_source_zip(source, tmp_path / "out", **limits)
    assert not (tmp_path / "out").exists()


def test_rejects_duplicate_and_unsupported_members(tmp_path):
    source = _archive(tmp_path, [("one", b"x"), ("ONE", b"y")])
    with zipfile.ZipFile(source) as archive, pytest.raises(RuntimeError, match="duplicate"):
        validate_zip(archive)
    source = _archive(tmp_path, [("one", b"x")], compression=zipfile.ZIP_LZMA)
    with zipfile.ZipFile(source) as archive, pytest.raises(RuntimeError, match="unsupported"):
        validate_zip(archive)


def test_extraction_deadline(tmp_path, monkeypatch):
    source = _archive(tmp_path, [("one", b"x")])
    clock = iter([0, 2])
    monkeypatch.setattr("scripts.archive_security.monotonic", lambda: next(clock))
    with pytest.raises(RuntimeError, match="time limit"):
        extract_source_zip(source, tmp_path / "out", max_seconds=1)


@pytest.mark.parametrize("advertised", [True, False])
def test_download_limit_with_and_without_content_length(tmp_path, advertised):
    def handler(request):
        headers = {"content-length": "8"} if advertised else {}
        return httpx.Response(200, headers=headers, stream=httpx.ByteStream(b"12345678"))
    with pytest.raises(RuntimeError, match="size limit"):
        download_file("https://source.example/archive.zip", tmp_path / "source.zip",
                      max_bytes=4, transport=httpx.MockTransport(handler))


def test_download_rejects_http_redirect_before_contacting_target(tmp_path):
    contacted = []
    def handler(request):
        contacted.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://insecure.example/archive.zip"})
    with pytest.raises(RuntimeError, match="HTTPS"):
        download_file("https://source.example/archive.zip", tmp_path / "source.zip",
                      transport=httpx.MockTransport(handler))
    assert contacted == ["https://source.example/archive.zip"]


def test_valid_download_and_deadline(tmp_path, monkeypatch):
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, headers={"content-length": "4"}, stream=httpx.ByteStream(b"data")))
    output = tmp_path / "source.zip"
    assert download_file("https://source.example/archive.zip", output,
                         transport=transport)["bytes"] == 4
    assert output.read_bytes() == b"data"
    clock = iter([0, 2])
    monkeypatch.setattr("scripts.run_refresh.monotonic", lambda: next(clock))
    with pytest.raises(RuntimeError, match="time limit"):
        download_file("https://source.example/archive.zip", output,
                      max_seconds=1, transport=transport)
