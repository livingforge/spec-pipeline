"""spec-reconcile コマンドラインインターフェース。

    python -m specreconcile <サブコマンド> [オプション]
    spec-reconcile <サブコマンド> [オプション]        # venv コマンド (launcher 経由)

サブコマンド:
    analyze  facts を名寄せして reconcile.json (提案) を生成する (①ブロッキング + ②LLM 裁定)
    review   reconcile.json を人間可読で一覧する (concept / contradiction / term_map)
    plan     reconcile.json から specdb mutate plan.json を生成する (④。適用は specdb mutate apply)
    config   LLM 接続設定の確認 (--check) と .env 雛形の作成 (--init)。docsummary と共有

秘密情報 (API キー) は `.env` か環境変数で渡す。このツールは値を表示・保存しない。
出力はすべて提案であり、specdb への適用は人の承認 (specdb mutate apply → approve) を通す。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from docagent.facts import FactStore
from docagent.store import DocAgentError, Library
from docextract import paths as _paths
from docsummary import providers, settings as settings_mod
from docsummary.settings import Settings, SettingsError

from . import adjudicate, blocking
from . import plan as plan_mod

DEFAULT_RECONCILE = "reconcile.json"
DEFAULT_PLAN = "plan.json"


def _emit(obj: Any, as_json: bool, human) -> None:
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    else:
        human(obj)


# ── analyze ──────────────────────────────────────────────────
def _load_facts(args: argparse.Namespace) -> list[dict[str, Any]]:
    facts_path = args.facts or _paths.facts_path()
    store = FactStore.load(facts_path)
    facts = store.items
    if args.doc:
        wanted = set(args.doc)
        facts = [f for f in facts if f.get("doc_id") in wanted]
    if args.dir:
        lib = Library.load(args.store or _paths.store_path())
        prefix = str(Path(args.dir))
        keep = {
            d["id"] for d in lib.documents
            if str(Path(d.get("source", ""))).startswith(prefix)
        }
        facts = [f for f in facts if f.get("doc_id") in keep]
    return facts


def cmd_analyze(args: argparse.Namespace) -> int:
    facts = _load_facts(args)
    clusters = blocking.candidate_clusters(facts, threshold=args.block_threshold)

    if args.dry_run:
        payload = {
            "facts": len(facts),
            "clusters": [
                {"size": len(c), "member_fact_ids": [f.get("id") for f in c]}
                for c in clusters
            ],
        }
        _emit(payload, args.json, lambda o: (
            print(f"ファクト {o['facts']} 件 / 候補クラスタ {len(o['clusters'])} 個"
                  " (LLM 未呼び出し)"),
            [print(f"  [{c['size']}件] {', '.join(c['member_fact_ids'])}")
             for c in o["clusters"]],
        ))
        return 0

    out = Path(args.out or DEFAULT_RECONCILE)
    if out.is_file() and not args.force:
        try:
            existing = json.loads(out.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            existing = None
        if existing and adjudicate.is_fresh(existing, facts):
            print(f"変更なし (facts が前回と同一): {out}。再生成は --force")
            return 0

    if not clusters:
        reconcile = {
            "version": 1,
            "generated_from": {
                "facts_hash": adjudicate.facts_hash(facts),
                "prompt_version": adjudicate.PROMPT_VERSION,
            },
            "concepts": [], "contradictions": [], "term_map": [],
        }
    else:
        try:
            cfg = settings_mod.resolve_config(
                Settings.load(args.env_file), args.provider, args.model)
        except SettingsError as e:
            print(f"エラー: {e}", file=sys.stderr)
            return 1
        try:
            reconcile = adjudicate.build_reconcile(
                cfg, facts, clusters,
                max_output_tokens=args.max_output_tokens, timeout=args.timeout)
        except providers.ProviderError as e:
            print(f"エラー: {e}", file=sys.stderr)
            return 1

    out.write_text(json.dumps(reconcile, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"生成: {out} — 統合提案 {len(reconcile['concepts'])} 件 /"
          f" 矛盾 {len(reconcile['contradictions'])} 件 /"
          f" 用語 {len(reconcile['term_map'])} 件")
    print("次の一手: spec-reconcile review でレビュー → plan で mutate plan を作る")
    return 0


# ── review ───────────────────────────────────────────────────
def _read_reconcile(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise DocAgentError(
            f"reconcile.json が見つかりません: {p}。"
            " 先に spec-reconcile analyze で生成してください")
    return json.loads(p.read_text(encoding="utf-8-sig"))


def cmd_review(args: argparse.Namespace) -> int:
    try:
        rec = _read_reconcile(args.reconcile or DEFAULT_RECONCILE)
    except DocAgentError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(rec, ensure_ascii=False, indent=2))
        return 0

    concepts = rec.get("concepts") or []
    contradictions = rec.get("contradictions") or []
    term_map = rec.get("term_map") or []
    print(f"■ 統合提案 (concept) {len(concepts)} 件 — 同一概念とみなしたファクト群")
    for c in concepts:
        print(f"  {c['concept_id']} [{c.get('fact_type')}] {c.get('canonical_term')}")
        print(f"      {c.get('canonical_statement')}")
        print(f"      members: {', '.join(c.get('member_fact_ids') or [])}")
    print(f"\n■ 矛盾 (contradiction) {len(contradictions)} 件 — 値が食い違う (人の判断が必要)")
    for con in contradictions:
        print(f"  {', '.join(con.get('fact_ids') or [])}: {con.get('issue')}")
        for cl in con.get("claims") or []:
            print(f"      - {cl.get('fact_id')}: {cl.get('position')}")
    print(f"\n■ 用語 (term_map) {len(term_map)} 件 — 表記ゆれ → 正準用語")
    for t in term_map:
        print(f"  {'、'.join(t.get('variants') or [])} → {t.get('canonical')}")
    return 0


# ── plan ─────────────────────────────────────────────────────
def _metamodel_path(args: argparse.Namespace) -> Path:
    if args.metamodel:
        return Path(args.metamodel)
    root = Path(args.root) if args.root else Path.cwd() / ".specdb"
    return root / "metamodel.yaml"


def cmd_plan(args: argparse.Namespace) -> int:
    try:
        rec = _read_reconcile(args.infile or DEFAULT_RECONCILE)
        the_plan, skipped = plan_mod.build_plan(rec, _metamodel_path(args))
    except DocAgentError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1

    out = Path(args.out or DEFAULT_PLAN)
    out.write_text(json.dumps(the_plan, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    if args.json:
        print(json.dumps({"plan": the_plan, "skipped": skipped},
                         ensure_ascii=False, indent=2))
        return 0
    print(f"生成: {out} — add-item {len(the_plan['ops'])} 件")
    if skipped:
        print(f"保留 {len(skipped)} 件 (spec-designer で補完):")
        for s in skipped:
            print(f"  {s['concept_id']}: {s['reason']}")
    print("次の一手: specdb mutate apply "
          f"{out} --dry-run で検証 → 外して適用 → specdb approve")
    return 0


# ── config ───────────────────────────────────────────────────
def cmd_config(args: argparse.Namespace) -> int:
    if args.init:
        target = Path(args.path) if args.path else Path.cwd() / ".env"
        example = target.with_name(target.name + ".example")
        example.write_text(settings_mod.ENV_TEMPLATE, encoding="utf-8")
        created = [str(example)]
        if not target.exists():
            target.write_text(settings_mod.ENV_TEMPLATE, encoding="utf-8")
            created.append(str(target))
        for p in created:
            print(f"作成しました: {p}")
        print("次の一手: 使うプロバイダの API キーを .env に記入する (値は読まない)")
        return 0
    env = Settings.load(args.env_file)
    payload = settings_mod.check_payload(env, args.provider)

    def human(o):
        print(f".env: {o['env_file'] or '(見つからない)'}")
        if o["selected_provider"]:
            print(f"使用プロバイダ: {o['selected_provider']}")
        else:
            print(f"使用プロバイダ: 未決定 — {o['selection_error']}")
        for name, p in o["providers"].items():
            print(f"[{'OK' if p['configured'] else '--'}] {name}")

    _emit(payload, args.json, human)
    return 0 if payload["selected_provider"] else 1


# ── パーサ ────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="機械可読な JSON で出力")

    p = argparse.ArgumentParser(
        prog="spec-reconcile",
        description="抽出ファクトを意味的に名寄せし、specdb への提案を作る",
        parents=[common])
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("analyze", help="facts を名寄せして reconcile.json を生成",
                        parents=[common])
    sp.add_argument("--facts", help="facts.json のパス (既定 <home>/store/facts.json)")
    sp.add_argument("--store", help="docagent 集約 JSON (--dir 解決に使う)")
    sp.add_argument("--doc", action="append", help="対象文書 ID (複数可)")
    sp.add_argument("--dir", help="このフォルダ配下の元ファイルを持つ文書のファクトに絞る")
    sp.add_argument("--out", help=f"出力先 (既定 {DEFAULT_RECONCILE})")
    sp.add_argument("--env-file", help=".env のパス (既定は cwd から上方探索)")
    sp.add_argument("--provider",
                    help="LLM プロバイダ (openai / azure / gemini / anthropic)")
    sp.add_argument("--model", help="モデル名/デプロイ名の上書き")
    sp.add_argument("--block-threshold", type=float, default=blocking.DEFAULT_THRESHOLD,
                    help=f"ブロッキングの類似閾値 (既定 {blocking.DEFAULT_THRESHOLD})")
    sp.add_argument("--max-output-tokens", type=int, default=4096,
                    help="生成の上限トークン数")
    sp.add_argument("--timeout", type=float, default=providers.DEFAULT_TIMEOUT,
                    help="API タイムアウト秒")
    sp.add_argument("--force", action="store_true", help="facts 不変でも再生成する")
    sp.add_argument("--dry-run", action="store_true",
                    help="候補クラスタだけ表示して LLM は呼ばない (API キー不要)")
    sp.set_defaults(func=cmd_analyze)

    sp = sub.add_parser("review", help="reconcile.json を人間可読で一覧",
                        parents=[common])
    sp.add_argument("reconcile", nargs="?", help=f"reconcile.json (既定 {DEFAULT_RECONCILE})")
    sp.set_defaults(func=cmd_review)

    sp = sub.add_parser("plan", help="reconcile.json から specdb mutate plan を生成",
                        parents=[common])
    sp.add_argument("--in", dest="infile", help=f"reconcile.json (既定 {DEFAULT_RECONCILE})")
    sp.add_argument("--out", help=f"出力先 (既定 {DEFAULT_PLAN})")
    sp.add_argument("--metamodel", help="ターゲット metamodel.yaml のパス")
    sp.add_argument("--root", help="specdb データルート (既定 ./.specdb)。metamodel.yaml を探す")
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("config", help="接続設定の確認 (--check) / .env 雛形の作成 (--init)",
                        parents=[common])
    sp.add_argument("--check", action="store_true", help="設定状態を表示 (値は出さない)")
    sp.add_argument("--init", action="store_true", help=".env / .env.example の雛形を作成")
    sp.add_argument("--path", help="--init の書き出し先 .env パス")
    sp.add_argument("--env-file", help=".env のパス")
    sp.add_argument("--provider", help="LLM プロバイダ")
    sp.set_defaults(func=cmd_config)

    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except DocAgentError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
