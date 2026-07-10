#!/usr/bin/env python3
"""Maren AI Radar Cloud Edition — 主检测脚本

每 1 小时在 GitHub Actions 上跑一次：
  1. 拉取上游源（LearnPrompt Radar daily-brief + 官方 RSS）
  2. 检测爆款（S/A/B/C 信号分级）
  3. 触发推送（飞书 + Server酱）
  4. 数据落盘 data/ 目录
"""

import csv
import datetime as dt
import hashlib
import html
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

# ---------- paths ----------
BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"
SRC_CFG = BASE / "config" / "sources.json"

DATA.mkdir(parents=True, exist_ok=True)

USER_AGENT = "MarenAIRadar/1.0 (+https://github.com/mandydudubye-design/maren-ai-radar)"

# ---------- HELPERS ----------
def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)

def stable_id(*parts: str) -> str:
    raw = "|".join(p or "" for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

def fetch_text(url: str, timeout: int = 15) -> str:
    cmd = [
        "curl", "-sS", "--noproxy", "*", "-L",
        "--connect-timeout", str(timeout),
        "--max-time", str(timeout + 10),
        "-A", USER_AGENT,
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 15)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or f"curl 失败: {result.returncode}").strip())
    return result.stdout

def parse_time(val: Any) -> str | None:
    if not val:
        return None
    if isinstance(val, (int, float)):
        try:
            return dt.datetime.fromtimestamp(val, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")
        except Exception:
            return None
    text = str(val).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return dt.datetime.fromisoformat(text).astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError:
        return None

def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))

# ---------- SOURCE FETCHERS ----------
def fetch_radar_daily_brief(url: str) -> list[dict]:
    """Fetch from LearnPrompt ai-news-radar daily-brief.json"""
    text = fetch_text(url)
    data = json.loads(text)
    items = []
    for entry in data.get("items", []):
        title = entry.get("title") or entry.get("headline") or ""
        primary = entry.get("primary_item") or {}
        item_url = entry.get("primary_url") or entry.get("url") or primary.get("url") or ""
        source = entry.get("source_name") or entry.get("source") or primary.get("source") or "Radar"
        published = entry.get("latest_at") or entry.get("earliest_at") or primary.get("published_at")
        score = entry.get("score") or entry.get("importance_score") or 0
        source_count = entry.get("source_count") or len(entry.get("sources") or []) or 1
        items.append({
            "id": stable_id(title, item_url),
            "title": title,
            "url": item_url,
            "source": source,
            "source_type": "radar",
            "published_at": parse_time(published),
            "category": entry.get("category") or "其他",
            "raw_score": float(score),
            "source_count": int(source_count),
            "reasons": entry.get("reasons") or [],
            "importance_label": entry.get("importance_label") or "",
            "importance_breakdown": entry.get("importance_breakdown") or {},
            "source_names": entry.get("source_names") or [],
        })
    return items

def fetch_rss(url: str) -> list[dict]:
    """Fetch RSS feed items"""
    text = fetch_text(url)
    items = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        print(f"  [rss] 解析失败: {url}")
        return items

    # Atom or RSS2
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall(".//item") or root.findall(".//atom:entry", ns):
        title = ""
        item_url = ""
        published = None
        for child in entry:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag == "title":
                title = child.text or ""
            elif tag == "link":
                if child.get("href"):
                    item_url = child.get("href")
                elif child.text and child.text.startswith("http"):
                    item_url = child.text
            elif tag in ("pubDate", "published", "updated"):
                published = child.text
            elif tag == "content":
                if not title or not item_url:
                    pass
        if title:
            items.append({
                "id": stable_id(title, item_url),
                "title": html.unescape(title.strip()),
                "url": item_url,
                "source": url,
                "source_type": "rss",
                "published_at": parse_time(published),
                "category": "其他",
                "raw_score": 50,
                "source_count": 1,
            })
    return items

# ---------- SCORING ----------
PREFERRED_CATEGORIES = [
    "AI Coding", "AI Agent", "Agent", "AI Skill", "MCP",
    "多模态", "模型", "产品", "开源", "工具",
    "商业", "融资", "创业", "开发者",
]

def categorize(title: str, summary: str = "") -> str:
    text = f"{title} {summary}".lower()
    if any(kw in text for kw in ["claude code", "cursor", "windsurf", "aide", "copilot", "codex"]):
        return "AI Coding"
    if any(kw in text for kw in ["agent", "workflow", "mcp", "tool use", "function calling"]):
        return "Agent"
    if any(kw in text for kw in ["开源", "open source", "本地部署", "ollama", "llama"]):
        return "开源"
    if any(kw in text for kw in ["release", "发布", "launch", "上线", "new model"]):
        return "产品"
    if any(kw in text for kw in ["融资", "funding", "投资", "收购", "估值"]):
        return "商业"
    return "其他"


def score_and_grade(item: dict, now: dt.datetime) -> tuple[int, str]:
    """评分 + 分级 S/A/B/C"""
    score = int(float(item.get("raw_score") or 0))

    # 加分：多源
    score += min(int(item.get("source_count") or 1), 5) * 8

    # 加分：偏好类目
    cat = item.get("category", "")
    if cat in PREFERRED_CATEGORIES or any(k in cat for k in ["Coding", "Agent", "多模态"]):
        score += 15

    # 加分：官方源 / RSS 官方源
    st = item.get("source_type", "")
    if st in ("official",):
        score += 15
    elif st == "rss":
        score += 5
    elif st == "radar":
        score += 12  # LearnPrompt 聚合源，多源共振，加分权重高
    if any(official in (item.get("url") or "") for official in ["openai.com", "anthropic.com", "deepmind"]):
        score += 15

    # 减分：过老
    published = item.get("published_at")
    if published:
        try:
            p = dt.datetime.fromisoformat(published.replace("Z", "+00:00"))
            hours = (now - p).total_seconds() / 3600
            if hours > 48:
                score -= 20
        except Exception:
            pass

    score = max(score, 0)

    # 定级
    if score >= 85:
        grade = "S"
    elif score >= 70:
        grade = "A"
    elif score >= 50:
        grade = "B"
    else:
        grade = "C"

    return score, grade


# ---------- PUSH ----------
def push_status_card(
    run_time: str,
    total: int,
    s_list: list,
    a_list: list,
    whitelist_updates: list = None,
    failed: list = None,
):
    """调用推送模块发状态卡"""
    # 动态导入，确保在 GitHub Actions 上也能正确 resolve
    import importlib.util
    spec = importlib.util.spec_from_file_location("push", BASE / "scripts" / "push.py")
    push_mod = importlib.util.module_from_spec(spec)
    sys.modules["push"] = push_mod
    spec.loader.exec_module(push_mod)

    push_mod.notify_status_card(
        run_time=run_time,
        item_count=total,
        s_items=s_list,
        a_items=a_list,
        new_items_from_whitelist=whitelist_updates or [],
        failed_sources=failed or [],
    )


def push_breaking(item: dict):
    """调用推送模块发爆款通知"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("push", BASE / "scripts" / "push.py")
    push_mod = importlib.util.module_from_spec(spec)
    sys.modules["push"] = push_mod
    spec.loader.exec_module(push_mod)
    push_mod.notify_breaking(item)


# ---------- MAIN ----------
def main():
    sources = read_json(SRC_CFG, [])

    all_items: list[dict] = []
    failed_sources: list[str] = []
    now = now_utc()
    run_time = now.strftime("%Y-%m-%d %H:%M (UTC)")

    print(f"[radar] 开始运行 | {run_time}")
    print(f"[radar] 共 {len(sources)} 个源")

    for src in sources:
        sid = src.get("id", "?")
        sname = src.get("name", sid)
        stype = src.get("type", "")
        url = src.get("url", "")
        fallbacks = src.get("fallback_urls", [])
        enabled = src.get("enabled", True)
        if not enabled:
            print(f"  [跳过] {sid}")
            continue

        ok = False
        urls_to_try = [url] + fallbacks
        for u in urls_to_try:
            if not u:
                continue
            try:
                if stype in ("json_daily_brief",):
                    items = fetch_radar_daily_brief(u)
                elif stype in ("x_crawl_rss",):
                    items = fetch_rss(u)
                else:
                    items = []

                all_items.extend(items)
                print(f"  [OK] {sid} ({sname}) → {len(items)} 条 | {u[:60]}...")
                ok = True
                break
            except Exception as e:
                print(f"  [失败] {sid} ({sname}): {e}")

        if not ok:
            failed_sources.append(sname)

    # 去重
    seen_ids = set()
    unique_items: list[dict] = []
    for item in all_items:
        key = item.get("id") or item.get("url") or item.get("title")
        if key and key not in seen_ids:
            seen_ids.add(key)
            unique_items.append(item)

    # 评分
    for item in unique_items:
        cat = categorize(item.get("title", ""), item.get("source", ""))
        item["category"] = cat
        score, grade = score_and_grade(item, now)
        item["score"] = score
        item["grade"] = grade

    unique_items.sort(key=lambda x: x.get("score", 0), reverse=True)

    # === 多样性截取：每源最多保留 N 条，避免单源霸榜 ===
    MAX_PER_SOURCE = 15
    source_counters: dict[str, int] = {}
    diverse_items: list[dict] = []
    for item in unique_items:
        src_key = item.get("source", "unknown")
        cnt = source_counters.get(src_key, 0)
        if cnt < MAX_PER_SOURCE:
            diverse_items.append(item)
            source_counters[src_key] = cnt + 1
        if len(diverse_items) >= 100:
            break

    # 分级列表
    s_items = [i for i in diverse_items if i.get("grade") == "S"]
    a_items = [i for i in diverse_items if i.get("grade") == "A"]

    print(f"\n[radar] 去重后: {len(unique_items)} 条")
    print(f"         S 级: {len(s_items)} | A 级: {len(a_items)} | 失败源: {len(failed_sources)}")

    # === 落盘 ===
    payload = {
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "run_time": run_time,
        "total_items": len(unique_items),
        "s_count": len(s_items),
        "a_count": len(a_items),
        "failed_sources": failed_sources,
        "items": diverse_items[:100],
        "s_items": s_items[:10],
        "a_items": a_items[:20],
    }
    ts = now.strftime("%Y%m%d%H%M")
    write_json(DATA / f"snapshot-{ts}.json", payload)
    write_json(DATA / "latest-snapshot.json", payload)
    print(f"[radar] 数据已写入 data/ 目录")

    # === 推送 ===
    whitelist_updates = [i for i in unique_items if i.get("grade") == "S"][:5] if s_items else None
    push_status_card(run_time, len(unique_items), s_items[:3], a_items[:5], whitelist_updates, failed_sources)

    # 单独推 S 级爆款
    for item in s_items[:3]:
        push_breaking(item)

    print(f"[radar] 完成\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
