"""base.py の ImageSaver を検証する。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from docextract.extractors.base import ImageSaver


def test_sequential_naming_and_return_path(tmp_path):
    saver = ImageSaver(tmp_path)
    p1 = saver.save(b"a", "png")
    p2 = saver.save(b"b", "png")
    assert p1 == "images/image_001.png"
    assert p2 == "images/image_002.png"


def test_files_written_with_correct_bytes(tmp_path):
    saver = ImageSaver(tmp_path)
    rel = saver.save(b"\x89PNG-data", "png")
    assert (tmp_path / rel).read_bytes() == b"\x89PNG-data"


def test_images_dir_created_lazily(tmp_path):
    saver = ImageSaver(tmp_path)
    assert not (tmp_path / "images").exists()  # 保存前は作られない
    saver.save(b"x", "png")
    assert (tmp_path / "images").is_dir()


@pytest.mark.parametrize(
    "given, expected_ext",
    [
        (".PNG", "png"),          # 先頭ドット + 大文字
        ("JPEG", "jpeg"),          # ドットなし
        (".JpG", "jpg"),           # 大小混在
        ("..png", "png"),          # 連続ドットは全て除去
        ("", "bin"),               # 空 -> bin
        (".", "bin"),              # ドットのみ -> lstrip 後は空 -> bin
    ],
)
def test_extension_normalization(tmp_path, given, expected_ext):
    saver = ImageSaver(tmp_path)
    rel = saver.save(b"x", given)
    assert rel.endswith(f".{expected_ext}")


def test_custom_images_subdir(tmp_path):
    saver = ImageSaver(tmp_path, images_subdir="assets")
    rel = saver.save(b"x", "png")
    assert rel == "assets/image_001.png"
    assert (tmp_path / "assets" / "image_001.png").exists()


def test_zero_padding_only_up_to_999(tmp_path):
    # 連番は 3 桁ゼロ埋め。1000 枚目以降は桁があふれる (仕様確認)
    saver = ImageSaver(tmp_path)
    saver._count = 999
    rel = saver.save(b"x", "png")
    assert rel == "images/image_1000.png"


def test_returned_path_is_posix_style(tmp_path):
    # Windows 上でも JSON 参照用にスラッシュ区切りを返す
    saver = ImageSaver(tmp_path)
    rel = saver.save(b"x", "png")
    assert "\\" not in rel
    assert "/" in rel
