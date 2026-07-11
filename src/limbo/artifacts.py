"""Content-addressed artifact store.

Task outputs can be ingested into an :class:`ArtifactStore`, which addresses
every blob by the SHA-256 digest of its contents. Because the address *is* the
digest, storing the same bytes twice is idempotent, retrieval can verify
integrity, and on-disk corruption is detectable by recomputing the hash.

An :class:`Artifact` is the metadata record that a run manifest keeps for each
output: the digest and size that identify the blob, plus the media type,
producing task, and logical (pipeline-relative) path that describe it.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Dict, Optional

from limbo.errors import LimboError

_CHUNK = 1024 * 1024
DEFAULT_MEDIA_TYPE = "application/octet-stream"


class ArtifactError(LimboError):
    """Raised when an artifact cannot be stored, read, or verified."""


@dataclass(frozen=True)
class Artifact:
    """Metadata describing one stored blob."""

    digest: str
    size: int
    media_type: str = DEFAULT_MEDIA_TYPE
    producer: Optional[str] = None
    logical_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "digest": self.digest,
            "size": self.size,
            "media_type": self.media_type,
            "producer": self.producer,
            "logical_path": self.logical_path,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Artifact":
        if not isinstance(data, dict) or not isinstance(data.get("digest"), str):
            raise ArtifactError("invalid artifact metadata")
        return cls(
            digest=data["digest"],
            size=int(data.get("size", 0)),
            media_type=data.get("media_type") or DEFAULT_MEDIA_TYPE,
            producer=data.get("producer"),
            logical_path=data.get("logical_path"),
        )


def hash_file(path: Path) -> str:
    """Return the SHA-256 hex digest of a file, streamed in chunks."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as reader:
        for chunk in iter(lambda: reader.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def guess_media_type(name: Optional[str]) -> str:
    """Best-effort media type from a file name, falling back to octet-stream."""

    if not name:
        return DEFAULT_MEDIA_TYPE
    guessed, _ = mimetypes.guess_type(name)
    return guessed or DEFAULT_MEDIA_TYPE


class ArtifactStore:
    """A local, disk-backed, content-addressed blob store."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._objects = self.root / "objects"

    def _path(self, digest: str) -> Path:
        if not isinstance(digest, str) or len(digest) < 3:
            raise ArtifactError(f"invalid digest {digest!r}")
        return self._objects / digest[:2] / digest[2:]

    def exists(self, digest: str) -> bool:
        return self._path(digest).exists()

    def put_bytes(self, data: bytes, *, media_type: Optional[str] = None,
                  producer: Optional[str] = None, logical_path: Optional[str] = None) -> Artifact:
        digest = hashlib.sha256(data).hexdigest()
        self._commit(digest, lambda handle: handle.write(data))
        return Artifact(
            digest=digest,
            size=len(data),
            media_type=media_type or guess_media_type(logical_path),
            producer=producer,
            logical_path=logical_path,
        )

    def put_file(self, path: Path, *, media_type: Optional[str] = None,
                 producer: Optional[str] = None, logical_path: Optional[str] = None) -> Artifact:
        source = Path(path)
        digest_obj = hashlib.sha256()
        size = 0

        def stream(handle: BinaryIO) -> None:
            nonlocal size
            with source.open("rb") as reader:
                for chunk in iter(lambda: reader.read(_CHUNK), b""):
                    digest_obj.update(chunk)
                    handle.write(chunk)
                    size += len(chunk)

        try:
            digest = self._commit(None, stream, digest_obj)
        except OSError as exc:
            raise ArtifactError(f"could not read {source}: {exc}") from exc
        return Artifact(
            digest=digest,
            size=size,
            media_type=media_type or guess_media_type(logical_path or source.name),
            producer=producer,
            logical_path=logical_path,
        )

    def get_bytes(self, digest: str, *, verify: bool = False) -> bytes:
        path = self._require(digest)
        data = path.read_bytes()
        if verify and hashlib.sha256(data).hexdigest() != digest:
            raise ArtifactError(f"artifact {digest} is corrupted (content does not match digest)")
        return data

    def open(self, digest: str) -> BinaryIO:
        return self._require(digest).open("rb")

    def verify(self, digest: str) -> bool:
        """Recompute the stored blob's hash and confirm it matches its address."""

        path = self._require(digest)
        actual = hashlib.sha256()
        with path.open("rb") as reader:
            for chunk in iter(lambda: reader.read(_CHUNK), b""):
                actual.update(chunk)
        return actual.hexdigest() == digest

    # -- internals -------------------------------------------------------

    def _require(self, digest: str) -> Path:
        path = self._path(digest)
        if not path.exists():
            raise ArtifactError(f"artifact {digest} not found")
        return path

    def _commit(self, digest, writer, digest_obj: Optional["hashlib._Hash"] = None) -> str:
        """Write via a temp file, then atomically place it at its digest path.

        When ``digest`` is known up front pass it directly; otherwise pass a
        seeded ``digest_obj`` that ``writer`` updates while streaming, and the
        final digest is read from it.
        """

        self._objects.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".artifact-", dir=str(self._objects))
        try:
            with os.fdopen(descriptor, "wb") as handle:
                writer(handle)
            if digest is None:
                digest = digest_obj.hexdigest()  # type: ignore[union-attr]
            final = self._path(digest)
            if final.exists():
                os.unlink(temporary)  # already stored; content-addressed put is idempotent
                return digest
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporary, final)
            return digest
        except OSError as exc:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise ArtifactError(f"could not store artifact: {exc}") from exc
