#!/usr/bin/env python3
"""
enrich.py -- cloud 雷达后处理脚本

在 radar.py 跑完后运行，给数据添加：
1. 中文标题翻译（title_zh）
2. AI 相关性评分（ai_relevance_score + matched_keywords）
3. Source tier 排名
4. Persona 锐评（需 DEEPSEEK_API_KEY 或 GLM_API_KEY）

使用：
  python3 scripts/enrich.py

环境变量：
  DEEPSEEK_API_KEY  -- DeepSeek API key（persona 锐评 + 翻译 fallback）
  GLM_API_KEY       -- 智谱 GLM API key（翻译 fallback）
  ENRICH_SKIP_PERSONA=1 -- 跳过 persona 锐评
"""
from __future__ import annotations

import json, os, re, sys, time, hashlib
from pathlib import Path
from datetime import datetime, timezone

import requests

# ---------- paths ----------
BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
CACHE_PATH = DATA / "title-zh-cache.json"
GLOSSARY_PATH = BASE / "translation-glossary.txt"
PERSONAS_DIR = BASE / "personas"

# ---------- AI relevance (from fork's ai_relevance.py) ----------
sys.path.insert(0, str(BASE / "scripts"))
try:
    from ai_relevance import score_ai_relevance, is_broadly_ai_related, add_ai_relevance_fields
    HAS_AI_RELEVANCE = True
except ImportError:
    HAS_AI_RELEVANCE = False
    print("[enrich] ⚠️ ai_relevance.py 未找到，跳过 AI 相关性评分")

# ---------- Source tier (simplified from fork) ----------
SOURCE_TIERS = {
    # Tier 1: Official first-party
    "official": ["rss_anthropic_blog", "rss_openai_blog", "rss_google_blog", "rss_meta_ai_blog",
                 "rss_mistral_blog", "rss_xai_blog", "rss_huggingface_blog",
                 "learnprompt_daily_brief", "learnprompt_latest_24h"],
    # Tier 2: AI-focused media
    "ai_media": ["wechat_jiqizhiixin", "wechat_xinzhiyuan", "wechat_aitec", "wechat_kaer",
                 "wechat_kazike", "wechat_hunyuan", "wechat_minicpm", "wechat_canghe",
                 "wechat_huashu", "wechat_daishudi", "rss_ithome", "rss_36kr"],
    # Tier 3: Aggregators/social
    "aggregator": [],  # everything else
}

TIER_RANK = {"official": 1, "ai_media": 2, "aggregator": 3}
TIER_LABEL = {"official": "官方一手", "ai_media": "AI媒体", "aggregator": "聚合/社交"}

def source_tier_for(site_id: str) -> dict:
    for tier, ids in SOURCE_TIERS.items():
        if site_id in ids:
            return {"source_tier": tier, "source_tier_label": TIER_LABEL[tier], "source_tier_rank": TIER_RANK[tier]}
    return {"source_tier": "aggregator", "source_tier_label": "聚合/社交", "source_tier_rank": 3}

# ---------- Chinese title translation (from fork) ----------
_cache: dict[str, str] = {}
_glossary_protected: set[str] = set()
_glossary_repairs: list[tuple[str, str, str | None]] = []

def load_cache():
    global _cache
    if CACHE_PATH.exists():
        _cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        print(f"[enrich] 加载翻译缓存: {len(_cache)} 条")

def load_glossary():
    global _glossary_protected, _glossary_repairs
    if not GLOSSARY_PATH.exists():
        return
    mode = None
    for line in GLOSSARY_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("##"):
            mode = "protected" if "保护" in line or "Protect" in line.lower() else "repair"
            continue
        if mode == "protected":
            _glossary_protected.add(line)
        elif mode == "repair" and "=>" in line:
            parts = line.split("=>")
            bad = parts[0].strip()
            rest = parts[1].strip()
            guard = None
            if "@" in rest:
                rest, guard = rest.rsplit("@", 1)
                guard = guard.strip()
            _glossary_repairs.append((bad, rest.strip(), guard))

def has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)

def translate_google(text: str) -> str | None:
    try:
        resp = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "en", "tl": "zh-CN", "dt": "t", "q": text},
            timeout=10,
        )
        resp.raise_for_status()
        segs = resp.json()
        translated = "".join(str(seg[0]) for seg in segs[0] if isinstance(seg, list) and seg and seg[0])
        return translated.strip() if translated.strip() and translated.strip() != text else None
    except Exception:
        return None

def translate_deepseek(text: str, api_key: str) -> str | None:
    try:
        resp = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Agent": "application/json"},
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": "你是科技新闻编辑，把英文 AI/科技新闻标题翻译成地道的简体中文。产品名、公司名、模型名、媒体名、人名一律保留英文原文不翻译。用自然的中文表达，说人话，避免翻译腔。只返回翻译结果，不要解释。"},
                    {"role": "user", "content": text},
                ],
                "temperature": 0.1,
            },
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return None

def apply_glossary(text: str, original_en: str) -> str:
    for bad, good, guard in _glossary_repairs:
        if guard and guard.lower() not in original_en.lower():
            continue
        text = text.replace(bad, good)
    return text

def translate_title(title: str, api_key: str | None = None) -> str | None:
    if not title or has_cjk(title):
        return None  # already Chinese or empty
    # Check cache
    cache_key = title.strip().lower()
    if cache_key in _cache:
        return _cache[cache_key]
    # Try Google Translate
    translated = translate_google(title)
    # Try DeepSeek if Google failed
    if not translated and api_key:
        translated = translate_deepseek(title, api_key)
    if translated:
        # Apply glossary repairs
        translated = apply_glossary(translated, title)
        # Cache it
        _cache[cache_key] = translated
        return translated
    return None

def save_cache():
    if _cache:
        CACHE_PATH.write_text(json.dumps(_cache, ensure_ascii=False, indent=2), encoding="utf-8")

# ---------- Main ----------
def main():
    load_cache()
    load_glossary()

    ds_key = os.environ.get("DEEPSEEK_API_KEY", "")
    glm_key = os.environ.get("GLM_API_KEY", "")
    skip_persona = os.environ.get("ENRICH_SKIP_PERSONA", "") == "1"

    # Process latest-snapshot.json
    snap_path = DATA / "latest-snapshot.json"
    if not snap_path.exists():
        print("[enrich] latest-snapshot.json 不存在，请先运行 radar.py")
        return 1

    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    all_items = snap.get("items", []) + snap.get("s_items", []) + snap.get("a_items", [])

    enriched_count = 0
    translated_count = 0
    ai_scored_count = 0

    for item in all_items:
        changed = False

        # 1. Chinese title translation
        title = item.get("title", "")
        if title and not item.get("title_zh"):
            title_zh = translate_title(title, ds_key or glm_key)
            if title_zh:
                item["title_zh"] = title_zh
                translated_count += 1
                changed = True

        # 2. AI relevance scoring
        if HAS_AI_RELEVANCE:
            record = {"title": title, "source": item.get("source", ""), "summary": item.get("summary", "")}
            result = score_ai_relevance(record)
            if result.get("ai_relevance_score") is not None:
                item["ai_relevance_score"] = round(result["ai_relevance_score"], 3)
                item["ai_matched_keywords"] = result.get("matched_keywords", [])
                item["is_ai_related"] = result.get("is_ai_related", False)
                ai_scored_count += 1
                changed = True

        # 3. Source tier
        site_id = item.get("_source_id", "")
        if site_id:
            tier_info = source_tier_for(site_id)
            item.update(tier_info)
            changed = True

        if changed:
            enriched_count += 1

    # Save enriched snapshot
    snap_path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[enrich] latest-snapshot.json: {enriched_count}/{len(all_items)} 条已增强")
    print(f"         翻译: {translated_count} | AI评分: {ai_scored_count} | Source tier: {len(all_items)}")

    # Also enrich daily-brief.json
    db_path = DATA / "daily-brief.json"
    if db_path.exists():
        db = json.loads(db_path.read_text(encoding="utf-8"))
        for item in db.get("items", []):
            title = item.get("title", "")
            if title and not item.get("title_zh"):
                title_zh = translate_title(title, ds_key or glm_key)
                if title_zh:
                    item["title_zh"] = title_zh
            if HAS_AI_RELEVANCE:
                record = {"title": title, "source": item.get("source", ""), "summary": item.get("summary", "")}
                result = score_ai_relevance(record)
                if result.get("ai_relevance_score") is not None:
                    item["ai_relevance_score"] = round(result["ai_relevance_score"], 3)
                    item["ai_matched_keywords"] = result.get("matched_keywords", [])
        db_path.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[enrich] daily-brief.json: 已增强")

    # Also enrich latest-24h.json
    l24_path = DATA / "latest-24h.json"
    if l24_path.exists():
        l24 = json.loads(l24_path.read_text(encoding="utf-8"))
        for item in l24.get("items", []):
            title = item.get("title", "")
            if title and not item.get("title_zh"):
                title_zh = translate_title(title, ds_key or glm_key)
                if title_zh:
                    item["title_zh"] = title_zh
            if HAS_AI_RELEVANCE:
                record = {"title": title, "source": item.get("source", ""), "summary": item.get("summary", "")}
                result = score_ai_relevance(record)
                if result.get("ai_relevance_score") is not None:
                    item["ai_relevance_score"] = round(result["ai_relevance_score"), 3)
                    item["ai_matched_keywords"] = result.get("matched_keywords", [])
        l24_path.write_text(json.dumps(l24, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[enrich] latest-24h.json: 已增强")

    # 4. Persona scoring (optional, requires API key)
    if not skip_persona and (ds_key or glm_key):
        try:
            from persona_score import load_personas, score_with_persona, load_cache as load_persona_cache, save_cache as save_persona_cache
            personas = load_personas(PERSONAS_DIR)
            if personas:
                stories_path = DATA / "stories-merged.json"
                if stories_path.exists():
                    stories_data = json.loads(stories_path.read_text(encoding="utf-8"))
                    stories = stories_data.get("stories", [])
                    persona_cache = load_persona_cache(DATA / "persona-cache.json")
                    api_key = ds_key or glm_key
                    base_url = "https://api.deepseek.com" if ds_key else "https://open.bigmodel.cn/api/paas/v4"
                    model = "deepseek-chat" if ds_key else "glm-4-flash"

                    scored = 0
                    for story in stories[:20]:  # Top 20 stories
                        for persona in personas[:1]:  # Default persona only
                            result = score_with_persona(
                                persona, story, persona_cache,
                                api_key=api_key, base_url=base_url, model=model,
                                timeout=30, stats={"cached": 0, "scored": 0, "failed": 0},
                            )
                            if result:
                                score, review = result
                                story.setdefault("persona_scores", {})[persona["id"]] = {
                                    "score": score, "review": review
                                }
                                scored += 1
                    save_persona_cache(DATA / "persona-cache.json", persona_cache, datetime.now(timezone.utc))
                    stories_path.write_text(json.dumps(stories_data, ensure_ascii=False, indent=2), encoding="utf-8")
                    print(f"[enrich] Persona 锐评: {scored} 条故事已评分 ({len(personas)} personas)")
        except ImportError:
            print("[enrich] ⚠️ persona_score.py 未找到，跳过 persona 锐评")
        except Exception as e:
            print(f"[enrich] ⚠️ Persona 锐评失败: {e}")
    elif not skip_persona:
        print("[enrich] ⏭️ Persona 锐评跳过（未配置 DEEPSEEK_API_KEY 或 GLM_API_KEY）")

    # Save translation cache
    save_cache()
    print(f"[enrich] 翻译缓存已保存 ({len(_cache)} 条)")
    print(f"[enrich] 完成 ✅")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
