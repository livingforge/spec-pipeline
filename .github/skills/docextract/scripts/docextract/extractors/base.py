"""抽出器の共通処理。"""

from __future__ import annotations

from pathlib import Path


class ImageSaver:
    """画像バイナリを出力ディレクトリに連番で保存し、相対パスを返す。"""

    def __init__(self, output_dir: Path, images_subdir: str = "images"):
        self.output_dir = output_dir
        self.images_subdir = images_subdir
        self._count = 0

    def save(self, data: bytes, ext: str) -> str:
        self._count += 1
        ext = ext.lstrip(".").lower() or "bin"
        images_dir = self.output_dir / self.images_subdir
        images_dir.mkdir(parents=True, exist_ok=True)
        filename = f"image_{self._count:03d}.{ext}"
        (images_dir / filename).write_bytes(data)
        # JSON からの参照用に POSIX 形式の相対パスを返す
        return f"{self.images_subdir}/{filename}"
