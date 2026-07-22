# -*- coding: utf-8 -*-
"""品質チェック — 正本アイテムの見出し（name）と本文の品質を機械的に検出する

    python contextdb/quality_check.py                    # 人間可読レポート
    python quality_check.py --json                    # 機械可読 (JSON)
    python quality_check.py --strict                  # error 級があれば exit 1
    python quality_check.py --type requirement        # 種別を絞る
    python quality_check.py --root <データディレクトリ> …

ファクトから起こしたアイテムの `name` は、`statement` の先頭を切ったプレフィックス
だったり、同種で重複していたりする。命名規約は LLM のプロンプト
（factreconcile/naming.py）の中にしか無く、適用後は誰も検証していない。本ツールは
その規約を**決定論で再検査**し、@spec-reviewer の入力になる指摘一覧を作る。

engine が「メタモデルに適合するか」を見るのに対し、本ツールは「人が読める設計書に
なるか」を見る。したがって engine には載せない（既存ストアを一斉に error にせず、
閾値を段階的に上げられるようにするため）。

  QC-NAME-PREFIX   name が statement の先頭一致 = 切り詰めの痕跡（error）
  QC-NAME-CUT      name が助詞止め = 文の途中で切れている（error）
  QC-NAME-SUFFIX   name が動詞に「の仕様」を接尾 = 見出しとして破綻（error）
  QC-NAME-DUP      同一種別内で name が重複（error）
  QC-NAME-LEN      name が長すぎる（warn）
  QC-NAME-YOGEN    name が用言止め = 体言止めになっていない（warn）
  QC-STMT-NEAR-DUP statement が別アイテムと近似重複（warn）
  QC-TERM-VARIANT  用語集の語が表記ゆれの形で使われている（warn）
  QC-TYPE-SKIP     --type/quality.yaml で指定した種別が検査不能（warn。黙って落とさない）

閾値と対象種別はデータルートの quality.yaml で上書きできる:

    types: [requirement, external-interface]   # 空/未指定 = label_field が name の全種別
    max_name_length: 30
    near_dup_threshold: 0.7
    min_prefix_length: 4

対象は `label_field` が `name` の種別に限る。`statement` を見出しにする種別
（業務ルール等）は切り詰めが起こりえず、`class_name` / `signature` を見出しにする
骨格（モジュール/メソッド）は codescan の決定論命名なので触らない。
エンジン・sync-check 同様、特定のアイテム種別の知識は持たない。
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
import unicodedata
from pathlib import Path

import yaml

from engine import Store, parse_root

CONFIG_NAME = "quality.yaml"

# 見出しに使う属性がこの名前の種別だけを対象にする（骨格・statement 見出しを除く）。
NAME_ATTR = "name"

# 本文として見る属性。先に見つかったものを使う。
BODY_ATTRS = ("statement", "description")

# ── 閾値の既定値 ────────────────────────────────────────────────
# naming.py のプロンプトは「20 字以内」を目安に指示するが、機械側は指示より緩く
# 取る。規約違反を全部拾うのではなく、読んで明らかに長い見出しだけを warn にする。
DEFAULT_MAX_NAME_LENGTH = 30
# 文字 2-gram Jaccard。blocking.py の候補生成（0.5）より高く取る。あちらは
# 再現率重視で LLM 裁定が精度を担保するが、こちらは人が直接読む指摘なので
# 誤検出を減らす方を優先する。
DEFAULT_NEAR_DUP_THRESHOLD = 0.7
# これより短い name は statement の先頭一致でも「切り詰め」と見なさない
# （短い正当な見出しが偶然 statement の書き出しと一致することがあるため）。
DEFAULT_MIN_PREFIX_LENGTH = 4

# ── 見出しの語尾 ────────────────────────────────────────────────
# 助詞止め。日本語の名詞がこれらの平仮名で終わることは実質無いので error にできる。
# 「り」「し」「き」は 見出し/送り/手続き のような正当な名詞語尾でもあるため、
# 連用中止形は検出対象から外す（誤検出のほうが害が大きい）。
_PARTICLE_ENDINGS = ("を", "に", "へ", "と", "が", "は", "も", "で", "や",
                     "から", "まで", "より")
# 用言止め。体言止めではないが文としては閉じているので warn に留める。
_YOGEN_ENDINGS = ("する", "した", "される", "された", "できる", "できた",
                  "ある", "ない", "なる", "なった", "行う", "行なう")

# 動詞に「の仕様」を後付けして見出しにした痕跡（旧 doc-author のバグ由来）。
# 「決済の仕様」のような 名詞＋の＋仕様 は正当なので、「の仕様」の直前が
# 動詞の活用語尾（未然・連用・終止の平仮名）のときだけ破綻と見なす。
# 「〜の仕様書」は末尾が「書」なので endswith('の仕様') に当たらず誤検出しない。
_NOUN_SUFFIX = "の仕様"
# 名詞末尾になりにくい動詞活用語尾。直前がこれなら「呼ば|の仕様」型の切断。
_VERB_TAIL_KANA = frozenset(
    "うくぐすつぬぶむるかがさざただなばぱまらわきぎしじちぢにびぴみりえけげせぜてでねべぺめれ")


# ---------- 設定 ----------

def load_config(data_root: Path) -> dict:
    path = data_root / CONFIG_NAME
    cfg = {}
    if path.is_file():
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    cfg.setdefault("types", [])
    cfg.setdefault("max_name_length", DEFAULT_MAX_NAME_LENGTH)
    cfg.setdefault("near_dup_threshold", DEFAULT_NEAR_DUP_THRESHOLD)
    cfg.setdefault("min_prefix_length", DEFAULT_MIN_PREFIX_LENGTH)
    return cfg


# ---------- 照合用の正規化 ----------
# factreconcile 側にも同種の畳み込み（docagent.store._fold_text）があるが、
# contextdb バンドルは docagent を同梱しないので独立に持つ。依存を足すより
# 十数行の重複を選ぶ（sync_check が diff._git を使うのと同じで、バンドル内で閉じる）。

def _fold(s: str) -> str:
    """表記ゆれ（全角/半角・大文字小文字・空白）を除いて照合用に正規化する。"""
    return "".join(unicodedata.normalize("NFKC", str(s or "")).split()).casefold()


def _bigrams(folded: str) -> set[str]:
    """畳んだ文字列の隣接 2 文字集合。1 文字以下ならその文字自体を 1 要素に。"""
    if len(folded) < 2:
        return {folded} if folded else set()
    return {folded[i:i + 2] for i in range(len(folded) - 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b) if inter else 0.0


# ---------- 検出結果 ----------

def _finding(level: str, kind: str, where: str, message: str) -> dict:
    return {"level": level, "kind": kind, "where": where, "message": message}


def target_types(store: Store, cfg: dict,
                 types: list[str] | None = None) -> list[str]:
    """検査対象の種別。--type > quality.yaml > label_field が name の全種別。"""
    wanted = types or cfg.get("types") or []
    named = [t for t, d in store.mm.item_types.items()
             if d.get("label_field") == NAME_ATTR]
    if not wanted:
        return sorted(named)
    return sorted(t for t in wanted if t in store.mm.item_types)


def _type_request_findings(store: Store, wanted: list[str]) -> list[dict]:
    """明示指定された種別のうち検査できないものを warn として可視化する。

    `target_types` は検査可能な種別だけを黙って残すため、`--type open-issue`
    （label_field が name でない）や存在しない種別を渡すと理由の説明なく
    「0 件」になり、指定が効いていないことに気づけない。ここで拾って知らせる。
    """
    findings = []
    for t in wanted:
        tdef = store.mm.item_types.get(t)
        if tdef is None:
            findings.append(_finding(
                "warn", "QC-TYPE-SKIP", t,
                f"種別 '{t}' はメタモデルに無い — 検査対象にならない"))
        elif tdef.get("label_field") != NAME_ATTR:
            findings.append(_finding(
                "warn", "QC-TYPE-SKIP", t,
                f"種別 '{t}' は label_field が '{tdef.get('label_field')}' "
                f"— name 見出しの品質検査は対象外（0 件になる）"))
    return findings


def _targets(store: Store, types: list[str]) -> list:
    """検査対象のアイテム。deprecated は棚卸し済みとして除外する。"""
    out = []
    for t in types:
        for item in store.items_of(t):
            if item.status == "deprecated":
                continue
            if str(item.attrs.get(NAME_ATTR, "")).strip():
                out.append(item)
    return out


def _body(item) -> str:
    for attr in BODY_ATTRS:
        v = item.attrs.get(attr)
        if isinstance(v, str) and v.strip():
            return v
    return ""


# ---------- 1. 見出しの形 — 切り詰め・語尾・長さ ----------

def check_name_shape(items: list, cfg: dict) -> list[dict]:
    findings = []
    max_len = int(cfg["max_name_length"])
    min_prefix = int(cfg["min_prefix_length"])
    for item in items:
        name = str(item.attrs[NAME_ATTR]).strip()
        folded = _fold(name)
        body = _body(item)
        body_folded = _fold(body)

        # statement の先頭を切っただけの name — 今回の主症状の直接検出
        if (len(folded) >= min_prefix and body_folded
                and len(body_folded) > len(folded)
                and body_folded.startswith(folded)):
            findings.append(_finding(
                "error", "QC-NAME-PREFIX", item.id,
                f"name '{name}' が本文の先頭一致 — 見出しではなく切り詰めの可能性"))
        elif name.endswith(_PARTICLE_ENDINGS):
            # 先頭一致で既に指摘済みなら重ねない（同じ原因の別症状）
            findings.append(_finding(
                "error", "QC-NAME-CUT", item.id,
                f"name '{name}' が助詞で終わっている — 文の途中で切れている"))

        # 動詞に「の仕様」を接いだ見出し（例: 'LLM を呼ばの仕様'）。切り詰め・
        # 助詞止めとは別原因なので elif 連鎖に載せず独立に判定する。
        if (name.endswith(_NOUN_SUFFIX) and len(name) > len(_NOUN_SUFFIX)
                and name[-len(_NOUN_SUFFIX) - 1] in _VERB_TAIL_KANA):
            findings.append(_finding(
                "error", "QC-NAME-SUFFIX", item.id,
                f"name '{name}' が動詞に「の仕様」を接尾 — 見出しとして破綻している"))

        if name.endswith(_YOGEN_ENDINGS):
            findings.append(_finding(
                "warn", "QC-NAME-YOGEN", item.id,
                f"name '{name}' が用言止め — 体言止めの名詞句にする"))

        if len(name) > max_len:
            findings.append(_finding(
                "warn", "QC-NAME-LEN", item.id,
                f"name が {len(name)} 字（上限 {max_len}）— '{name}'"))
    return findings


# ---------- 2. 見出しの重複 — 同一種別内 ----------

def check_name_duplicates(items: list) -> list[dict]:
    """同じ種別に同じ見出しが複数ある状態を検出する。

    metamodel の unique: true でも同じことは検出できるが、そちらは engine を
    error で止めてしまい既存ストアが `--frozen` を通らなくなる。品質側で見れば
    段階的に直せる。
    """
    findings = []
    by_type: dict[str, dict[str, list[str]]] = {}
    for item in items:
        folded = _fold(item.attrs[NAME_ATTR])
        by_type.setdefault(item.type, {}).setdefault(folded, []).append(item.id)
    for t, groups in sorted(by_type.items()):
        for folded, ids in sorted(groups.items()):
            if len(ids) < 2:
                continue
            ids = sorted(ids)
            for iid in ids[1:]:
                findings.append(_finding(
                    "error", "QC-NAME-DUP", iid,
                    f"{t} の name が {ids[0]} と重複 — 違いが分かる語で区別する"))
    return findings


# ---------- 3. 本文の近似重複 — 同一仕様が別アイテムに割れている ----------

# 近似重複は「その種別が本文として明示的に持つ statement」だけを比較する。
# _body() のフォールバック（description）を使うと、型から機械生成された
# data-item の定型 description（「文字列型のデータ項目。」等）が全ペア一致して
# ノイズで埋まる。statement を持たない種別は近似重複の対象にしない。
_NEAR_DUP_ATTR = "statement"


def check_near_duplicates(items: list, cfg: dict) -> list[dict]:
    threshold = float(cfg["near_dup_threshold"])
    findings = []
    prepared: dict[str, list[tuple[str, str, str, set[str]]]] = {}
    for item in items:
        body = item.attrs.get(_NEAR_DUP_ATTR)
        if not isinstance(body, str) or not body.strip():
            continue
        folded = _fold(body)
        prepared.setdefault(item.type, []).append(
            (item.id, body, folded, _bigrams(folded)))
    for t in sorted(prepared):
        rows = sorted(prepared[t])          # id 順に固定して出力を決定的にする
        for i in range(len(rows)):
            # SequenceMatcher は seq2 を内部キャッシュするので外側を seq2 に固定し、
            # 内側だけ差し替える。real/quick_ratio で本計算 ratio() を刈り込む。
            sm = difflib.SequenceMatcher(None, "", rows[i][2], autojunk=False)
            for j in range(i + 1, len(rows)):
                score = _near_dup_score(sm, rows[i][3], rows[j][3],
                                        rows[j][2], threshold)
                if score is None:
                    continue
                findings.append(_finding(
                    "warn", "QC-STMT-NEAR-DUP", rows[j][0],
                    f"{rows[i][0]} と本文が {score:.2f} 類似 — "
                    "同一仕様が割れていないか確認する"))
    return findings


def _near_dup_score(sm: "difflib.SequenceMatcher", bg_i: set[str],
                    bg_j: set[str], folded_j: str,
                    threshold: float) -> float | None:
    """文字 2-gram Jaccard と編集類似度の高い方を返す（閾値未満なら None）。

    Jaccard は語順違いに弱いので difflib.SequenceMatcher を併用する。ratio() は
    O(n·m) と重いため、O(1)/O(n) の real_quick_ratio / quick_ratio で刈ってから
    本計算に入る。標準ライブラリのみで追加依存は無い。
    """
    j = _jaccard(bg_i, bg_j)
    if j >= threshold:
        return j
    sm.set_seq1(folded_j)
    if sm.real_quick_ratio() < threshold or sm.quick_ratio() < threshold:
        return None
    r = sm.ratio()
    return r if r >= threshold else None


# ---------- 4. 用語の表記ゆれ ----------

def check_term_variants(store: Store, items: list) -> list[dict]:
    """用語集に登録された語が、正規形と違う表記で本文に現れているかを見る。

    畳み込むと一致するのに原文では一致しない = 全角/半角・大小文字などの
    表記ゆれ。用語集そのものが無いプロジェクトでは何も検出しない。
    """
    terms = []
    for t, tdef in store.mm.item_types.items():
        if tdef.get("label_field") != "term":
            continue
        for item in store.items_of(t):
            if item.status == "deprecated":
                continue
            raw = str(item.attrs.get("term", "")).strip()
            if len(raw) >= 2:
                terms.append((raw, _fold(raw)))
    if not terms:
        return []

    findings = []
    for item in items:
        text = f"{item.attrs[NAME_ATTR]}\n{_body(item)}"
        folded = _fold(text)
        for raw, folded_term in terms:
            if folded_term in folded and raw not in text:
                findings.append(_finding(
                    "warn", "QC-TERM-VARIANT", item.id,
                    f"用語 '{raw}' が表記ゆれの形で使われている — 正規形に揃える"))
    return findings


# ---------- レポート ----------

def run_checks(data_root: Path, types: list[str] | None = None) -> dict:
    store = Store.load(data_root)
    cfg = load_config(data_root)
    tt = target_types(store, cfg, types)
    items = _targets(store, tt)
    wanted = types or cfg.get("types") or []
    findings = (_type_request_findings(store, wanted)
                + check_name_shape(items, cfg)
                + check_name_duplicates(items)
                + check_near_duplicates(items, cfg)
                + check_term_variants(store, items))
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1
    return {"root": str(data_root), "types": tt, "checked": len(items),
            "findings": findings, "counts": counts}


def render_text(report: dict) -> str:
    lines = [f"# 品質チェック — {report['root']}", ""]
    lines.append(f"対象: {', '.join(report['types']) or '(なし)'}"
                 f" — {report['checked']} 件")
    lines.append("")
    if not report["findings"]:
        lines.append("検出なし — 見出し・本文の品質に問題は観測されていない。")
    for f in report["findings"]:
        lines.append(f"{f['level']}: {f['kind']} [{f['where']}] {f['message']}")
    if report["counts"]:
        summary = " / ".join(f"{k} {v}" for k, v in sorted(report["counts"].items()))
        lines += ["", f"検出: {summary}"]
        if any(f["level"] == "error" for f in report["findings"]):
            lines.append("次の一手: @spec-reviewer で指摘を裁定し、"
                         "命名は fact-reconcile name → name-plan に流す")
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    data_root, rest = parse_root(sys.argv[1:])
    ap = argparse.ArgumentParser(
        prog="quality_check.py",
        description="正本アイテムの見出し・本文の品質を機械的に検出する")
    ap.add_argument("--type", action="append", dest="types", default=None,
                    help="検査する種別（繰り返し可。既定は label_field が name の全種別）")
    ap.add_argument("--json", action="store_true", help="JSON で出力する")
    ap.add_argument("--strict", action="store_true",
                    help="error 級の検出があれば exit 1（CI ゲート用）")
    args = ap.parse_args(rest)

    report = run_checks(data_root, types=args.types)
    if args.json:
        sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(render_text(report))
    if args.strict and any(f["level"] == "error" for f in report["findings"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
