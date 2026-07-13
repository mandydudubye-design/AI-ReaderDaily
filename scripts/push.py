"""AI Radar 推送模块 - 飞书 + Server酱 双通道"""
import os
import json
import re
import urllib.parse
import urllib.request
from typing import Optional, Any

FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")
SERVERCHAN_KEY = os.environ.get("SERVERCHAN_KEY", "")

# ---------- 中文翻译：基于关键词词典 + Google Translate 免费 API ----------
_EN_ZH_DICT = {
    # 产品名
    "Gemini": "Gemini", "Claude": "Claude", "GPT": "GPT", "ChatGPT": "ChatGPT",
    "Llama": "Llama", "Mistral": "Mistral", "DeepSeek": "DeepSeek",
    "Anthropic": "Anthropic", "OpenAI": "OpenAI", "Google": "Google",
    "Meta": "Meta", "Hugging Face": "Hugging Face", "HuggingFace": "HuggingFace",
    "NVIDIA": "英伟达", "xAI": "xAI", "DeepMind": "DeepMind",
    # 常见 AI 概念
    "Agent": "智能体", "Agents": "智能体", "agent": "智能体", "agents": "智能体",
    "MCP": "模型上下文协议",
    "benchmark": "基准测试", "Benchmark": "基准测试", "Benchmarking": "基准测试",
    "release": "发布", "Release": "发布", "Launching": "发布",
    "update": "更新", "Update": "更新",
    "open source": "开源", "Open Source": "开源",
    "API": "API", "model": "模型", "Model": "模型",
    "training": "训练", "Training": "训练",
    "inference": "推理", "Inference": "推理",
    "multimodal": "多模态", "Multimodal": "多模态",
    "vision": "视觉", "Vision": "视觉",
    "code": "代码", "Code": "代码",
    "tool use": "工具调用", "function calling": "函数调用",
    "embedding": "嵌入向量", "fine-tuning": "微调",
    "RAG": "检索增强生成",
    "safety": "安全", "alignment": "对齐",
    "reasoning": "推理能力", "Reasoning": "推理能力",
    "enterprise": "企业", "Enterprise": "企业",
    "startup": "创业公司", "funding": "融资",
    "open-weight": "开源权重",
    "background": "后台", "tasks": "任务",
    "remote": "远程", "managed": "托管",
}


def _translate_title(title: str) -> str:
    """英文标题 → 中文翻译（词典优先，降级用 Google Translate 免费 API）"""
    if not title:
        return ""
    # 快速判断：如果中文字符占比 > 30%，认为已经是中文
    cn_chars = len(re.findall(r'[\u4e00-\u9fff]', title))
    if cn_chars > len(title) * 0.3:
        return title

    # 词典替换（保留无法翻译的部分）
    zh = title
    # 按长度降序替换避免短词覆盖长词
    for en, cn in sorted(_EN_ZH_DICT.items(), key=lambda x: -len(x[0])):
        zh = zh.replace(en, cn)

    # 如果替换后变化不大（<20%），尝试 Google Translate 免费 API
    if zh == title:
        try:
            encoded = urllib.parse.quote(title)
            api_url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=zh-CN&dt=t&q={encoded}"
            cmd = ["curl", "-sS", "--noproxy", "*", "-L", "--connect-timeout", "5", "--max-time", "8", api_url]
            import subprocess
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
            if result.returncode == 0 and result.stdout:
                data = json.loads(result.stdout)
                # 结构: [[["翻译结果","原文",...], ...], ...]
                parts = []
                for sentence_group in data[0]:
                    if sentence_group and sentence_group[0]:
                        parts.append(sentence_group[0])
                if parts:
                    return "".join(parts)
        except Exception:
            pass

    return zh

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

    # 中文标题
    title_en = item.get('title', '?')
    title_zh = _translate_title(title_en)
    if summary_parts:
        lines.append(f"  📋 {title_zh[:80]}")
        lines.append(f"  {' | '.join(summary_parts)}")
    else:
        lines.append(f"  📋 {title_zh[:100]}")

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


def _feishu_card(title: str, body_lines: list[str], buttons: list[dict] = None) -> bool:
    """通过飞书机器人推送卡片消息（支持 markdown + 可点击按钮）"""
    if not FEISHU_WEBHOOK:
        print("[push] 跳过飞书卡片：无 webhook")
        return False

    content = "\n".join(body_lines)
    elements = [
        {"tag": "markdown", "content": content[:3000]}
    ]

    # 添加可点击按钮（每个按钮一个链接）
    if buttons:
        btn_actions = []
        for btn in buttons[:5]:  # 飞书卡片最多 5 个按钮
            btn_actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": btn.get("text", "打开链接")[:20]},
                "type": "primary",
                "url": btn.get("url", "")
            })
        if btn_actions:
            elements.append({"tag": "action", "actions": btn_actions})

    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title[:150]},
                "template": "blue"
            },
            "elements": elements
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

    # --- 收集所有要显示的链接，做成飞书按钮 ---
    all_buttons = []
    if s_items:
        for item in s_items[:3]:
            url = item.get("url", "")
            if url:
                title_short = _translate_title(item.get('title', ''))[:15] or item.get('title', '')[:15]
                all_buttons.append({"text": f"🔗 {title_short}", "url": url})

    # --- 飞书卡片 ---
    body_lines = [f"📡 **AI Radar 状态卡** | {now}"]
    body_lines.append(f"📊 共捕获 **{item_count}** 条信号")

    if s_items:
        body_lines.append(f"\n🔥 **S 级爆款**（{len(s_items)} 条）")
        for item in s_items[:3]:
            title_zh = _translate_title(item.get('title', '?'))
            body_lines.append(f"\n• **{title_zh[:60]}** — 综合分 {item.get('score','?')}")
            breakout = _generate_breakout_text(item)
            body_lines.extend(breakout)
            url = item.get("url", "")
            if url:
                body_lines.append(f"  🔗 {url}")  # 裸链接（飞书支持点按）

    if a_items and not s_items:
        body_lines.append(f"\n📈 **A 级信号**（{len(a_items)} 条）")
        for item in a_items[:5]:
            title_zh = _translate_title(item.get('title', '?'))
            body_lines.append(f"\n• **{title_zh[:60]}** — 综合分 {item.get('score','?')}")
            url = item.get("url", "")
            if url:
                body_lines.append(f"  🔗 {url}")

    if failed_sources:
        body_lines.append(f"\n⚠️ **信源异常**（{len(failed_sources)} 个）")
        for s in failed_sources[:5]:
            body_lines.append(f"• {s}")

    # 雷达网页链接
    radar_url = "https://mandydudubye-design.github.io/AI-ReaderDaily/"
    body_lines.append(f"\n🔗 [查看全部雷达信号]({radar_url})")
    _feishu_card("📡 AI Radar 状态卡", body_lines,
                 buttons=all_buttons[:4] + [{"text": "📡 查看全部", "url": radar_url}])

    # --- Server酱简讯 ---
    summary_parts = [f"📡 AI Radar | {now} | {item_count} 条"]
    if s_items:
        summary_parts.append(f"🔥 {len(s_items)} 个爆款")
    if failed_sources:
        summary_parts.append(f"⚠️ {len(failed_sources)} 个异常源")
    summary_parts.append(f"🔗 {radar_url}")
    desp_lines = [s.replace("\n", "  ") for s in body_lines]
    desp_lines.append(f"\n——\n📡 查看全部雷达信号：{radar_url}")
    _serverchan(summary_parts[0], "\n".join(desp_lines))


def notify_breaking(item: dict):
    """推送爆款紧急通知（仅 S 级 + 白名单更新）"""
    title_zh = _translate_title(item.get('title', '?'))
    title = f"🔥 AI 爆款: {title_zh[:60]}"
    body = [
        f"**{title_zh}**",
        f"来源: {item.get('source','?')} | 综合分: {item.get('score','?')}",
    ]
    breakout = _generate_breakout_text(item)
    body.extend(breakout)
    url = item.get("url", "")
    if url:
        body.append(f"\n🔗 {url}")

    buttons = []
    if url:
        buttons.append({"text": "打开原文", "url": url})
    _feishu_card(title[:150], body, buttons=buttons)
    _serverchan(title, "\n".join(body))
