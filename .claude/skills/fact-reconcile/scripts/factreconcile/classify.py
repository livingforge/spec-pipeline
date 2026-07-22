"""③' ルール層の分類 — business-rule に rule_kind を付ける（命名パスと対称）。

Phase 2（code-fact-extractor）は「業務的意味を持つ条件・定数・判断」を層を決めずに
広めに拾う。層（business / calculation / validation / processing / default / error）を
LLM の一発判断に委ねると不安定なので、ここに **決定論の仕分け層** を置く。

分類は 2 段:
  1. `heuristic_kind` が statement のキーワードから rule_kind を決定論で当てる。
     コード由来のルールは実装層（processing/default/…）に倒れるのが正常で、
     business は「規程・法令・議事録が根拠の業務判断」に限られ少数になる。
  2. 迷う分だけ Claude 裁定で上書きできる（`emit_batches` → `--verdicts`）。
     構造は naming（emit-batches → 裁定 → plan の set-attr）と同一。

出力は set-attr（rule_kind のみ）の mutate plan。statement/source は触らない。
生成時にこの rule_kind でビュー（基本設計書＝business/calculation、
詳細設計書＝validation/processing/default/error）へ振り分ける。
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from pathlib import Path
from typing import Any, Callable

from docagent.store import DocAgentError
from docsummary import providers
from docsummary.settings import LLMConfig

PROMPT_VERSION = "fact-reconcile-classify/1"

# 分類対象の種別（label_field が statement のルール台帳）。
DEFAULT_TYPES = ("business-rule",)
KIND_ATTR = "rule_kind"

# rule_kind の値。メタモデルの enum と一致させる（extensible）。
RULE_KINDS = ("business", "calculation", "validation",
              "processing", "default", "error")

# ── 決定論の仕分けキーワード ────────────────────────────────────
# 先に判定したものを採る。順序 = 具体的・強いシグナルから。
_CALC_KWS = ("掛け", "乗じ", "加算", "減算", "合計", "合算", "割った", "割る",
             "除し", "算出", "計算", "単価", "金額は", "コストは", "総額", "率を",
             "パーセント", "端数", "四捨五入", "切り捨て", "切り上げ")
_ERROR_KWS = ("エラー", "存在しない", "見つからない", "失敗", "不正", "破損",
              "異常", "例外", "中断", "停止する", "リトライ", "None を返す",
              "空で続行", "スキップ")
_DEFAULT_KWS = ("既定", "デフォルト", "初期値", "規定値", "閾値", "上限", "下限",
                "最大", "最小", "既定値")
_PROCESSING_KWS = ("順序", "順に", "先に", "後に", "降順", "昇順", "ソート",
                   "並べ", "並び", "走査", "読み込む順", "繰り返", "ループ",
                   "再帰", "段階", "フェーズ", "表示する", "描画", "データなし",
                   "一覧に", "見出し", "整形", "レンダ")
_VALIDATION_KWS = ("チェック", "検証", "バリデーション", "必須", "形式",
                   "桁", "範囲", "整合", "妥当性", "重複を", "一意")
# business は「業務判断・方針・禁止・不変条件」を明示する強いシグナルのみ。
# 数十件しか残らないのが正常（コード由来は原則、実装層に倒れる）。
_POLICY_KWS = ("してはならない", "しない", "禁止", "方針", "承認", "委ねる",
               "両論", "優先する", "推定で埋めない", "勝手に", "捏造",
               "根拠", "規程", "法令", "契約", "義務", "権利", "原則")


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKC", str(s or "")).casefold()


def heuristic_kind(statement: str) -> str:
    """statement のキーワードから rule_kind を決定論で当てる。

    コード由来のルールは実装層に倒れる。business は業務判断の強いシグナルが
    あるものだけ。どれにも当たらなければ processing（実装層の既定）に倒す
    — 未分類（空）は作らない。
    """
    s = _fold(statement)
    for kws, kind in ((_CALC_KWS, "calculation"),
                      (_ERROR_KWS, "error"),
                      (_DEFAULT_KWS, "default"),
                      (_PROCESSING_KWS, "processing"),
                      (_VALIDATION_KWS, "validation")):
        if any(_fold(k) in s for k in kws):
            return kind
    if any(_fold(k) in s for k in _POLICY_KWS):
        return "business"
    return "processing"


# ── アイテムの読み込み（contextdb を import せず YAML を直読み） ──────────
def load_rule_items(root: str | Path,
                    types: tuple[str, ...] = DEFAULT_TYPES) -> list[dict[str, Any]]:
    """``<root>/items/**/<種別>/*.yaml`` から分類対象（statement 見出し）を読む。"""
    import yaml

    items_dir = Path(root) / "items"
    if not items_dir.is_dir():
        raise DocAgentError(
            f"items ディレクトリが見つかりません: {items_dir}。"
            " --root で contextdb データルートを指定してください")
    wanted = set(types)
    out: list[dict[str, Any]] = []
    for path in sorted(items_dir.rglob("*.yaml")):
        if path.parent.name not in wanted:
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        except yaml.YAMLError as e:
            raise DocAgentError(f"アイテムが読めません: {path} ({e})") from None
        records = data.get("items") if isinstance(data, dict) else data
        for rec in records or []:
            if not isinstance(rec, dict) or not rec.get("id"):
                continue
            stmt = rec.get("statement")
            if not isinstance(stmt, str) or not stmt.strip():
                continue
            out.append({
                "id": rec["id"],
                "type": path.parent.name,
                "statement": stmt,
                "current_kind": rec.get(KIND_ATTR) or "",
                "file": str(path),
            })
    out.sort(key=lambda i: (i["type"], i["id"]))
    return out


def items_hash(items: list[dict[str, Any]]) -> str:
    rows = sorted(
        ({"id": i["id"], "type": i["type"], "statement": i["statement"]}
         for i in items),
        key=lambda r: r["id"])
    blob = json.dumps(rows, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── ① 決定論分類（API キー不要） ──────────────────────────────
def classify_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """全アイテムに rule_kind を付ける（決定論。未分類 0 を保証）。"""
    return [{"id": it["id"], "type": it["type"],
             "rule_kind": heuristic_kind(it["statement"]),
             "current_kind": it.get("current_kind") or ""}
            for it in items]


# ── ② バッチ化（Claude 裁定用。ヒューリスティックを suggested に同梱） ──
def batch_id(index: int) -> str:
    return f"kb{index + 1:03d}"


def emit_batches(items: list[dict[str, Any]],
                 batch_size: int = 40) -> dict[str, Any]:
    """アイテムをバッチ化して書き出す（LLM 不要）。裁定の材料。

    各アイテムに決定論の `suggested_kind` を同梱し、Claude は迷うものだけ直す。
    """
    by_type: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        by_type.setdefault(it["type"], []).append(it)
    batches: list[dict[str, Any]] = []
    for item_type, group in sorted(by_type.items()):
        group = sorted(group, key=lambda i: i["id"])
        for start in range(0, len(group), batch_size):
            chunk = group[start:start + batch_size]
            batches.append({
                "batch_id": batch_id(len(batches)),
                "item_type": item_type,
                "rule_kinds": list(RULE_KINDS),
                "items": [{"id": it["id"], "statement": it["statement"],
                           "suggested_kind": heuristic_kind(it["statement"])}
                          for it in chunk],
            })
    return {"version": 1,
            "generated_from": {"items_hash": items_hash(items),
                               "prompt_version": PROMPT_VERSION},
            "batches": batches}


CLASSIFY_SYSTEM_PROMPT = (
    "あなたは仕様書のルールを層に仕分ける編集者である。同一種別のルール群を渡すので、"
    "各ルールに rule_kind を 1 つ付ける。\n"
    "rule_kind: business（業務判断。規程・法令・社内規程が根拠）/ "
    "calculation（算出・計算式）/ validation（入力チェック・整合性検証）/ "
    "processing（処理順序・アルゴリズム・走査/表示順）/ default（既定値・閾値・上限）/ "
    "error（異常系・エラー時の振る舞い）。\n"
    "判定の 3 テスト: ①誰が決めたか（業務部門・法令・規程なら business、開発者なら実装層）"
    "②システムが無くても人手運用で成立するか（するなら business）"
    "③変わる理由（業務・制度が変わるとき business、リファクタ・性能改善のとき実装層）。\n"
    "**根拠（規程・法令・議事録）と決定者が埋まらないものは business ではない。**"
    "出典がコードだけのルールは原則すべて実装層に倒れ、business は数十件しか残らない —— "
    "それが正常な結果であり、business に倒しすぎないこと。\n"
    "suggested_kind があれば尊重し、明確に誤っている場合だけ直す。\n"
    "出力は JSON のみ。形式:\n"
    '{"classifications":[{"id":"br-0001","rule_kind":"calculation"}]}\n'
    "id には入力に無いものを書かない。全アイテムぶん返す。"
)


def _ground(raw: dict[str, Any], batch: dict[str, Any]) -> list[dict[str, Any]]:
    """裁定結果を接地する。入力に無い id・enum 外の kind は捨てて既定に戻す。"""
    valid = {it["id"]: it for it in batch.get("items") or []}
    picked = {}
    for c in raw.get("classifications") or []:
        iid, kind = c.get("id"), c.get("rule_kind")
        if iid in valid and kind in RULE_KINDS:
            picked[iid] = kind
    out = []
    for iid, it in valid.items():
        out.append({"id": iid, "type": batch.get("item_type", ""),
                    "rule_kind": picked.get(iid) or it.get("suggested_kind")
                    or heuristic_kind(it["statement"])})
    return out


def build_classify(cfg: LLMConfig | None,
                   items: list[dict[str, Any]],
                   batches: dict[str, Any],
                   *,
                   complete: Callable[..., str] = providers.complete,
                   verdicts: dict[str, dict[str, Any]] | None = None,
                   max_output_tokens: int = 4096,
                   timeout: float = providers.DEFAULT_TIMEOUT) -> dict[str, Any]:
    """全バッチを分類して classify.json（提案）を組む。

    ``verdicts`` を渡すと LLM を呼ばず外部裁定（batch_id → 分類）を採る。裁定の
    無いバッチは決定論（suggested_kind）に倒す。接地は LLM 経路と同一コード。
    """
    rows: list[dict[str, Any]] = []
    for batch in batches.get("batches") or []:
        if verdicts is not None:
            rows.extend(_ground(verdicts.get(batch["batch_id"]) or {}, batch))
        else:
            text = complete(cfg, CLASSIFY_SYSTEM_PROMPT, _batch_prompt(batch),
                            max_output_tokens=max_output_tokens, timeout=timeout)
            try:
                data = json.loads(_strip_fence(text))
            except json.JSONDecodeError:
                data = {}
            rows.extend(_ground(data, batch))
    return {"version": 1,
            "generated_from": {"items_hash": items_hash(items),
                               "prompt_version": PROMPT_VERSION},
            "classifications": rows}


def _batch_prompt(batch: dict[str, Any]) -> str:
    return json.dumps({"item_type": batch.get("item_type"),
                       "rule_kinds": batch.get("rule_kinds"),
                       "items": batch.get("items")},
                      ensure_ascii=False, indent=2)


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if t.count("```") >= 2 else t.strip("`")
        if t.startswith("json"):
            t = t[4:]
    return t.strip()


def is_fresh(classify: dict[str, Any], items: list[dict[str, Any]]) -> bool:
    gen = classify.get("generated_from") or {}
    return (gen.get("items_hash") == items_hash(items)
            and gen.get("prompt_version") == PROMPT_VERSION)


# ── ③ mutate plan（決定論 apply） ─────────────────────────────
def build_classify_plan(classify: dict[str, Any]
                        ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """classify.json → (mutate plan, skipped)。rule_kind の set-attr のみ。"""
    ops: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row in classify.get("classifications") or []:
        kind = row.get("rule_kind")
        if kind not in RULE_KINDS:
            skipped.append({"id": row.get("id"),
                            "reason": f"rule_kind '{kind}' が enum 外です"})
            continue
        ops.append({"op": "set-attr", "ref": row["id"],
                    "attr": KIND_ATTR, "value": kind})
    return {"ops": ops}, skipped
