# Maren AI Radar · Cloud Edition

GitHub Actions 每小时跑一次的 AI 信号检测雷达。下班关机也能盯盘，爆款直接推飞书 + Server酱。

## 架构

```
GitHub Actions (cron hourly)
  ↓
scripts/radar.py        # 拉源 → 检测爆款 → 评分
  ↓
scripts/notifier.py     # 飞书卡片 + Server酱 双通道推送
  ↓
git commit & push       # 数据回写仓库 data/ 目录
```

## 推送通道

- 飞书机器人（主通道，卡片消息）
- Server酱（兜底通道，微信服务号）

## 配置

需要配置的 GitHub Secrets：

| Name | 说明 |
|---|---|
| `FEISHU_WEBHOOK` | 飞书机器人 webhook URL |
| `SC_SENDKEY` | Server酱 sendkey |
| `GLM_API_KEY` | GLM API key（用于生成选题大纲，可选） |

## 本地测试

```bash
python3 scripts/radar.py --local
python3 scripts/notifier.py --local
```
