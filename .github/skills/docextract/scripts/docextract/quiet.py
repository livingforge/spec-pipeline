"""サードパーティ製ライブラリの標準出力・ログノイズを抑える。

抽出パイプラインは標準出力を LLM/エージェントに渡す前提のため、OCR・表検出で
使う依存 (onnxruntime / RapidOCR / rapid_layout / rapid_table) がモデル読み込み
時に吐く警告や初回ダウンロードの進捗が stdout/stderr を汚さないよう、既知の
noisy ロガーと onnxruntime のログ重大度を **ERROR** に寄せる。

- :func:`silence_third_party` は **冪等**で、エンジン生成の直前に呼ぶ想定
  (:mod:`docextract.ocr` / :mod:`docextract.image_tables` の遅延生成箇所)。
- 環境変数 ``DOCEXTRACT_VERBOSE_DEPS=1`` で抑制を無効化できる (デバッグ用)。
  抑制するのは「本処理と無関係なノイズ」だけで、監査ログ (obs) や docextract
  自身の ``[OK]`` 等の人向け出力には一切触れない。
"""

from __future__ import annotations

import logging
import os

# モデル読み込み・推論時に情報レベルのログや警告を出しがちなロガー名。
# 実際に存在しないロガー名を getLevel しても副作用は無いので、取りこぼしを
# 避けるため関連しそうな名前を広めに寄せておく。
_NOISY_LOGGERS = (
    "rapidocr",
    "RapidOCR",
    "rapid_layout",
    "rapid_table",
    "rapid_undistort",
    "ppocr",
    "PaddleOCR",
    "onnxruntime",
)

_TRUTHY = {"1", "true", "yes", "on"}
_applied = False


def _verbose() -> bool:
    """``DOCEXTRACT_VERBOSE_DEPS`` が真値なら抑制しない (デバッグ用)。"""
    return os.environ.get("DOCEXTRACT_VERBOSE_DEPS", "").strip().lower() in _TRUTHY


def silence_third_party() -> None:
    """既知の noisy な依存のログ重大度を ERROR に引き上げる (冪等)。

    一度適用したら以降は何もしない。``DOCEXTRACT_VERBOSE_DEPS=1`` のときは
    抑制せず、以降の呼び出しも素通りさせる。
    """
    global _applied
    if _applied:
        return
    _applied = True
    if _verbose():
        return
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.ERROR)
    # onnxruntime は C++ 層のログを Python logging 経由では抑えられないため、
    # 専用 API で既定ロガーの重大度を ERROR (=3) に上げる。未導入や API 差異は無視。
    try:
        import onnxruntime  # type: ignore

        onnxruntime.set_default_logger_severity(3)  # 0=VERBOSE ... 3=ERROR 4=FATAL
    except Exception:
        pass


def _reset_for_tests() -> None:
    """テスト用: 冪等ガードを解除する (プロダクションコードからは呼ばない)。"""
    global _applied
    _applied = False
