"""quiet.py — サードパーティのログノイズ抑制を検証する。

抽出パイプラインは標準出力を LLM/エージェントに渡すため、OCR/表検出の依存が
出すログ/警告を ERROR に寄せてノイズを抑える。ここでは docextract 自身の
挙動 (冪等・env での無効化・対象ロガーの重大度) だけを検証し、実際の
onnxruntime 等の導入には依存しない。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docextract import quiet


def test_silence_raises_noisy_logger_levels(monkeypatch):
    monkeypatch.delenv("DOCEXTRACT_VERBOSE_DEPS", raising=False)
    quiet._reset_for_tests()
    # 事前に情報レベルに落としておき、抑制で ERROR まで上がることを確認
    logging.getLogger("rapidocr").setLevel(logging.INFO)
    logging.getLogger("rapid_table").setLevel(logging.DEBUG)

    quiet.silence_third_party()

    assert logging.getLogger("rapidocr").level == logging.ERROR
    assert logging.getLogger("rapid_table").level == logging.ERROR


def test_silence_is_idempotent(monkeypatch):
    monkeypatch.delenv("DOCEXTRACT_VERBOSE_DEPS", raising=False)
    quiet._reset_for_tests()
    quiet.silence_third_party()
    # 2 回目以降は素通り: 手動で下げたレベルを上書きし直さない
    logging.getLogger("onnxruntime").setLevel(logging.INFO)
    quiet.silence_third_party()
    assert logging.getLogger("onnxruntime").level == logging.INFO


def test_verbose_env_disables_suppression(monkeypatch):
    monkeypatch.setenv("DOCEXTRACT_VERBOSE_DEPS", "1")
    quiet._reset_for_tests()
    logging.getLogger("rapid_layout").setLevel(logging.INFO)

    quiet.silence_third_party()

    # 抑制しない (デバッグ用): レベルは触られない
    assert logging.getLogger("rapid_layout").level == logging.INFO
