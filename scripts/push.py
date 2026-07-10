"""AI Radar 推送模块 - 飞书 + Server酱 双通道"""
import os
import json
import urllib.parse
import urllib.request
from typing import Optional, Any

FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")
SERVERCHAN_KEY = os.environ.get("SERVERCHAN_KEY", "")

# ---------- 辅助函数：中文摘要 + 爆点说明 ----------

BRIEF_TEMPLATES = {
    "AI Coding": "AI 编程工具/平台更新",
    "Agent": "AI Agent / 智能体相关",
    "开源": "开源模型/本地部署",
    "产品": "新产品/模型发布",
    "商业": "融资/商业化/收购",
    "多模态": "多模态生成/视觉/Audio",
}


def _generate_breakout_text(item: dict) -> list[str]:
    """生成爆款分析文本行"""
    lines = []

    # 1. 中文一句话摘要（从 reasons / importance_label / category 推断）
    reasons = item.get("reasons") or []
    importance_label = item.get("importance_label") or ""
    cat = item.get("category") or "其他"
    source_names = item.get("source_names") or []

    summary_parts = []
    if importance_label:
        summary_parts.append(importance_label)
    elif reasons:
        label_map = {
            "official_source": "官方发布",
            "multi_source": "多源共振",
            "high_ai_relevance": "高 AI 相关性",
            "high_importance": "高重要性",
        }
        parts = [label_map.get(r, r) for r in reasons if r in label_map]
        if parts:
            summary_parts.append(" / ".join(parts))

    if source_names:
        src_str = "、".join(source_names[:3])
        if len(source_names) > 3:
            src_str += f"等 {len(source_names)} 个源"
        summary_parts.append(f"覆盖 {src_str}")

    if summary_parts:
        lines.append(f"  📋 简介：{item.get('title','?')[:80]}")
        lines.append(f"  {' | '.join(summary_parts)}")
    else:
        lines.append(f"  📋 {item.get('title','?')[:100]}")

    # 2. 爆款点分析
    score = item.get("score", 0)
    source_count = item.get("source_count", 1)
    ib = item.get("importance_breakdown") or {}

    breakout_points = []

    # 多源共振
    if source_count >= 3:
        breakout_points.append(f"🔥 多源共振：{source_count} 个站源同时报道")
    elif source_count >= 2:
        breakout_points.append(f"🔥 双源交叉验证")

    # 官方源
    if "official_source" in reasons:
        breakout_points.append("🏢 官方一手消息")

    # 高评分
    if score >= 90:
        breakout_points.append(f"⭐ 综合评分 {score}（极高）")
    elif score >= 85:
        breakout_points.append(f"⭐ 综合评分 {score}（高位）")

    # importance breakdown 拆解
    if ib:
        high_dims = []
        for dim_key, dim_label in [
            ("editorial", "编辑权重"),
            ("source_tier", "源等级"),
            ("ai_relevance", "AI 关联度"),
            ("recency", "时效性"),
            ("story_heat", "话题热度"),
        ]:
            val = ib.get(dim_key, 0)
            if isinstance(val, (int, float)) and val >= 0.85:
                high_dims.append(f"{dim_label} {val:.0%}")
        if high_dims:
            breakout_points.append(f"📊 高分维度：{' | '.join(high_dims)}")

    if not breakout_points:
        breakout_points.append(f"📊 综合评分 {score}")

    lines.append(f"  💥 爆点：{'；'.join(breakout_points)}")

    # 3. 选题角度
    cat_angle_map = {
        "AI Coding": "适合写 AI 编程工具评测、开发者效率提升",
        "Agent": "适合写 Agent 应用场景、Workflow 实战",
        "开源": "适合写开源模型对比、本地部署教程",
        "产品": "适合写新品解读、产品体验",
        "商业": "适合写行业趋势、投融资分析",
    }
    angle = cat_angle_map.get(cat, f"适合写{cat}相关分析")
    lines.append(f"  💡 选题角度：{angle}")

    return lines


def _format_item_for_push(item: dict) -> list[str]:
    """格式化单条信号为推送行（供状态卡和爆款卡复用）"""
    lines = []
    breakout = _generate_breakout_text(item)
    lines.extend(breakout)
    url = item.get("url", "")
    if url:
        lines.append(f"  🔗 [原文链接]({url})")
    return lines


def _feishu_text(text: str) -> bool:
    """通过飞书机器人推送文本消息"""
    if not FEISHU_WEBHOOK:
        print("[push] 跳过飞书：无 webhook")
        return False
    payload = {"msg_type": "text", "content": {"text": text[:2000]}}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(FEISHU_WEBHOOK, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            ok = result.get("StatusCode") == 0 or result.get("code") == 0
            print(f"[push] 飞书推送 {'成功' if ok else '失败'}: {result}")
            return ok
    except Exception as e:
        print(f"[push] 飞书异常: {e}")
        return False


def _feishu_card(title: str, body_lines: list[str]) -> bool:
    """通过飞书机器人推送卡片消息"""
    if not FEISHU_WEBHOOK:
        print("[push] 跳过飞书卡片：无 webhook")
        return False

    content = "\n".join(body_lines)
    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title[:150]},
                "template": "blue"
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": content[:3000]
                }
            ]
        }
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(FEISHU_WEBHOOK, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            ok = result.get("StatusCode") == 0 or result.get("code") == 0
            print(f"[push] 飞书卡片 {'成功' if ok else '失败'}")
            return ok
    except Exception as e:
        print(f"[push] 飞书卡片异常: {e}")
        return False


def _serverchan(title: str, desp: str = "") -> bool:
    """通过 Server酱 推送"""
    if not SERVERCHAN_KEY:
        print("[push] 跳过 Server酱：无 sendkey")
        return False
    url = f"https://sctapi.ftqq.com/{SERVERCHAN_KEY}.send"
    data = urllib.parse.urlencode({"title": title[:150], "desp": desp[:30000]}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            ok = result.get("code") == 0
            print(f"[push] Server酱 {'成功' if ok else '失败'}: {result.get('message', '')}")
            return ok
    except Exception as e:
        print(f"[push] Server酱异常: {e}")
        return False


def notify_status_card(
    run_time: str,
    item_count: int,
    s_items: list[dict],
    a_items: list[dict],
    new_items_from_whitelist: list[dict] = None,
    failed_sources: list[str] = None
):
    """推送每小时状态卡片到飞书 + 简讯到 Server酱"""
    now = run_time

    # --- 飞书卡片 ---
    body_lines = [f"📡 **AI Radar 状态卡** | {now}"]
    body_lines.append(f"📊 共捕获 **{item_count}** 条信号")

    if s_items:
        body_lines.append(f"\n🔥 **S 级爆款**（{len(s_items)} 条）")
        for item in s_items[:3]:
            body_lines.append(f"\n• **{item.get('title','?')}** — 综合分 {item.get('score','?')} | {item.get('source','?')}")
            breakout = _generate_breakout_text(item)
            body_lines.extend(breakout)
            url = item.get("url", "")
            if url:
                body_lines.append(f"  🔗 [原文链接]({url})")

    if a_items and not s_items:
        body_lines.append(f"\n📈 **A 级信号**（{len(a_items)} 条）")
        for item in a_items[:5]:
            body_lines.append(f"\n• **{item.get('title','?')}** — 综合分 {item.get('score','?')} | {item.get('source','?')}")
            body_lines.append(f"  📋 {item.get('title','?')[:80]}")
            url = item.get("url", "")
            if url:
                body_lines.append(f"  🔗 [原文链接]({url})")

    if failed_sources:
        body_lines.append(f"\n⚠️ **信源异常**（{len(failed_sources)} 个）")
        for s in failed_sources[:5]:
            body_lines.append(f"• {s}")

    body_lines.append(f"\n🔗 [查看完整数据](https://github.com/mandydudubye-design/AI-ReaderDaily)")
    _feishu_card("📡 AI Radar 状态卡", body_lines)

    # --- Server酱简讯 ---
    summary_parts = [f"📡 AI Radar | {now} | {item_count} 条"]
    if s_items:
        summary_parts.append(f"🔥 {len(s_items)} 个爆款")
    if failed_sources:
        summary_parts.append(f"⚠️ {len(failed_sources)} 个异常源")
    desp_lines = [s.replace("\n", "  ") for s in body_lines]
    _serverchan(summary_parts[0], "\n".join(desp_lines))


def notify_breaking(item: dict):
    """推送爆款紧急通知（仅 S 级 + 白名单更新）"""
    title = f"🔥 AI 爆款: {item.get('title','?')[:60]}"
    body = [
        f"**{item.get('title','?')}**",
        f"来源: {item.get('source','?')} | 综合分: {item.get('score','?')}",
    ]
    breakout = _generate_breakout_text(item)
    body.extend(breakout)
    url = item.get("url", "")
    if url:
        body.append(f"\n🔗 [原文链接]({url})")

    _feishu_card(title[:150], body)
    _serverchan(title, "\n".join(body))
