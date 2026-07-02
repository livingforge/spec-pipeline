"""純粋ロジックの単体テスト (フィクスチャ生成も外部依存も不要)。"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docextract.extractors.base import ImageSaver
from docextract.image_tables import _html_table_to_rows
from docextract.extractors.pdf_extractor import _group_lines_into_blocks
from docextract.models import ExtractionResult, ImageElement, TableElement, TextElement


class TestHtmlTableToRows(unittest.TestCase):
    def test_basic_table(self):
        html = "<table><tr><td>A</td><td>B</td></tr><tr><td>1</td><td>2</td></tr></table>"
        self.assertEqual(_html_table_to_rows(html), [["A", "B"], ["1", "2"]])

    def test_th_cells_and_wrapper(self):
        html = "<html><body><table><tr><th>H1</th><th>H2</th></tr></table></body></html>"
        self.assertEqual(_html_table_to_rows(html), [["H1", "H2"]])

    def test_colspan_pads_columns(self):
        html = '<table><tr><td colspan="2">wide</td><td>C</td></tr></table>'
        self.assertEqual(_html_table_to_rows(html), [["wide", "", "C"]])

    def test_whitespace_stripped(self):
        html = "<table><tr><td>  x \n </td></tr></table>"
        self.assertEqual(_html_table_to_rows(html), [["x"]])


class TestGroupLinesIntoBlocks(unittest.TestCase):
    @staticmethod
    def line(top, bottom, text="t", x0=0.0, x1=10.0):
        return {"top": top, "bottom": bottom, "text": text, "x0": x0, "x1": x1}

    def test_close_lines_merge(self):
        lines = [self.line(0, 10), self.line(14, 24)]  # gap 4 < 9
        self.assertEqual(len(_group_lines_into_blocks(lines)), 1)

    def test_distant_lines_split(self):
        lines = [self.line(0, 10), self.line(40, 50)]  # gap 30 > 9
        self.assertEqual(len(_group_lines_into_blocks(lines)), 2)

    def test_sorted_by_position(self):
        lines = [self.line(40, 50, "second"), self.line(0, 10, "first")]
        blocks = _group_lines_into_blocks(lines)
        self.assertEqual(blocks[0][0]["text"], "first")

    def test_empty(self):
        self.assertEqual(_group_lines_into_blocks([]), [])


class TestImageSaver(unittest.TestCase):
    def test_sequential_posix_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            saver = ImageSaver(Path(tmp))
            p1 = saver.save(b"a", "PNG")
            p2 = saver.save(b"b", ".jpg")
            self.assertEqual(p1, "images/image_001.png")
            self.assertEqual(p2, "images/image_002.jpg")
            self.assertEqual((Path(tmp) / p1).read_bytes(), b"a")


class TestModels(unittest.TestCase):
    def test_summary_counts_and_shapes(self):
        result = ExtractionResult(source="a.docx", file_type="docx")
        result.elements = [
            TextElement(content="hi", style="Normal"),
            TableElement(rows=[["a", "b"], ["c"]]),
            ImageElement(file="images/i.png", format="png"),
        ]
        data = result.to_dict()
        self.assertEqual(data["summary"], {"text": 1, "table": 1, "image": 1})
        table = data["elements"][1]
        self.assertEqual(table["n_rows"], 2)
        self.assertEqual(table["n_cols"], 2)

    def test_ocr_text_omitted_when_empty(self):
        with_ocr = ImageElement(file="i.png", format="png", ocr_text="text").to_dict()
        without = ImageElement(file="i.png", format="png", ocr_text=None).to_dict()
        self.assertEqual(with_ocr["ocr_text"], "text")
        self.assertNotIn("ocr_text", without)


class TestExtractErrors(unittest.TestCase):
    def test_unsupported_extension(self):
        from docextract import extract

        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "x.txt"
            bad.write_text("hello", encoding="utf-8")
            with self.assertRaises(ValueError):
                extract(bad, output_dir=tmp)

    def test_missing_file(self):
        from docextract import extract

        with self.assertRaises(FileNotFoundError):
            extract("no_such_file.docx")


if __name__ == "__main__":
    unittest.main()
