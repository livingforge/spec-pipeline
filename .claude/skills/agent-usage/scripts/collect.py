# -*- coding: utf-8 -*-
"""Claude Code のセッション記録 (~/.claude/projects/**/*.jsonl) を読み、
消費トークン・利用モデル・ツール呼び出し・実行時間・コストへ正規化する収集器。

Claude Code は 1 メッセージ 1 行の JSONL でセッションを保存する。各 assistant 行の
``message.usage`` に入力/出力/キャッシュ書込/キャッシュ読込トークンが入っており、
コストは保存されていないため pricing.json の単価表から算出する。実行時間も保存されて
いないので timestamp の差分から導出する（ツール別時間は tool_use → 対応する
tool_result のタイムスタンプで挟んで計測する）。

サブエージェント（Task/Agent）の内部は、新しい Claude Code（VS Code 版を含む）では
親セッションの ``<session>.jsonl`` には混ざらず、
``<プロジェクト>/<session>/subagents/agent-*.jsonl`` に分離して記録される
（各行 ``isSidechain: true`` で、内部の assistant 行に ``usage`` が入っている）。
本収集器はこのサブディレクトリも読み、隣の ``*.meta.json``（``toolUseId`` /
``agentType``）を使って**親会話の当該 Agent 呼び出しにひも付けつつ** サブエージェント
分として計上する。古いログ（``subagents/`` が無い形式）でもそのまま動く。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def default_claude_dir() -> Path:
    """~/.claude/projects を返す（CLAUDE_CONFIG_DIR があればそれを尊重）。"""
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    root = Path(base) if base else Path.home() / ".claude"
    return root / "projects"


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _canonical_model(model: str | None, aliases: dict) -> str | None:
    if not model:
        return None
    return aliases.get(model, model)


def iter_events(claude_dir: Path):
    """全セッションを走査し、正規化済みイベント dict を yield する。

    まずメインセッション（``<プロジェクト>/<session>.jsonl``）を全件、続いて
    サブエージェント（``<プロジェクト>/<session>/subagents/agent-*.jsonl``）を全件読む。
    サブエージェント分を後に読むことで、親会話の Agent タイムライン項目が先に作られ、
    ``parent_tool_use_id`` で確実にひも付けられる。

    yield されるイベント種別:
      - assistant: model / usage / tokens / cost 材料 / is_sidechain / tool_uses[]。
        サブエージェント由来のものは ``subagent_type`` と ``parent_tool_use_id`` を伴う。
      - tool_time: tool 名と所要秒（tool_use→tool_result のペアから算出）
      - session_meta: ai-title / last-prompt
    セッション/プロジェクト/タイムスタンプはイベントに付随する。
    """
    for path in sorted(claude_dir.glob("*/*.jsonl")):
        yield from _iter_file(path, project=path.parent.name,
                              session_override=None, subagent=None)
    # サブエージェント内部ログ（新しい Claude Code / VS Code 版が別ファイルに分離記録）。
    # 親会話（session ディレクトリ名）へひも付け、is_sidechain=True で計上する。
    for path in sorted(claude_dir.glob("*/*/subagents/*.jsonl")):
        session_dir = path.parent.parent            # <session>
        yield from _iter_file(path, project=session_dir.parent.name,
                              session_override=session_dir.name,
                              subagent=_load_meta(path))


def _load_meta(jsonl_path: Path) -> dict:
    """subagents/agent-*.jsonl の隣にある agent-*.meta.json を読む。
    ``toolUseId``（親の Agent 呼び出しへの参照）と ``agentType`` を含む。無ければ空 dict。"""
    meta_path = jsonl_path.with_name(jsonl_path.stem + ".meta.json")
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _iter_file(path: Path, project: str, session_override: str | None,
               subagent: dict | None):
    """1 ファイル（メイン or サブエージェント）を走査してイベントを yield する。

    subagent が渡された場合はサブエージェント内部ログとして扱い、is_sidechain=True・
    親会話へのひも付け（session_override / parent_tool_use_id）を付与する。
    """
    is_sub = subagent is not None
    parent_tool_use_id = (subagent or {}).get("toolUseId")
    sub_type = (subagent or {}).get("agentType")
    # tool_use_id -> (tool_name, ts) をファイル内で保持し tool_result と突き合わせる
    pending_tools: dict[str, tuple[str, datetime | None]] = {}
    for raw in _read_lines(path):
        obj = _loads(raw)
        if obj is None:
            continue
        ev_type = obj.get("type")
        ts = _parse_ts(obj.get("timestamp"))
        session = session_override or obj.get("sessionId") or path.stem
        cwd = obj.get("cwd") or ""
        branch = obj.get("gitBranch") or ""
        msg = obj.get("message") or {}

        if ev_type in ("ai-title", "last-prompt"):
            # 会話（セッション）の人間可読な見出し。Claude Code が自動生成する
            # タイトルと最後のユーザー指示。usage は無いのでメタとして返す。
            yield {
                "kind": "session_meta",
                "session": session,
                "title": obj.get("aiTitle"),
                "last_prompt": obj.get("lastPrompt"),
            }
        elif ev_type == "assistant":
            usage = msg.get("usage") or {}
            tool_uses = []
            for block in (msg.get("content") or []):
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = block.get("name") or "?"
                    tid = block.get("id")
                    meta = None
                    if name in ("Agent", "Task"):
                        # サブエージェント呼び出し。型と説明を控える。内部の消費は
                        # subagents/*.jsonl から取り込み、parent_tool_use_id でこの
                        # 呼び出しにひも付ける（下のサブエージェント走査で計上）。
                        binp = block.get("input") or {}
                        meta = {
                            "subagent_type": binp.get("subagent_type"),
                            "description": binp.get("description"),
                        }
                    tool_uses.append({"name": name, "id": tid, "meta": meta})
                    if tid:
                        pending_tools[tid] = (name, ts)
            ev = {
                "kind": "assistant",
                "project": project,
                "session": session,
                "cwd": cwd,
                "branch": branch,
                "ts": ts,
                "model": msg.get("model"),
                "usage": usage,
                "is_sidechain": is_sub or bool(obj.get("isSidechain")),
                "tool_uses": tool_uses,
            }
            if is_sub:
                ev["subagent_type"] = sub_type
                ev["parent_tool_use_id"] = parent_tool_use_id
            yield ev
        elif ev_type == "user":
            # tool_result を含む user 行から、対応する tool_use の所要時間を計上
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        tid = block.get("tool_use_id")
                        hit = pending_tools.pop(tid, None) if tid else None
                        if hit and hit[1] and ts:
                            secs = (ts - hit[1]).total_seconds()
                            if 0 <= secs < 3600:  # 外れ値（放置セッション）を除外
                                yield {
                                    "kind": "tool_time",
                                    "project": project,
                                    "session": session,
                                    "tool": hit[0],
                                    "tool_use_id": tid,
                                    "seconds": secs,
                                    "is_error": bool(block.get("is_error")),
                                    "is_sidechain": is_sub,
                                }
        # それ以外(queue-operation, attachment, ai-title 等)はコスト無関係


def _read_lines(path: Path):
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield line
    except OSError:
        return


def _loads(raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def price_usage(usage: dict, model: str | None, pricing: dict) -> float | None:
    """1 メッセージの usage を USD コストに換算。単価表に無いモデルは None。"""
    aliases = pricing.get("aliases", {})
    key = _canonical_model(model, aliases)
    rates = pricing.get("models", {}).get(key)
    if rates is None:
        return None
    inp = usage.get("input_tokens", 0) or 0
    out = usage.get("output_tokens", 0) or 0
    cache_read = usage.get("cache_read_input_tokens", 0) or 0
    cache_write = usage.get("cache_creation_input_tokens", 0) or 0
    # 5m / 1h の内訳があれば別単価、無ければ全量 5m とみなす
    breakdown = usage.get("cache_creation") or {}
    w5 = breakdown.get("ephemeral_5m_input_tokens")
    w1 = breakdown.get("ephemeral_1h_input_tokens")
    if w5 is None and w1 is None:
        w5, w1 = cache_write, 0
    else:
        w5, w1 = (w5 or 0), (w1 or 0)
    total = (
        inp * rates["input"]
        + out * rates["output"]
        + cache_read * rates["cache_read"]
        + w5 * rates["cache_write_5m"]
        + w1 * rates["cache_write_1h"]
    )
    return total / 1_000_000.0


def cache_saving(usage: dict, model: str | None, pricing: dict) -> float:
    """キャッシュ読込によって節約できた額（入力満額との差）。単価不明なら 0。"""
    key = _canonical_model(model, pricing.get("aliases", {}))
    rates = pricing.get("models", {}).get(key)
    if rates is None:
        return 0.0
    cache_read = usage.get("cache_read_input_tokens", 0) or 0
    return cache_read * (rates["input"] - rates["cache_read"]) / 1_000_000.0
