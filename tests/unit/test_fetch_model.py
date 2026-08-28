import io
import tarfile
from pathlib import Path

from scripts.fetch_model import _extract_keep


def _add_bytes(archive: tarfile.TarFile, name: str, value: bytes) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(value)
    archive.addfile(member, io.BytesIO(value))


def test_extract_keep_preserves_lite_tokenizer_but_rejects_fp32(tmp_path: Path) -> None:
    archive_path = tmp_path / "model.tar.bz2"
    with tarfile.open(archive_path, "w:bz2") as archive:
        _add_bytes(archive, "model/model.int8.onnx", b"int8")
        _add_bytes(archive, "model/model.onnx", b"fp32")
        _add_bytes(archive, "model/tokens.txt", b"tokens")
        _add_bytes(archive, "model/bbpe.model", b"bbpe")

    target = tmp_path / "installed"
    _extract_keep(archive_path, target)

    assert (target / "model.int8.onnx").read_bytes() == b"int8"
    assert (target / "tokens.txt").read_bytes() == b"tokens"
    assert (target / "bbpe.model").read_bytes() == b"bbpe"
    assert not (target / "model.onnx").exists()
