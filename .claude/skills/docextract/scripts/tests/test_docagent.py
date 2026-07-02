"""docagent データ操作 API のユニットテスト。

フィクスチャ (result.json 相当) はテスト内で生成するため、docextract の実行も
ネットワークも不要。一時ディレクトリ上のストアに対して操作する。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docagent import Library, DocAgentError, doc_id_from_source
from docagent.store import BUILTIN_CATEGORIES


def make_result(source: str, texts, tables=None, ocr=None) -> dict:
    elements = []
    for i, t in enumerate(texts, 1):
        elements.append({"type": "text", "content": t, "style": "Normal", "location": {"order": i}})
    for tb in tables or []:
        elements.append({"type": "table", "n_rows": len(tb), "n_cols": len(tb[0]), "rows": tb, "location": {}})
    for o in ocr or []:
        elements.append({"type": "image", "file": "images/x.png", "ocr_text": o, "location": {}})
    summary = {"text": len(texts)}
    if tables:
        summary["table"] = len(tables)
    if ocr:
        summary["image"] = len(ocr)
    return {
        "source": source,
        "file_type": Path(source).suffix.lstrip(".").lower(),
        "metadata": {"title": None, "author": "tester"},
        "summary": summary,
        "elements": elements,
    }


class DocAgentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = self.root / "store" / "library.json"
        self.cats = self.root / "store" / "categories.json"

    def tearDown(self):
        self.tmp.cleanup()

    def _write_result(self, name: str, **kw) -> Path:
        p = self.root / f"{name}_result.json"
        p.write_text(json.dumps(make_result(name, **kw), ensure_ascii=False), encoding="utf-8")
        return p

    def _lib(self) -> Library:
        return Library.load(self.store, self.cats)

    # ── ID 生成 ──
    def test_id_from_source(self):
        self.assertEqual(doc_id_from_source("report.docx"), "report_docx")
        self.assertEqual(doc_id_from_source("a/b/売上.xlsx"), "売上_xlsx")
        self.assertEqual(doc_id_from_source("no ext file"), "no_ext_file")

    # ── 取り込み ──
    def test_add_from_result(self):
        rp = self._write_result("report.docx", texts=["月次売上の報告です。"])
        lib = self._lib()
        entry = lib.add_from_result(rp)
        lib.save()
        self.assertEqual(entry["id"], "report_docx")
        self.assertEqual(entry["file_type"], "docx")
        self.assertEqual(entry["status"], "registered")
        self.assertIsNone(entry["category"])
        self.assertIn("月次売上", entry["preview"])
        self.assertTrue(self.store.exists())

    def test_add_duplicate_requires_overwrite(self):
        rp = self._write_result("report.docx", texts=["x"])
        lib = self._lib()
        lib.add_from_result(rp)
        with self.assertRaises(DocAgentError):
            lib.add_from_result(rp)
        # overwrite は既存の分析結果を保持する
        lib.set_category("report_docx", BUILTIN_CATEGORIES[0])
        lib.set_summary("report_docx", "要約", ["a"])
        entry = lib.add_from_result(rp, overwrite=True)
        self.assertEqual(entry["category"], BUILTIN_CATEGORIES[0])
        self.assertEqual(entry["summary"], "要約")
        self.assertEqual(entry["status"], "analyzed")

    # ── 分類・要約と status 遷移 ──
    def test_status_transitions(self):
        rp = self._write_result("report.docx", texts=["x"])
        lib = self._lib()
        lib.add_from_result(rp)
        self.assertEqual(lib.get("report_docx")["status"], "registered")
        lib.set_category("report_docx", "報告・レポート")
        self.assertEqual(lib.get("report_docx")["status"], "registered")  # 要約がまだ
        lib.set_summary("report_docx", "報告書の要約。")
        self.assertEqual(lib.get("report_docx")["status"], "analyzed")

    def test_reject_unknown_category(self):
        rp = self._write_result("report.docx", texts=["x"])
        lib = self._lib()
        lib.add_from_result(rp)
        with self.assertRaises(DocAgentError):
            lib.set_category("report_docx", "存在しないカテゴリ")
        # force で許可
        lib.set_category("report_docx", "臨時カテゴリ", force=True)
        self.assertEqual(lib.get("report_docx")["category"], "臨時カテゴリ")

    def test_update_combined(self):
        rp = self._write_result("plan.pptx", texts=["新機能の提案。"])
        lib = self._lib()
        lib.add_from_result(rp)
        doc = lib.update("plan_pptx", category="計画・提案", summary="提案の要約。", keywords=["新機能", "提案"])
        self.assertEqual(doc["category"], "計画・提案")
        self.assertEqual(doc["keywords"], ["新機能", "提案"])
        self.assertEqual(doc["status"], "analyzed")

    # ── 参照系 ──
    def test_query_and_stats(self):
        lib = self._lib()
        lib.add_from_result(self._write_result("a.docx", texts=["契約条項について。"]))
        lib.add_from_result(self._write_result("b.pdf", texts=["会議の決定事項。"]))
        lib.update("a_docx", category="契約・法務", summary="契約の要約。", keywords=["契約"])
        lib.update("b_pdf", category="議事録", summary="議事の要約。", keywords=["会議"])
        lib.save()

        fresh = self._lib()
        self.assertEqual(len(fresh.query(category="契約・法務")), 1)
        self.assertEqual(len(fresh.query(status="analyzed")), 2)
        self.assertEqual(len(fresh.query(keyword="会議")), 1)
        self.assertEqual(len(fresh.query(text="契約条項")), 1)  # preview へのマッチ
        stats = fresh.stats()
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["by_category"]["契約・法務"], 1)
        self.assertEqual(stats["by_status"]["analyzed"], 2)

    def test_remove(self):
        lib = self._lib()
        lib.add_from_result(self._write_result("a.docx", texts=["x"]))
        lib.remove("a_docx")
        self.assertEqual(len(lib.documents), 0)
        with self.assertRaises(DocAgentError):
            lib.get("a_docx")

    def test_get_missing_raises(self):
        with self.assertRaises(DocAgentError):
            self._lib().get("nope")

    # ── カテゴリ定義 ──
    def test_categories_from_file(self):
        self.cats.parent.mkdir(parents=True, exist_ok=True)
        self.cats.write_text(json.dumps({"categories": ["X", "Y"]}, ensure_ascii=False), encoding="utf-8")
        lib = self._lib()
        self.assertEqual(lib.categories, ["X", "Y"])
        lib.add_from_result(self._write_result("a.docx", texts=["x"]))
        lib.set_category("a_docx", "X")  # ファイル定義のカテゴリは通る
        with self.assertRaises(DocAgentError):
            lib.set_category("a_docx", "報告・レポート")  # 組み込みでもファイルに無ければ拒否

    def test_default_categories_when_no_file(self):
        lib = self._lib()
        self.assertEqual(lib.categories, BUILTIN_CATEGORIES)

    # ── preview は上限で切られる ──
    def test_preview_truncated(self):
        long_text = "あ" * 2000
        rp = self._write_result("big.docx", texts=[long_text])
        lib = self._lib()
        entry = lib.add_from_result(rp)
        self.assertLessEqual(len(entry["preview"]), 600)

    # ── text (本文テキストのみの軽量ビュー) ──
    def test_extract_text(self):
        rp = self._write_result(
            "mix.docx",
            texts=["第一段落。", "第二段落。"],
            tables=[[["品名", "数量"], ["りんご", "3"]]],
            ocr=["画像内の文字"],
        )
        lib = self._lib()
        lib.add_from_result(rp)
        out = lib.extract_text("mix_docx")
        self.assertEqual(out["id"], "mix_docx")
        self.assertFalse(out["truncated"])
        self.assertIn("第一段落。", out["text"])
        self.assertIn("品名 | 数量", out["text"])
        self.assertIn("りんご | 3", out["text"])
        self.assertIn("[画像OCR] 画像内の文字", out["text"])
        self.assertNotIn("location", out["text"])  # レイアウト情報は落ちる

    def test_extract_text_max_chars(self):
        rp = self._write_result("big.docx", texts=["あ" * 2000])
        lib = self._lib()
        lib.add_from_result(rp)
        out = lib.extract_text("big_docx", max_chars=100)
        self.assertTrue(out["truncated"])
        self.assertEqual(len(out["text"]), 100)
        self.assertEqual(out["total_chars"], 2000)

    def test_extract_text_missing_result(self):
        rp = self._write_result("gone.docx", texts=["x"])
        lib = self._lib()
        lib.add_from_result(rp)
        rp.unlink()
        with self.assertRaises(DocAgentError):
            lib.extract_text("gone_docx")

    # ── prep (分析準備の複合操作) ──
    def test_prep_registers_from_path(self):
        rp = self._write_result("report.docx", texts=["月次売上の報告です。"])
        lib = self._lib()
        out = lib.prep(str(rp))
        self.assertEqual(out["id"], "report_docx")
        self.assertEqual(out["status"], "registered")
        self.assertFalse(out["already_analyzed"])
        self.assertEqual(out["categories"], BUILTIN_CATEGORIES)
        self.assertIn("月次売上", out["text"])
        self.assertIn("docagent set report_docx", out["next_action"])
        self.assertTrue(self.store.exists())  # 登録時はストアも保存される

    def test_prep_by_id_preserves_analysis_and_skips(self):
        rp = self._write_result("report.docx", texts=["x"])
        lib = self._lib()
        lib.add_from_result(rp)
        lib.update("report_docx", category="報告・レポート", summary="要約。", keywords=["a"])
        lib.save()

        out = self._lib().prep("report_docx")
        self.assertTrue(out["already_analyzed"])
        self.assertEqual(out["category"], "報告・レポート")
        self.assertNotIn("text", out)  # 解析済みは本文抜粋を返さない
        self.assertIn("スキップ", out["next_action"])

        # パスで再 prep しても分析結果は保持される
        out2 = self._lib().prep(str(rp))
        self.assertTrue(out2["already_analyzed"])
        self.assertEqual(out2["category"], "報告・レポート")

    def test_prep_max_chars(self):
        rp = self._write_result("big.docx", texts=["あ" * 2000])
        out = self._lib().prep(str(rp), max_chars=100)
        self.assertTrue(out["text_truncated"])
        self.assertEqual(len(out["text"]), 100)

    def test_prep_unknown_target_raises(self):
        with self.assertRaises(DocAgentError):
            self._lib().prep("nope")

    def test_prep_missing_result_falls_back_to_preview(self):
        rp = self._write_result("gone.docx", texts=["中身のテキスト"])
        lib = self._lib()
        lib.add_from_result(rp)
        lib.save()
        rp.unlink()
        out = self._lib().prep("gone_docx")
        self.assertIsNone(out["text"])
        self.assertIn("中身のテキスト", out["preview"])

    # ── 集約 export ──
    def test_export_shape(self):
        lib = self._lib()
        lib.add_from_result(self._write_result("a.docx", texts=["x"]))
        data = lib.export()
        self.assertEqual(set(data), {"version", "categories", "documents"})
        self.assertEqual(len(data["documents"]), 1)


if __name__ == "__main__":
    unittest.main()
