#!/usr/bin/env python3
"""AIニュースダイジェストを自動生成するスクリプト。

GitHub Actions から3日に1回呼ばれる想定。
1. Google ニュースのRSS検索で直近のAI関連記事を集める
2. Claude Haiku にTOP5の日本語ダイジェストを書かせる（設定.md の方針に従う）
3. digests/YYYY-MM-DD.md を書き出し、index.json の先頭に追加する

依存: anthropic SDK（pip install anthropic）。RSS取得・解析は標準ライブラリのみ。
APIキーは環境変数 ANTHROPIC_API_KEY から読む（コードには絶対に書かない）。
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

# ── 設定 ────────────────────────────────────────────────────────────
MODEL = "claude-haiku-4-5"          # ユーザーが選んだモデル（コスト重視）
JST = timezone(timedelta(hours=9))  # 日本時間
RECENT_DAYS = 5                     # 直近この日数の記事だけ候補にする
PER_SOURCE = 6                      # 1情報源あたり拾う記事の上限
MAX_CANDIDATES = 50                 # モデルに渡す候補の総上限（トークン節約）

# Google ニュースRSS検索クエリ（設定.md の「入れる」リストに対応）
# 日本語版と英語版の両方を引く＝国内＋海外の最新を拾う（出力は日本語にまとめる）
GOOGLE_QUERIES_JA = [
    "Anthropic Claude",
    "OpenAI ChatGPT Codex",
    "Google Gemini AI",
    "xAI Grok",
    "GitHub Copilot AI 開発ツール",
    "AIエージェント 自律",
]
GOOGLE_QUERIES_EN = [
    "Anthropic Claude",
    "OpenAI ChatGPT Codex",
    "Google Gemini AI model",
    "xAI Grok",
    "AI agent autonomous",
]

# 海外の一次情報・テックメディアのRSS。(表示名, URL, 一般フィードか)
# 「一般フィード=True」のものはAI関連キーワードで絞り込む（AI以外の記事が混ざるため）。
RSS_FEEDS = [
    ("TechCrunch", "https://techcrunch.com/category/artificial-intelligence/feed/", False),
    ("VentureBeat", "https://venturebeat.com/category/ai/feed", False),
    ("OpenAI", "https://openai.com/news/rss.xml", False),
    ("Hacker News", "https://hnrss.org/newest?q=AI+OR+Anthropic+OR+OpenAI+OR+Gemini+OR+Claude", False),
    ("The Verge", "https://www.theverge.com/rss/index.xml", True),
    ("MIT Tech Review", "https://www.technologyreview.com/feed/", True),
]

# 一般フィードを絞り込むためのAI関連キーワード（小文字で照合）
AI_KEYWORDS = (
    "ai", "a.i.", "artificial intelligence", "anthropic", "claude", "openai",
    "chatgpt", "gpt", "gemini", "grok", "copilot", "llm", "agent", "model",
    "machine learning", "生成ai", "エージェント",
)

# プロジェクトのルート（このファイルの1つ上）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 設定.md が手元にあれば読む（ローカル実行用）。
# クラウド（公開リポジトリ）には設定.md を含めないので、無ければ下の埋め込み方針を使う。
EMBEDDED_POLICY = """\
## 形式
- TOP5・短め（1項目3〜4行）
- 日本語のみ。英語記事・専門用語は読まなくて済むようにする
- 専門用語には1行の注釈をつける
- 各項目に「🟢あなたに関係」の1行（キャリア/副業/新規事業/ツール習熟の観点で）
- 末尾に出典リンク

## 入れる（興味の中心）
- Anthropic / Claude / Claude Code の機能・アップデート
- OpenAI / Codex の機能・アップデート
- Grok（xAI）
- Gemini（Google）
- GitHub などAI補助の開発ツール（Copilot 等）
- その他のエージェントAI（自律的に作業するAI）
- AIを活用して伸びている企業

## 入れない（興味の外）
- ロボット・ヒューマノイド・自動車
- 量子コンピュータなど遠い基礎研究
- 一般消費者向けガジェット・スマートホーム
- 医療・ヘルスケアの個別事例（業界全体の大きな動き以外）
"""


def load_policy() -> str:
    """設定.md があればそれを、無ければ埋め込み方針を返す。"""
    path = os.path.join(ROOT, "設定.md")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    return EMBEDDED_POLICY


def google_news_url(query: str, lang: str) -> str:
    """Google ニュースRSS検索のURLを作る。lang は 'ja' か 'en'。"""
    q = urllib.parse.quote(query)
    if lang == "en":
        return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
    return f"https://news.google.com/rss/search?q={q}&hl=ja&gl=JP&ceid=JP:ja"


def fetch_url(url: str, _redirects: int = 0) -> bytes:
    """URLを取得してバイト列を返す。301/302/307はurllibが自動追従、308は手動で追う。"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (ai-news-digest bot)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.read()
    except urllib.error.HTTPError as e:
        # 308 Permanent Redirect は古いurllibが追わないので手動で1回だけ追う
        if e.code == 308 and _redirects < 3:
            loc = e.headers.get("Location")
            if loc:
                return fetch_url(urllib.parse.urljoin(url, loc), _redirects + 1)
        raise


def _local(tag: str) -> str:
    """名前空間付きタグ '{...}entry' から 'entry' を取り出す。"""
    return tag.rsplit("}", 1)[-1]


def _find_local(parent, name: str):
    """子要素から localname が一致する最初の要素を返す。"""
    for child in parent:
        if _local(child.tag) == name:
            return child
    return None


def _text_local(parent, name: str) -> str:
    el = _find_local(parent, name)
    return (el.text or "").strip() if el is not None and el.text else ""


def _parse_date(value: str):
    """RFC822（RSS）かISO8601（Atom）の日付文字列を datetime に。失敗時 None。"""
    if not value:
        return None
    value = value.strip()
    try:
        return parsedate_to_datetime(value)  # RSS の pubDate
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))  # Atom の ISO8601
    except ValueError:
        return None


def _extract_link(entry) -> str:
    """item/entry からリンクURLを取り出す（RSSはテキスト、Atomは href 属性）。"""
    alt = ""
    for child in entry:
        if _local(child.tag) != "link":
            continue
        href = child.get("href")
        if href:  # Atom 形式
            rel = child.get("rel", "alternate")
            if rel == "alternate":
                return href.strip()
            alt = alt or href.strip()
        elif child.text and child.text.strip():  # RSS 形式
            return child.text.strip()
    return alt


def _is_ai_related(title: str) -> bool:
    low = title.lower()
    return any(k in low for k in AI_KEYWORDS)


def parse_feed(xml_bytes: bytes, cutoff: datetime, default_source: str = "",
               keyword_filter: bool = False) -> list[dict]:
    """RSS 2.0 / Atom どちらの item/entry も (title, link, source, date) に変換。
    cutoff より古いものは除外。keyword_filter=True なら AI 関連の見出しだけ残す。"""
    items = []
    root = ElementTree.fromstring(xml_bytes)
    for entry in root.iter():
        if _local(entry.tag) not in ("item", "entry"):
            continue
        title = _text_local(entry, "title")
        link = _extract_link(entry)
        if not (title and link):
            continue
        if keyword_filter and not _is_ai_related(title):
            continue
        # 出典名: Google ニュースは <source>、直接フィードは default_source
        source_el = _find_local(entry, "source")
        source = (source_el.text or "").strip() if source_el is not None and source_el.text else default_source
        when = _parse_date(_text_local(entry, "pubDate") or _text_local(entry, "published")
                           or _text_local(entry, "updated") or _text_local(entry, "date"))
        # 日付が読めた場合だけ古い記事を除外（読めなければ残す）
        if when is not None and when < cutoff:
            continue
        items.append({
            "title": title,
            "link": link,
            "source": source,
            "date": when.astimezone(JST).strftime("%Y-%m-%d") if when else "",
        })
    return items


def collect_candidates() -> list[dict]:
    """Google ニュース（日英）＋海外RSSを集めて、重複を除いた候補記事リストを返す。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)
    seen = set()
    candidates = []

    # 1情報源 = (ラベル, URL, default_source, keyword_filter)
    sources = []
    for q in GOOGLE_QUERIES_JA:
        sources.append((f"Googleニュース日本語 '{q}'", google_news_url(q, "ja"), "", False))
    for q in GOOGLE_QUERIES_EN:
        sources.append((f"Googleニュース英語 '{q}'", google_news_url(q, "en"), "", False))
    for name, url, general in RSS_FEEDS:
        sources.append((name, url, name, general))

    for label, url, default_source, keyword_filter in sources:
        try:
            xml_bytes = fetch_url(url)
            items = parse_feed(xml_bytes, cutoff, default_source, keyword_filter)
        except Exception as e:  # noqa: BLE001 — 1情報源の失敗で全体を止めない
            print(f"[warn] 取得失敗 '{label}': {e}", file=sys.stderr)
            continue
        for item in items[:PER_SOURCE]:
            key = re.sub(r"\s+", "", item["title"]).lower()[:40]
            if key in seen:
                continue
            seen.add(key)
            candidates.append(item)
    return candidates[:MAX_CANDIDATES]


def build_messages(candidates: list[dict], policy: str, today: str):
    """モデルへのsystem / user メッセージを組み立てる。"""
    lines = []
    for i, c in enumerate(candidates, 1):
        src = f"（{c['source']}・{c['date']}）" if c["source"] else ""
        lines.append(f"{i}. {c['title']} {src}\n   出典URL: {c['link']}")
    article_block = "\n".join(lines)

    system = f"""あなたはAIニュースの編集者です。下記の「選定方針」に厳密に従い、
渡された候補記事から最も重要な5件を選び、日本語の「AIニュース TOP5」ダイジェストを書きます。

# 選定方針
{policy}

# 重要なルール
- 期間は直近3日ほど。「今週」ではなく「最近」「直近」という表現を使う。
- 候補には英語の海外記事も混じっている。見出しが英語でも、本文・見出しはすべて日本語に翻訳して書く。
- 国内・海外を問わず、本当に重要で新しい動きを優先して選ぶ。
- 「入れない」カテゴリの記事は選ばない。
- 重複した話題はまとめて1項目にする。
- 各項目の出典は、候補に付いている「出典URL」をそのまま source に使う（URLを創作しない）。
- 事実を創作しない。候補に書かれている範囲で書く。

# 出力フォーマット（厳守・このまま出力する。前後に説明やコードフェンスを付けない）
---
date: {today}
title: 最新のAIニュース TOP5
summary: （一覧用の短い見出し。主要トピックを「／」で2〜3個つなぐ。20〜30字）
intro: 英語記事や専門用語は読まなくてOK。下の日本語まとめだけで「今どこまで来てるか」が分かるようにしてあります。各項目に「🟢あなたに関係」の1行をつけています。
oneliner: （直近の流れを1〜2文でまとめた「ひとこと」）
---

## （1件目の見出し）
本文を3〜4行で。重要な数字や固有名は **太字** にしてよい。

- term: 専門用語＝やさしい1行説明（用語が無ければこの行は省く）
- relate: キャリア/副業/新規事業/ツール習熟の観点で、読者に関係する一言
- source: https://（候補の出典URL）

## （2件目の見出し）
...同じ形式で5件まで。"""

    user = f"今日の日付: {today}\n\n候補記事（{len(candidates)}件）:\n{article_block}\n\n上記から重要な5件を選び、指定フォーマットでダイジェストを出力してください。"
    return system, user


def generate_digest(system: str, user: str) -> str:
    """Claude Haiku を呼んでダイジェストのMarkdownを得る。"""
    from anthropic import Anthropic

    client = Anthropic()  # ANTHROPIC_API_KEY を環境変数から自動で読む
    message = client.messages.create(
        model=MODEL,
        max_tokens=2500,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(block.text for block in message.content if block.type == "text")
    return clean_output(text)


def clean_output(text: str) -> str:
    """コードフェンスや前置きを取り除き、front-matter から始まる本文にする。"""
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\n", "", text)
    text = re.sub(r"\n```$", "", text)
    idx = text.find("---")
    if idx > 0:
        text = text[idx:]
    return text.strip() + "\n"


def parse_front_matter(markdown: str) -> dict:
    """front-matter を辞書にする（index.json 更新用に title / summary を取り出す）。"""
    meta = {}
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        return meta
    for line in lines[1:]:
        if line.strip() == "---":
            break
        sep = line.find(":")
        if sep > 0:
            meta[line[:sep].strip()] = line[sep + 1:].strip()
    return meta


def update_index(date: str, title: str, summary: str, rel_path: str):
    """index.json の weeks 先頭に新しいエントリを足す（同じ日付は置き換え）。"""
    index_path = os.path.join(ROOT, "index.json")
    with open(index_path, encoding="utf-8") as f:
        data = json.load(f)
    weeks = data.get("weeks", [])
    weeks = [w for w in weeks if w.get("date") != date]
    weeks.insert(0, {"date": date, "title": title, "summary": summary, "file": rel_path})
    weeks.sort(key=lambda w: w.get("date", ""), reverse=True)
    data["weeks"] = weeks
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main() -> int:
    today = datetime.now(JST).strftime("%Y-%m-%d")
    print(f"[info] ダイジェスト生成開始: {today}")

    candidates = collect_candidates()
    print(f"[info] 候補記事: {len(candidates)}件")
    if len(candidates) < 5:
        print("[error] 候補記事が5件未満。ダイジェストを作らず終了します。", file=sys.stderr)
        return 1

    policy = load_policy()
    system, user = build_messages(candidates, policy, today)
    markdown = generate_digest(system, user)

    if not markdown.startswith("---"):
        print("[error] 生成結果が想定フォーマットではありません。中止します。", file=sys.stderr)
        print(markdown[:500], file=sys.stderr)
        return 1

    meta = parse_front_matter(markdown)
    title = meta.get("title", "最新のAIニュース TOP5")
    summary = meta.get("summary", "")

    rel_path = f"digests/{today}.md"
    digest_path = os.path.join(ROOT, rel_path)
    os.makedirs(os.path.dirname(digest_path), exist_ok=True)
    with open(digest_path, "w", encoding="utf-8") as f:
        f.write(markdown)
    print(f"[info] 書き出し: {rel_path}")

    update_index(today, title, summary, rel_path)
    print(f"[info] index.json 更新: {summary}")
    print("[info] 完了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
