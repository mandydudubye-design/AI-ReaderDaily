#!/usr/bin/env python3
"""Maren AI Radar Cloud Edition — 主检测脚本

每 1 小时在 GitHub Actions 上跑一次：
  1. 拉取上游源（LearnPrompt Radar daily-brief + 官方 RSS）
  2. 检测爆款（S/A/B/C 信号分级）
  3. 故事线合并（多源聚簇 + 标题相似度）
  4. 信源健康统计（成功/失败/AI占比）
  5. 触发推送（飞书 + Server酱）
  6. 数据落盘 data/ 目录
"""

import csv
import datetime as dt
import hashlib
import html
import io
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any

# ---------- paths ----------
BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"
SRC_CFG = BASE / "config" / "sources.json"
SCORING_CFG = BASE / "config" / "scoring.json"

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
        "curl", "-sS", "-L",
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
        # 上游 score 是 0-1 小数，归一化到 0-100 整数
        if isinstance(score, (int, float)) and 0 <= score <= 1:
            score = score * 100
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

def fetch_rss(url: str, source_name: str = "") -> list[dict]:
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
            # === 动态 raw_score：按标题关键词区分 ===
            text_lower = title.lower()
            raw_score = 35  # 基础分
            # 高分关键词
            if any(kw in text_lower for kw in ["gpt-5", "gpt5", "claude 4", "gemini 3", "release", "launch", "发布", "新模型"]):
                raw_score = 60
            elif any(kw in text_lower for kw in ["agent", "mcp", "copilot", "codex", "tool use", "function calling"]):
                raw_score = 55
            elif any(kw in text_lower for kw in ["open source", "开源", "本地部署", "benchmark", "性能"]):
                raw_score = 50
            elif any(kw in text_lower for kw in ["update", "更新", "升级", "改进"]):
                raw_score = 45
            # 加分：HuggingFace blog 论文类降分
            if "huggingface.co" in url and any(kw in text_lower for kw in ["paper", "论文", "research", "研究"]):
                raw_score = max(raw_score - 10, 30)

            items.append({
                "id": stable_id(title, item_url),
                "title": html.unescape(title.strip()),
                "url": item_url,
                "source": source_name or url,
                "source_type": "rss",
                "published_at": parse_time(published),
                "category": "其他",
                "raw_score": raw_score,
                "source_count": 1,
            })
    return items

# ---------- SCORING ----------
def infer_source_type(url: str, source_type: str = "") -> str:
    """Map feed origins to a small, configuration-driven trust taxonomy."""
    url = (url or "").lower()
    if source_type == "radar":
        return "aggregator"
    if "github.com" in url or "github.blog" in url:
        return "github"
    if any(domain in url for domain in [
        "openai.com", "anthropic.com", "deepmind.google", "blog.google",
        "ai.meta.com", "huggingface.co", "mistral.ai", "x.ai",
    ]):
        return "official"
    return "unknown"


def is_ai_relevant(title: str, scoring: dict) -> bool:
    """Avoid putting unrelated general-tech and social-feed items on the radar."""
    text = title.lower()
    keywords = scoring.get("ai_keywords", [])
    return any(keyword.lower() in text for keyword in keywords)


def categorize(title: str, scoring: dict, summary: str = "") -> str:
    text = f"{title} {summary}".lower()
    best_category = "其他"
    best_hits = 0
    for category, keywords in scoring.get("category_keywords", {}).items():
        hits = sum(1 for keyword in keywords if keyword.lower() in text)
        if hits > best_hits:
            best_category, best_hits = category, hits
    if best_hits:
        return best_category
    return "其他"


def score_and_grade(item: dict, now: dt.datetime, scoring: dict) -> tuple[int, str]:
    """评分 + 分级 S/A/B/C"""
    score = int(float(item.get("raw_score") or 0))

    # 加分：多源（最多 +20）
    score += min(int(item.get("source_count") or 1), 5) * 4

    # 加分：偏好类目
    cat = item.get("category", "")
    if cat in scoring.get("preferred_categories", []):
        score += int(scoring.get("preferred_category_bonus", 8))

    # 加分：官方源 / RSS 官方源
    st = infer_source_type(item.get("url", ""), item.get("source_type", ""))
    item["source_type"] = st
    score += int(scoring.get("source_type_weights", {}).get(st, 0))

    # 减分：过老
    published = item.get("published_at")
    if published:
        try:
            p = dt.datetime.fromisoformat(published.replace("Z", "+00:00"))
            hours = (now - p).total_seconds() / 3600
            if hours > 48:
                score -= 15
        except Exception:
            pass

    score = max(score, 0)

    # 定级（S 稀缺，避免全 S）
    thresholds = scoring.get("grade_thresholds", {})
    if score >= int(thresholds.get("S", 100)):
        grade = "S"
    elif score >= int(thresholds.get("A", 80)):
        grade = "A"
    elif score >= int(thresholds.get("B", 60)):
        grade = "B"
    else:
        grade = "C"

    return score, grade


def build_information_card(item: dict) -> dict:
    """Stable, downstream-facing card schema for the website and writing tools."""
    category = item.get("category") or "其他"
    angle_map = {
        "AI 编程": "关注开发效率、工具体验与团队协作方式的变化。",
        "智能体": "关注 Agent 的可落地场景、工作流和可靠性。",
        "模型": "关注能力边界、成本、生态和真实使用影响。",
        "产品": "关注产品定位、用户价值与竞品差异。",
        "开源": "关注部署门槛、社区生态和可复用能力。",
        "多模态": "关注图像、视频、语音与创作工作流的实际价值。",
        "商业": "关注商业化、融资、企业采用和行业格局。",
    }
    return {
        "id": item.get("id"),
        "title": item.get("title"),
        "url": item.get("url"),
        "source": item.get("source"),
        "published_at": item.get("published_at"),
        "category": category,
        "score": item.get("score"),
        "grade": item.get("grade"),
        "why_it_matters": f"{item.get('source_count', 1)} 个来源信号；{category} 方向；综合评分 {item.get('score', 0)}。",
        "topic_angle": angle_map.get(category, "作为 AI 行业动态线索，回看原文后判断是否值得跟进。"),
        "source_names": item.get("source_names") or [item.get("source")],
    }


# ---------- STORY MERGING ----------
def _title_similarity(t1: str, t2: str) -> float:
    """计算两条标题的文本相似度（0-1），基于词袋 Jaccard + 公共子串"""
    if not t1 or not t2:
        return 0.0
    t1_low, t2_low = t1.lower(), t2.lower()
    # 完全包含
    if t1_low in t2_low or t2_low in t1_low:
        return 0.95
    # 词袋 Jaccard
    def tokenize(s: str) -> set:
        # 按空格、标点切分，保留英文词 + 中文词
        tokens = set(re.findall(r'[a-zA-Z0-9]+|[\u4e00-\u9fff]+', s.lower()))
        return tokens
    s1, s2 = tokenize(t1), tokenize(t2)
    if not s1 or not s2:
        return 0.0
    inter = len(s1 & s2)
    union = len(s1 | s2)
    return inter / union if union > 0 else 0.0


def merge_stories(items: list[dict], now: dt.datetime) -> list[dict]:
    """基于标题相似度 + 时间窗口 + URL 域名匹配，将分散条目合并成故事线

    返回 stories 列表，每项含：
      - title: 最佳标题
      - primary_url: 最佳 URL
      - source_count: 去重后的站源数
      - source_names: 站源名称列表
      - sources: 原始条目列表
      - earliest_at: 最早发布时间
      - latest_at: 最晚发布时间
      - importance_score: 聚合重要性（综合源数 + 最高分 + 新泽西）
      - importance_label: S/A/B/C
    """
    if not items:
        return []

    SIMILARITY_THRESHOLD = 0.35  # 词袋 Jaccard >= 0.35 认为同一事件
    TIME_WINDOW_HOURS = 36       # 最多跨 36h 合并

    # 按时间排序（最新的在前）
    sorted_items = sorted(items, key=lambda i: i.get("published_at") or "", reverse=True)

    used = set()
    stories: list[dict] = []

    for i, item in enumerate(sorted_items):
        iid = item.get("id") or item.get("url") or item.get("title", "")
        if iid in used:
            continue

        cluster = [item]
        used.add(iid)
        title_i = item.get("title", "")
        pub_i = item.get("published_at")

        for j in range(i + 1, len(sorted_items)):
            jid = sorted_items[j].get("id") or sorted_items[j].get("url") or sorted_items[j].get("title", "")
            if jid in used:
                continue
            title_j = sorted_items[j].get("title", "")
            if _title_similarity(title_i, title_j) >= SIMILARITY_THRESHOLD:
                # 时间窗口检查
                pub_j = sorted_items[j].get("published_at")
                if pub_i and pub_j:
                    try:
                        ti = dt.datetime.fromisoformat(pub_i.replace("Z", "+00:00"))
                        tj = dt.datetime.fromisoformat(pub_j.replace("Z", "+00:00"))
                        diff_h = abs((ti - tj).total_seconds()) / 3600
                        if diff_h > TIME_WINDOW_HOURS:
                            continue
                    except Exception:
                        pass
                cluster.append(sorted_items[j])
                used.add(jid)

        if len(cluster) < 1:
            continue

        # 聚合
        sources_set: dict[str, str] = {}  # source_name -> url
        all_titles = []
        all_urls = []
        all_scores = []
        earliest = None
        latest = None

        for c in cluster:
            src = c.get("source", "未知")
            c_url = c.get("url", "")
            if src and src not in sources_set:
                sources_set[src] = c_url
            all_titles.append(c.get("title", ""))
            if c_url:
                all_urls.append(c_url)
            score = c.get("score") or c.get("raw_score") or 0
            if isinstance(score, (int, float)):
                all_scores.append(score)
            pub = c.get("published_at")
            if pub:
                try:
                    pt = dt.datetime.fromisoformat(pub.replace("Z", "+00:00"))
                    if earliest is None or pt < earliest:
                        earliest = pt
                    if latest is None or pt > latest:
                        latest = pt
                except Exception:
                    pass

        # 选最具代表性标题（选字数最长的，通常是信息最完整的）
        best_title = max(all_titles, key=lambda t: len(t)) if all_titles else "未知"
        primary_url = all_urls[0] if all_urls else ""
        source_count = len(sources_set)
        max_score = max(all_scores) if all_scores else 0

        # 热度因子：多源 + 最高分 + 新鲜度
        heat = source_count * 15 + min(max_score, 100) * 0.6
        # 新鲜度加分（< 6h 加分）
        if latest:
            age_h = (now - latest).total_seconds() / 3600
            if age_h < 6:
                heat += (6 - age_h) * 3

        # S/A/B/C 定级
        if heat >= 80:
            imp_label = "S"
        elif heat >= 60:
            imp_label = "A"
        elif heat >= 40:
            imp_label = "B"
        else:
            imp_label = "C"

        stories.append({
            "title": best_title,
            "primary_url": primary_url,
            "source_count": source_count,
            "source_names": list(sources_set.keys()),
            "cluster_size": len(cluster),
            "item_count": len(cluster),
            "earliest_at": earliest.isoformat().replace("+00:00", "Z") if earliest else None,
            "latest_at": latest.isoformat().replace("+00:00", "Z") if latest else None,
            "importance_score": round(heat, 1),
            "importance_label": imp_label,
        })

    # 按 importance 降序
    stories.sort(key=lambda s: -s["importance_score"])
    return stories


def _load_snapshot_history(max_snapshots: int = 48) -> list[tuple[str, list[dict]]]:
    history: list[tuple[str, list[dict]]] = []
    for path in sorted(DATA.glob("snapshot-*.json"))[-max_snapshots:]:
        payload = read_json(path, {})
        if not payload:
            continue
        ts = payload.get("run_time") or payload.get("generated_at") or path.stem.replace("snapshot-", "")
        history.append((str(ts), payload.get("items") or []))
    return history


def _match_item_points(title: str, url: str, history: list[tuple[str, list[dict]]]) -> list[dict]:
    url = (url or "").split("?")[0]
    points: list[dict] = []
    for ts, items in history:
        best_score = 0
        best_sources = 0
        for item in items:
            item_url = (item.get("url") or "").split("?")[0]
            same_url = bool(url and item_url and url == item_url)
            similar = _title_similarity(title, item.get("title", "")) >= 0.35
            if not (same_url or similar):
                continue
            best_score = max(best_score, int(item.get("score") or 0))
            best_sources = max(best_sources, int(item.get("source_count") or 1))
        if best_score > 0:
            points.append({"ts": ts, "score": best_score, "source_count": best_sources})
    return points


def attach_story_heat_trends(stories: list[dict], max_snapshots: int = 48) -> None:
    """Attach per-story heat trend points from recent hourly snapshots."""
    history = _load_snapshot_history(max_snapshots)
    for story in stories:
        title = story.get("title", "")
        url = story.get("primary_url", "")
        points = _match_item_points(title, url, history)
        story["story_id"] = stable_id(title, url)
        story["heat_trend"] = points[-24:]
        story["heat_delta"] = 0
        if len(points) >= 2:
            story["heat_delta"] = points[-1]["source_count"] - points[0]["source_count"]


def attach_item_heat_trends(items: list[dict], max_snapshots: int = 48) -> None:
    history = _load_snapshot_history(max_snapshots)
    for item in items:
        points = _match_item_points(item.get("title", ""), item.get("url", ""), history)
        item["heat_trend"] = points[-24:]
        item["heat_delta"] = 0
        if len(points) >= 2:
            item["heat_delta"] = points[-1]["source_count"] - points[0]["source_count"]


# ---------- SOURCE HEALTH STATS ----------
def build_source_stats(
    src_config: list[dict],
    all_items: list[dict],
    failed_names: list[str],
    fetch_durations: dict[str, float],
    now: dt.datetime,
) -> dict:
    """构建信源健康统计字典（source-status.json）"""
    sites = []
    successful_sites = 0
    failed_sites = 0
    zero_item_sites = 0
    total_ai_items = 0

    # 信源维度统计
    src_items_count: dict[str, int] = {}
    src_ai_count: dict[str, int] = {}
    for item in all_items:
        source_id = item.get("_source_id", "unknown")
        src_items_count[source_id] = src_items_count.get(source_id, 0) + 1
        grade = item.get("grade", "")
        if grade in ("S", "A", "B"):
            src_ai_count[source_id] = src_ai_count.get(source_id, 0) + 1
            total_ai_items += 1

    for src in src_config:
        sid = src.get("id", "?")
        sname = src.get("name", sid)
        enabled = src.get("enabled", True)
        if not enabled:
            continue
        ok = sname not in failed_names
        item_count = src_items_count.get(sid, 0)
        ai_count = src_ai_count.get(sid, 0)
        ai_pct = round(ai_count / item_count * 100, 1) if item_count > 0 else 0.0
        duration = fetch_durations.get(sid, 0.0)

        if ok:
            successful_sites += 1
        else:
            failed_sites += 1
        if item_count == 0:
            zero_item_sites += 1

        sites.append({
            "site_id": sid,
            "site_name": sname,
            "ok": ok,
            "enabled": enabled,
            "item_count": item_count,
            "ai_count": ai_count,
            "ai_pct": ai_pct,
            "fetch_duration_s": round(duration, 2),
            "note": src.get("note", ""),
        })

    total_items = len(all_items)
    return {
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "source_count": len(sites),
        "successful_sites": successful_sites,
        "failed_sites": failed_sites,
        "zero_item_sites": zero_item_sites,
        "total_items": total_items,
        "total_ai_items": total_ai_items,
        "ai_pct_total": round(total_ai_items / total_items * 100, 1) if total_items > 0 else 0.0,
        "sites": sites,
    }
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
    scoring = read_json(SCORING_CFG, {})

    all_items: list[dict] = []
    failed_sources: list[str] = []
    fetch_durations: dict[str, float] = {}  # src_id -> 抓取耗时(秒)
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
        start_t = time.time()
        for u in urls_to_try:
            if not u:
                continue
            try:
                if stype in ("json_daily_brief",):
                    items = fetch_radar_daily_brief(u)
                elif stype in ("x_crawl_rss",):
                    items = fetch_rss(u, source_name=sname)
                    if src.get("filter_ai", False):
                        items = [item for item in items if is_ai_relevant(item["title"], scoring)]
                elif stype == "json_source_status":
                    # Metadata-only upstream source: useful for provenance, but not a news feed.
                    fetch_text(u)
                    items = []
                else:
                    raise ValueError(f"不支持的信源类型: {stype}")

                for item in items:
                    item["_source_id"] = sid
                all_items.extend(items)
                print(f"  [OK] {sid} ({sname}) → {len(items)} 条 | {u[:60]}...")
                ok = True
                break
            except Exception as e:
                print(f"  [失败] {sid} ({sname}): {e}")

        fetch_durations[sid] = round(time.time() - start_t, 2)

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
        cat = categorize(item.get("title", ""), scoring, item.get("source", ""))
        item["category"] = cat
        score, grade = score_and_grade(item, now, scoring)
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

    # === 故事线合并（多源聚簇）===
    stories = merge_stories(diverse_items, now)
    attach_story_heat_trends(stories)
    attach_item_heat_trends(diverse_items)
    stories_s = [s for s in stories if s["importance_label"] == "S"]
    stories_a = [s for s in stories if s["importance_label"] == "A"]
    print(f"[radar] 故事线合并: {len(stories)} 条故事 | S 级: {len(stories_s)} | A 级: {len(stories_a)}")

    # === 信源健康统计 ===
    source_status = build_source_stats(sources, unique_items, failed_sources, fetch_durations, now)
    print(f"[radar] 信源健康: {source_status['successful_sites']}/{source_status['source_count']} 成功 | AI 占比: {source_status['ai_pct_total']}%")

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
    # 新增：故事线合并数据 + 信源健康数据
    write_json(DATA / "stories-merged.json", {
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "total_stories": len(stories),
        "s_stories": len(stories_s),
        "a_stories": len(stories_a),
        "stories": stories,
    })
    write_json(DATA / "source-status.json", source_status)
    cards = [build_information_card(item) for item in diverse_items[:100]]
    write_json(DATA / "daily-brief.json", {
        "schema_version": "1.0",
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "items": cards,
    })
    write_json(DATA / "latest-24h.json", {
        "schema_version": "1.0",
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "items": [card for card in cards if card.get("grade") in ("S", "A", "B")],
    })
    print(f"[radar] 数据已写入 data/ 目录（含 stories-merged.json + source-status.json）")

    # === 趋势数据（追加 trend.json，最多保留 48 小时）===
    trend_path = DATA / "trend.json"
    trend_entry = {
        "ts": now.strftime("%Y-%m-%d %H:%M UTC"),
        "total": len(diverse_items),
        "s": len(s_items),
        "a": len(a_items),
        "b": sum(1 for i in diverse_items if i.get("grade") == "B"),
        "c": sum(1 for i in diverse_items if i.get("grade") == "C"),
    }
    history = read_json(trend_path, [])
    history.append(trend_entry)
    # 保留最多 48 条（48 小时）
    history = history[-48:]
    write_json(trend_path, history)
    print(f"[radar] 趋势数据已追加到 trend.json（共 {len(history)} 条）")

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
