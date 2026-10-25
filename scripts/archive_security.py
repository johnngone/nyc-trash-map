"""Resource and path limits for downloaded source archives."""

import stat
import zipfile
from pathlib import Path, PurePosixPath
from time import monotonic

MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
MAX_DOWNLOAD_SECONDS = 900
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_MEMBER_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000
MAX_EXTRACT_SECONDS = 300


def validate_zip(
    archive: zipfile.ZipFile,
    *,
    max_bytes: int = MAX_ARCHIVE_BYTES,
    max_member_bytes: int = MAX_MEMBER_BYTES,
    max_members: int = MAX_ARCHIVE_MEMBERS,
) -> list[zipfile.ZipInfo]:
    """Reject unsafe metadata before reading or creating any archive members."""
    members = archive.infolist()
    if len(members) > max_members:
        raise RuntimeError("Archive exceeds member count limit")
    total = 0
    paths: set[str] = set()
    for member in members:
        # Inspect the original header, before zipfile's platform normalization.
        name = member.orig_filename
        path = PurePosixPath(name)
        if (not name or path.is_absolute() or ".." in path.parts
                or "\\" in name or ":" in name or "\x00" in member.orig_filename):
            raise RuntimeError("Archive contains an unsafe member path")
        canonical = path.as_posix().casefold()
        if canonical in paths:
            raise RuntimeError("Archive contains duplicate member paths")
        paths.add(canonical)
        mode = member.external_attr >> 16
        kind = stat.S_IFMT(mode)
        if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise RuntimeError("Archive contains a link or special file")
        if member.flag_bits & 1:
            raise RuntimeError("Encrypted archives are not supported")
        if member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise RuntimeError("Archive uses unsupported compression")
        if member.file_size > max_member_bytes:
            raise RuntimeError("Archive exceeds individual member size limit")
        total += member.file_size
        if total > max_bytes:
            raise RuntimeError("Archive exceeds total extracted size limit")
    return members


def extract_source_zip(
    source: Path,
    destination: Path,
    *,
    max_bytes: int = MAX_ARCHIVE_BYTES,
    max_member_bytes: int = MAX_MEMBER_BYTES,
    max_members: int = MAX_ARCHIVE_MEMBERS,
    max_seconds: float = MAX_EXTRACT_SECONDS,
) -> None:
    """Stream into a fresh private directory, enforcing actual output limits too."""
    started = monotonic()
    with zipfile.ZipFile(source) as archive:
        members = validate_zip(archive, max_bytes=max_bytes,
                               max_member_bytes=max_member_bytes, max_members=max_members)
        destination.mkdir(parents=True, exist_ok=False)
        total = 0
        for member in members:
            if monotonic() - started > max_seconds:
                raise RuntimeError("Archive extraction exceeded time limit")
            target = destination.joinpath(*PurePosixPath(member.filename).parts)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with archive.open(member) as reader, target.open("xb") as writer:
                while chunk := reader.read(1024 * 1024):
                    total += len(chunk)
                    written += len(chunk)
                    if total > max_bytes or written > max_member_bytes:
                        raise RuntimeError("Archive exceeded actual extracted size limit")
                    if monotonic() - started > max_seconds:
                        raise RuntimeError("Archive extraction exceeded time limit")
                    writer.write(chunk)
