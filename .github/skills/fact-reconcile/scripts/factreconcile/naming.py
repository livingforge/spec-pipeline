"""⑤ 命名パス — 仕様アイテムの `name` だけを LLM で整える。

reconcile (名寄せ) と **同型の独立パス** にしてある: 決定論バッチ → LLM →
review-only JSON (``names.json``) → 決定論 apply (contextdb mutate plan)。

なぜ要るか: ファクトから起こしたアイテムの ``name`` は ``statement`` の先頭を
区切りで切ったプレフィックスにすぎず、見出しとして独立していない
(「候補クラスタを LLM で裁定し」で切れる)。名寄せで統合された concept だけは
``canonical_term`` が付くが、大多数を占める単独アイテムは素通りしてしまう。

設計上の約束:

- **触るのは ``name`` だけ**。``statement`` / ``source`` は不変で、書き換え op も
  作らない (トレーサビリティを壊さない)。``name`` 属性を持たない種別
  (例 business-rule は ``label_field`` が ``statement``) は対象外にする。
- **種別ごとにバッチ化**する。同種を一度に見せないと名前の粒度・文体が揃わず、
  相互に重複しない名前も付けられない。
- **同種内で name は一意**。LLM が重複させたぶんは決定論側で弾き
  (:func:`_resolve_conflicts`)、黙って改名せず人のレビューに回す。
- 鮮度判定は reconcile と同じ ``items_hash + PROMPT_VERSION``。

命名対象は「意図の層」(要件・外部インターフェース等)。骨格 (メソッド/データ項目/
クラス/エンティティ) は codescan の決定論命名を尊重して触らない。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from docagent.store import DocAgentError
from docsummary import providers
from docsummary.settings import LLMConfig

# プロンプトを変えたら必ず上げる。names.json のキャッシュ鮮度に効く。
PROMPT_VERSION = "fact-name/1"

# 命名対象の種別 (items/<種別>/ のディレクトリ名)。骨格は含めない。
DEFAULT_TYPES = ("requirement", "external-interface", "screen")

# 1 バッチのアイテム数。多いほど名前は揃うがトークンと JSON 崩れのリスクが増える。
DEFAULT_BATCH_SIZE = 20

# 命名される属性。この属性を持たないアイテムは命名対象外 (statement は不変のため)。
NAME_ATTR = "name"

NAME_SYSTEM_PROMPT = (
    "あなたは日本語の仕様書の見出しを付ける編集者である。"
    "同一種別の仕様アイテム群を渡すので、各アイテムに canonical_name を付ける。\n"
    "規約:\n"
    "1. statement の内容だけを根拠にする。statement に書かれていない情報を"
    "名前に足さない (推測で補わない)。\n"
    "2. 体言止めの名詞句にする。目安 20 字以内。文の途中で切れた形"
    "(「〜し」「〜を」で終わる) にしない。\n"
    "3. **バッチ内で相互に重複しない**名前にする。似たアイテムは違いが分かる"
    "語を入れて区別する。\n"
    "4. statement は絶対に変更しない。返すのは名前だけ。\n"
    "5. suggested_name があればそれを尊重し、規約に反する場合だけ直す。\n"
    "出力は JSON のみ。前置き・後書き・コードフェンス (```) を付けない。形式:\n"
    '{"names":[{"id":"req-code-0001","canonical_name":"クラスタLLM裁定",'
    '"rationale":"なぜこの名前か"}]}\n'
    "id には入力に無いものを書かない。全アイテムぶん返す。"
)


# ── アイテムの読み込み (contextdb を import せず YAML を直読みする) ──────────
def load_items(root: str | Path,
               types: tuple[str, ...] = DEFAULT_TYPES) -> list[dict[str, Any]]:
    """``<root>/items/**/<種別>/*.yaml`` から命名対象のアイテムを読む。

    contextdb 本体を import しない (fact-reconcile スキルは contextdb を同梱しない)。
    種別は **ディレクトリ名** から採る。名前空間つきのレイアウト
    (``items/<名前空間>/<種別>/``) でも親ディレクトリ名が種別になるので同じ扱いで
    読める。``name`` 属性を持たないアイテムは命名できないので落とす。
    """
    import yaml  # 遅延 import: 他サブコマンドに PyYAML を要求しない

    items_dir = Path(root) / "items"
    if not items_dir.is_dir():
        raise DocAgentError(
            f"items ディレクトリが見つかりません: {items_dir}。"
            " --root で contextdb データルートを指定してください")

    wanted = set(types)
    out: list[dict[str, Any]] = []
    for path in sorted(items_dir.rglob("*.yaml")):
        item_type = path.parent.name
        if item_type not in wanted:
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        except yaml.YAMLError as e:
            raise DocAgentError(f"アイテムが読めません: {path} ({e})") from None
        records = data.get("items") if isinstance(data, dict) else data
        for rec in records or []:
            if not isinstance(rec, dict) or not rec.get("id"):
                continue
            if NAME_ATTR not in rec:
                continue  # name を持たない種別 (label_field が statement 等) は対象外
            out.append({
                "id": rec["id"],
                "type": item_type,
                "name": rec.get(NAME_ATTR) or "",
                "statement": rec.get("statement") or rec.get("description") or "",
                "file": str(path),
            })
    out.sort(key=lambda i: (i["type"], i["id"]))
    return out


def items_hash(items: list[dict[str, Any]]) -> str:
    """命名に効くフィールドだけを正準化した内容ハッシュ (並び順非依存)。"""
    rows = sorted(
        ({"id": i["id"], "type": i["type"], "name": i["name"],
          "statement": i["statement"]} for i in items),
        key=lambda r: r["id"],
    )
    blob = json.dumps(rows, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── ① バッチ化 (決定論・API キー不要) ────────────────────────
def batch_id(index: int) -> str:
    """バッチの安定 ID。emit-batches と verdicts の突き合わせ鍵。"""
    return f"nb{index + 1:03d}"


def seed_names(reconcile: dict[str, Any] | None) -> dict[str, str]:
    """統合済み concept の ``canonical_term`` を「命名の初期値」として拾う。

    名寄せで既に良い正準名称が付いているものを命名し直すのは無駄なので、
    バッチに ``suggested_name`` として同梱して尊重させる。plan.py が
    ``slug = concept_id`` で採番する規約 (``<接頭辞>-c001``) を使って
    アイテム ID と突き合わせる。
    """
    seeds: dict[str, str] = {}
    for c in (reconcile or {}).get("concepts") or []:
        cid, term = c.get("concept_id"), (c.get("canonical_term") or "").strip()
        if cid and term:
            seeds[cid] = term
    return seeds


def _suggested_for(item_id: str, seeds: dict[str, str]) -> str:
    for cid, term in seeds.items():
        if item_id.endswith(f"-{cid}"):
            return term
    return ""


def emit_batches(items: list[dict[str, Any]],
                 batch_size: int = DEFAULT_BATCH_SIZE,
                 seeds: dict[str, str] | None = None) -> dict[str, Any]:
    """アイテムを種別ごとにバッチ化して書き出す (LLM 不要)。

    種別をまたぐバッチは作らない — 名前の粒度・文体を揃えるには同種を一度に
    見せる必要があるため。
    """
    seeds = seeds or {}
    by_type: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_type.setdefault(item["type"], []).append(item)

    batches: list[dict[str, Any]] = []
    for item_type, group in sorted(by_type.items()):
        group = sorted(group, key=lambda i: i["id"])
        for start in range(0, len(group), batch_size):
            chunk = group[start:start + batch_size]
            members = []
            for item in chunk:
                member = {
                    "id": item["id"],
                    "current_name": item["name"],
                    "statement": item["statement"],
                }
                suggested = _suggested_for(item["id"], seeds)
                if suggested:
                    member["suggested_name"] = suggested
                members.append(member)
            batches.append({
                "batch_id": batch_id(len(batches)),
                "item_type": item_type,
                "items": members,
            })

    return {
        "version": 1,
        "generated_from": {
            "items_hash": items_hash(items),
            "prompt_version": PROMPT_VERSION,
        },
        "batches": batches,
    }


# ── ② LLM 裁定 ────────────────────────────────────────────────
def _batch_prompt(batch: dict[str, Any]) -> str:
    lines = [f"種別: {batch['item_type']} のアイテム群 "
             f"({len(batch['items'])} 件。名前は相互に重複させないこと):", ""]
    for item in batch["items"]:
        lines.append(f"- id: {item['id']}")
        lines.append(f"  current_name: {item.get('current_name', '')}")
        lines.append(f"  statement: {item.get('statement', '')}")
        if item.get("suggested_name"):
            lines.append(f"  suggested_name: {item['suggested_name']}")
    return "\n".join(lines)


def _extract_json(text: str) -> dict[str, Any]:
    """LLM 応答から JSON オブジェクトを取り出す (コードフェンス等に耐性)。"""
    s = text.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s.strip("`")
        if s.lstrip().startswith("json"):
            s = s.lstrip()[4:]
    start, end = s.find("{"), s.rfind("}")
    if start >= 0 and end > start:
        s = s[start:end + 1]
    return json.loads(s)


def _ground_names(data: dict[str, Any], batch: dict[str, Any]) -> list[dict[str, Any]]:
    """命名結果を接地する共通処理 (LLM 経路も外部 verdicts 経路もここを通る)。

    入力バッチに無い ID・空の名前は捨てる。``statement`` を返してきても無視する
    (statement は不変という約束を、データ側で担保する)。
    """
    valid = {i["id"]: i for i in batch["items"]}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in data.get("names") or []:
        item_id = row.get("id")
        if item_id not in valid or item_id in seen:
            continue
        name = (row.get("canonical_name") or "").strip()
        if not name:
            continue
        seen.add(item_id)
        out.append({
            "id": item_id,
            "canonical_name": name,
            "old_name": valid[item_id].get("current_name", ""),
            "rationale": (row.get("rationale") or "").strip(),
        })
    out.sort(key=lambda r: r["id"])
    return out


def name_batch(cfg: LLMConfig,
               batch: dict[str, Any],
               *,
               complete: Callable[..., str] = providers.complete,
               max_output_tokens: int = 4096,
               timeout: float = providers.DEFAULT_TIMEOUT) -> list[dict[str, Any]]:
    """1 バッチを LLM で命名する。JSON が壊れたら 1 回だけ補強して再試行する。"""
    user = _batch_prompt(batch)
    text = complete(cfg, NAME_SYSTEM_PROMPT, user,
                    max_output_tokens=max_output_tokens, timeout=timeout)
    try:
        data = _extract_json(text)
    except (json.JSONDecodeError, IndexError):
        text = complete(cfg, NAME_SYSTEM_PROMPT,
                        user + "\n\n応答は JSON オブジェクトのみにすること。",
                        max_output_tokens=max_output_tokens, timeout=timeout)
        data = _extract_json(text)
    return _ground_names(data, batch)


# ── ③ names.json の組み立て ───────────────────────────────────
def _resolve_conflicts(rows: list[dict[str, Any]],
                       items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同種内で name が衝突したものに ``conflict`` を立てる (黙って改名しない)。

    比較相手は「今回付けた名前」だけでなく **改名しない既存アイテムの名前** も
    含める。バッチ分割で LLM の視野に入らなかった相手と衝突しうるため。
    衝突した行は plan に載せず、人のレビューに回す (:func:`build_name_plan`)。
    """
    type_of = {i["id"]: i["type"] for i in items}
    renamed = {r["id"] for r in rows}
    # 改名対象外の既存名を先に押さえる (種別ごと)。
    taken: dict[str, dict[str, str]] = {}
    for item in items:
        if item["id"] in renamed or not item["name"]:
            continue
        taken.setdefault(item["type"], {}).setdefault(item["name"], item["id"])

    out: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda r: r["id"]):
        item_type = type_of.get(row["id"], "")
        holder = taken.setdefault(item_type, {}).get(row["canonical_name"])
        if holder:
            out.append({**row, "conflict": True, "conflict_with": holder})
            continue
        taken[item_type][row["canonical_name"]] = row["id"]
        out.append(row)
    return out


def build_names(cfg: LLMConfig | None,
                items: list[dict[str, Any]],
                batches: dict[str, Any],
                *,
                complete: Callable[..., str] = providers.complete,
                verdicts: dict[str, dict[str, Any]] | None = None,
                max_output_tokens: int = 4096,
                timeout: float = providers.DEFAULT_TIMEOUT) -> dict[str, Any]:
    """全バッチを命名して names.json (提案アーティファクト) を組み立てる。

    ``verdicts`` を渡すと LLM を呼ばず、外部命名 (batch_id → 命名結果) を採用する
    (呼び出し元エージェントが命名する Claude 経路)。接地・衝突解決は LLM 経路と
    同一のコードを通る。命名の無いバッチは「改名なし」として扱う。

    ``old_name`` と同じ名前になった行は改名にならないので落とす (no-op を plan に
    載せない = 再実行の決定性)。
    """
    rows: list[dict[str, Any]] = []
    for batch in batches.get("batches") or []:
        if verdicts is not None:
            raw = verdicts.get(batch["batch_id"]) or {}
            named = _ground_names(raw, batch)
        else:
            named = name_batch(cfg, batch, complete=complete,
                               max_output_tokens=max_output_tokens, timeout=timeout)
        rows.extend(r for r in named if r["canonical_name"] != r["old_name"])

    return {
        "version": 1,
        "generated_from": {
            "items_hash": items_hash(items),
            "prompt_version": PROMPT_VERSION,
        },
        "names": _resolve_conflicts(rows, items),
    }


def is_fresh(names: dict[str, Any], items: list[dict[str, Any]]) -> bool:
    """既存 names.json が現在の items + プロンプト版と一致しているか。"""
    gen = names.get("generated_from") or {}
    return (
        gen.get("items_hash") == items_hash(items)
        and gen.get("prompt_version") == PROMPT_VERSION
    )


# ── ④ mutate plan (決定論 apply) ──────────────────────────────
def build_name_plan(names: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """names.json → (mutate plan, skipped 一覧)。

    ``name`` 属性の set-attr op **だけ** を作る。statement / source は触らない。
    衝突した行は plan に載せず skipped に積む (同種内の一意性を機械的に保つ)。
    """
    ops: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row in names.get("names") or []:
        if row.get("conflict"):
            skipped.append({
                "id": row["id"],
                "reason": f"名前 '{row['canonical_name']}' が同種の"
                          f" {row.get('conflict_with')} と重複します (人が調整してください)",
            })
            continue
        ops.append({
            "op": "set-attr",
            "ref": row["id"],
            "attr": NAME_ATTR,
            "value": row["canonical_name"],
        })
    return {"ops": ops}, skipped
