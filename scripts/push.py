"""AI Radar 推送模块 - 飞书 + Server酱 双通道"""
import os
import json
import urllib.parse
import urllib.request
from typing import Optional

FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")
SERVERCHAN_KEY = os.environ.get("SERVERCHAN_KEY", "")


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

    if new_items_from_whitelist:
        body_lines.append(f"\n📌 **白名单更新**（{len(new_items_from_whitelist)} 条）")
        for item in new_items_from_whitelist[:5]:
            body_lines.append(f"• **{item.get('title','?')}**")
            if item.get("url"):
                body_lines.append(f"  [查看原文]({item['url']})")

    if s_items:
        body_lines.append(f"\n🔥 **S 级爆款**（{len(s_items)} 条）")
        for item in s_items[:3]:
            body_lines.append(f"• **{item.get('title','?')}** — 综合分 {item.get('score','?')}")
            body_lines.append(f"  {item.get('source','?')} | [链接]({item.get('url','#')})")
            if item.get("topic_angles"):
                body_lines.append(f"  💡 选题角度: {item.get('topic_angles')}")

    if a_items and not s_items:
        body_lines.append(f"\n📈 **A 级信号**（{len(a_items)} 条）")
        for item in a_items[:5]:
            body_lines.append(f"• **{item.get('title','?')}** — 分 {item.get('score','?')} | {item.get('source','?')}")
            body_lines.append(f"  [链接]({item.get('url','#')})")

    if failed_sources:
        body_lines.append(f"\n⚠️ **信源异常**（{len(failed_sources)} 个）")
        for s in failed_sources[:5]:
            body_lines.append(f"• {s}")

    body_lines.append(f"\n🔗 [查看完整数据](https://github.com/mandydudubye-design/maren-ai-radar)")
    _feishu_card("AI Radar 状态卡", body_lines)

    # --- Server酱简讯 ---
    summary_parts = [f"📡 AI Radar | {now} | {item_count} 条"]
    if s_items:
        summary_parts.append(f"🔥 {len(s_items)} 个爆款")
    if new_items_from_whitelist:
        summary_parts.append(f"📌 {len(new_items_from_whitelist)} 个白名单更新")
    desp_lines = [s.replace("\n", "  ") for s in body_lines]
    _serverchan(summary_parts[0], "\n".join(desp_lines))


def notify_breaking(item: dict):
    """推送爆款紧急通知（仅 S 级 + 白名单更新）"""
    title = f"🔥 AI 爆款: {item.get('title','?')[:60]}"
    body = [
        f"**{item.get('title','?')}**",
        f"来源: {item.get('source','?')} | 综合分: {item.get('score','?')}",
        f"原文: {item.get('url','#')}",
    ]
    if item.get("topic_angles"):
        body.append(f"\n💡 选题角度:\n{item.get('topic_angles')}")

    _feishu_card(title[:150], body)
    _serverchan(title, "\n".join(body))
