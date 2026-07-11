import hashlib
import tempfile
import unittest
from pathlib import Path

from limbo.artifacts import Artifact, ArtifactError, ArtifactStore, guess_media_type, hash_file


class ArtifactMetadataTests(unittest.TestCase):
    def test_roundtrip_serialization(self):
        art = Artifact(digest="a" * 64, size=12, media_type="application/json",
                       producer="build", logical_path="out/data.json")
        self.assertEqual(art, Artifact.from_dict(art.to_dict()))

    def test_from_dict_defaults(self):
        art = Artifact.from_dict({"digest": "b" * 64})
        self.assertEqual(0, art.size)
        self.assertEqual("application/octet-stream", art.media_type)
        self.assertIsNone(art.producer)

    def test_from_dict_rejects_bad_metadata(self):
        with self.assertRaises(ArtifactError):
            Artifact.from_dict({"size": 3})  # no digest

    def test_guess_media_type(self):
        self.assertEqual("application/json", guess_media_type("x.json"))
        self.assertEqual("text/csv", guess_media_type("data.csv"))
        self.assertEqual("application/octet-stream", guess_media_type(None))
        self.assertEqual("application/octet-stream", guess_media_type("mystery"))


class ArtifactStoreTests(unittest.TestCase):
    def test_put_bytes_is_content_addressed_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ArtifactStore(Path(tmpdir))
            data = b'{"value": 1}'
            art = store.put_bytes(data, producer="t", logical_path="out.json")

            self.assertEqual(hashlib.sha256(data).hexdigest(), art.digest)
            self.assertEqual(len(data), art.size)
            self.assertEqual("application/json", art.media_type)
            self.assertTrue(store.exists(art.digest))
            self.assertEqual(data, store.get_bytes(art.digest, verify=True))
            # Storing identical bytes returns the same digest, does not duplicate.
            self.assertEqual(art.digest, store.put_bytes(data).digest)

    def test_put_file_streams_large_content(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            source = base / "big.bin"
            payload = b"x" * (3 * 1024 * 1024 + 7)
            source.write_bytes(payload)

            store = ArtifactStore(base / "store")
            art = store.put_file(source, producer="gen", logical_path="big.bin")

            self.assertEqual(len(payload), art.size)
            self.assertEqual(hashlib.sha256(payload).hexdigest(), art.digest)
            self.assertEqual(payload, store.get_bytes(art.digest))

    def test_empty_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            source = base / "empty"
            source.write_bytes(b"")
            store = ArtifactStore(base / "store")

            art = store.put_file(source)
            self.assertEqual(0, art.size)
            self.assertEqual(hashlib.sha256(b"").hexdigest(), art.digest)
            self.assertEqual(b"", store.get_bytes(art.digest))

    def test_artifacts_survive_process_restart(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "store"
            digest = ArtifactStore(root).put_bytes(b"durable").digest
            # A fresh store instance over the same root simulates a restart.
            reopened = ArtifactStore(root)
            self.assertTrue(reopened.exists(digest))
            self.assertEqual(b"durable", reopened.get_bytes(digest))
            self.assertTrue(reopened.verify(digest))

    def test_corruption_is_detected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ArtifactStore(Path(tmpdir))
            digest = store.put_bytes(b"original").digest

            store._path(digest).write_bytes(b"corrupted")  # simulate on-disk rot

            self.assertFalse(store.verify(digest))
            with self.assertRaisesRegex(ArtifactError, "corrupted"):
                store.get_bytes(digest, verify=True)

    def test_missing_artifact_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ArtifactStore(Path(tmpdir))
            with self.assertRaisesRegex(ArtifactError, "not found"):
                store.get_bytes("0" * 64)
            self.assertFalse(store.exists("0" * 64))

    def test_hash_file_matches_hashlib(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "f"
            path.write_bytes(b"content")
            self.assertEqual(hashlib.sha256(b"content").hexdigest(), hash_file(path))


if __name__ == "__main__":
    unittest.main()
