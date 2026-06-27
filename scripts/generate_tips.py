#!/usr/bin/env python3
"""Claude Code学習コンテンツを生成し、tips/ と tips_index.json を更新する。"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate_digest import (  # noqa: E402
    JST, MODEL, clean_output, fetch_url, google_news_url, parse_feed,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIPS_DIR = os.path.join(ROOT, "tips")
TIPS_INDEX = os.path.join(ROOT, "tips_index.json")
THEMES = [
    "スキル（Skills）", "スラッシュコマンド", "MEMORY・CLAUDE.md",
    "サブエージェント", "フック", "MCP", "計画モード", "チェックポイント",
    "オートメモリ", "バンドルスキル（/code-review・/debug）",
    "CLIワンショット・GitHub Actions", "並列リサーチ",
    "エージェントチェーン", "Worktrees・並行開発",
    "Permissions・権限管理", "プラグイン・Marketplace",
]
# テーマ固有の基本知識。AIが不正確になりやすいニッチなテーマだけ書く。
TOPIC_HINTS: dict[str, str] = {
    "バンドルスキル（/code-review・/debug）": (
        "/code-review, /batch, /debug, /loop, /claude-api, /run, /verify, "
        "/run-skill-generator などのビルトインスキルが最初から使える。"
    ),
    "CLIワンショット・GitHub Actions": (
        "claude -p 'メッセージ' でターミナルから1回だけ実行できる。"
        "GitHub Actions のステップとして組み込み、CIで自動実行する使い方が広まっている。"
    ),
    "並列リサーチ": (
        "Agent tool の run_in_background パラメータで複数サブエージェントを同時起動できる。"
        "「競合A」「競合B」「公式docs」を別エージェントに割り振るのが典型例。"
    ),
    "エージェントチェーン": (
        "ある agent の出力を次の agent の入力にする多段構成。"
        "researcher → reviewer → writer のパターンが代表例。"
        ".claude/agents/*.md でカスタム agent を定義できる。"
    ),
    "Worktrees・並行開発": (
        "git worktree と組み合わせ、複数ブランチで並行作業できる。"
        "Agent tool の isolation: 'worktree' でエージェントを隔離した作業ツリー上で動かせる。"
    ),
    "Permissions・権限管理": (
        "settings.json の permissions でツール・ファイル・コマンドの利用範囲を制限できる。"
        "ユーザー・プロジェクト・ローカルの3階層で設定を上書きできる。"
    ),
    "プラグイン・Marketplace": (
        "skills・MCP・hooks・UI拡張をまとめたパッケージが「プラグイン」。"
        "Marketplace から発見・インストールでき、チームで共有もできる。"
    ),
}
OFFICIAL_DOCS = "https://docs.claude.com/ja/docs/claude-code"


def choose_theme(today: str) -> str:
    """日付から決定的にテーマを選ぶ。再実行しても同日のテーマは変わらない。"""
    ordinal = datetime.strptime(today, "%Y-%m-%d").toordinal()
    return THEMES[ordinal % len(THEMES)]


def collect_candidates() -> list[dict]:
    """自動取得できる情報源だけを集め、失敗時も空リストで続行する。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    sources = [
        ("GitHub Releases", "https://github.com/anthropics/claude-code/releases.atom", "GitHub Releases"),
        ("Googleニュース日本語", google_news_url("Claude Code", "ja"), ""),
        ("Googleニュース英語", google_news_url("Claude Code", "en"), ""),
        ("GoogleニュースAnthropic", google_news_url("Anthropic Claude Code", "en"), ""),
    ]
    candidates, seen = [], set()
    for label, url, default_source in sources:
        try:
            items = parse_feed(fetch_url(url), cutoff, default_source)
        except Exception as error:  # 1情報源が落ちてもレッスンは生成できる
            print(f"[warn] 取得失敗 '{label}': {error}", file=sys.stderr)
            continue
        for item in items[:6]:
            key = item["link"] or item["title"]
            if key in seen:
                continue
            seen.add(key)
            candidates.append(item)
    return candidates[:24]


def build_messages(candidates: list[dict], today: str, theme: str) -> tuple[str, str]:
    candidate_text = "\n".join(
        f"{number}. {item['title']}\n   出典URL: {item['link']}"
        for number, item in enumerate(candidates, 1)
    ) or "候補記事なし。公式ドキュメント根拠だけで、安定した入門レッスンを作ること。"
    hint = TOPIC_HINTS.get(theme, "")
    hint_block = f"\n\n参考知識（レッスン内容の根拠に使える事実）:\n{hint}" if hint else ""
    system = f"""あなたはClaude Codeのやさしい日本語講師です。初心者向けに今日のテーマ「{theme}」を、すぐ試せるレッスンとして書きます。{hint_block}

必須ルール:
- 出力は下記スキーマのMarkdownだけ。コードフェンスや前置きは付けない。
- front-matterの後に3〜5個の ## セクション。先頭は必ず kind: lesson、残りは kind: new / skill / md。
- レッスン本文は3〜8行、初心者が実行できる具体的な手順を書く。各セクションに term と https の source を付ける。try は必要なセクションに、Claude Codeへそのまま貼れる日本語の依頼文を1行で付ける。
- 出典URLを創作しない。候補にない内容の根拠には {OFFICIAL_DOCS} を使う。
- 候補が少なくても失敗しない。安定した知識をレッスンにし、公式ドキュメントを出典にする。

出力スキーマ:
---
date: {today}
title: 使い方
theme: {theme}
summary: 今日のテーマを短く20〜35字で
intro: 今日のレッスンをやさしく紹介する1〜2文
level: 入門
readtime: 約4分
---

## レッスンの見出し
本文
- kind: lesson
- term: 用語＝やさしい説明
- try: そのまま試せる依頼文
- source: https://...
"""
    user = f"今日の日付: {today}\n今日のテーマ: {theme}\n\n候補記事:\n{candidate_text}\n\n指定形式で生成してください。"
    return system, user


def generate_tips(system: str, user: str) -> str:
    from anthropic import Anthropic

    client = Anthropic()  # ANTHROPIC_API_KEY は環境変数からのみ読む
    message = client.messages.create(
        model=MODEL,
        max_tokens=2500,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return clean_output("".join(block.text for block in message.content if block.type == "text"))


def parse_front_matter(markdown: str) -> dict:
    meta = {}
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        return meta
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, separator, value = line.partition(":")
        if separator and key.strip():
            meta[key.strip()] = value.strip()
    return meta


def update_tips_index(date: str, title: str, theme: str, summary: str, rel_path: str) -> None:
    data = {"entries": []}
    if os.path.exists(TIPS_INDEX):
        with open(TIPS_INDEX, encoding="utf-8") as file:
            loaded = json.load(file)
            if isinstance(loaded, dict) and isinstance(loaded.get("entries"), list):
                data = loaded
    entries = [entry for entry in data["entries"] if entry.get("date") != date]
    entries.append({"date": date, "title": title, "theme": theme, "summary": summary, "file": rel_path})
    data["entries"] = sorted(entries, key=lambda entry: entry.get("date", ""), reverse=True)
    with open(TIPS_INDEX, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")


def main() -> int:
    today = datetime.now(JST).strftime("%Y-%m-%d")
    theme = choose_theme(today)
    print(f"[info] 使い方コンテンツ生成開始: {today} / {theme}")
    candidates = collect_candidates()
    print(f"[info] 候補記事: {len(candidates)}件")
    markdown = generate_tips(*build_messages(candidates, today, theme))
    if not markdown.startswith("---"):
        print("[error] 生成結果が想定フォーマットではありません。中止します。", file=sys.stderr)
        return 1
    meta = parse_front_matter(markdown)
    rel_path = f"tips/{today}.md"
    os.makedirs(TIPS_DIR, exist_ok=True)
    with open(os.path.join(ROOT, rel_path), "w", encoding="utf-8") as file:
        file.write(markdown)
    update_tips_index(today, meta.get("title", "使い方"), meta.get("theme", theme), meta.get("summary", ""), rel_path)
    print(f"[info] 書き出し: {rel_path}")
    print("[info] tips_index.json 更新・完了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
