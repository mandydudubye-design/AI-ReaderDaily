# AI-ReaderDaily · AI 热点雷达

一个面向中文创作者的 AI 热点聚合工具：持续抓取公开 AI 信源，生成可浏览的热点网站和可复用的信息卡片，并可把高优先级信号推送到飞书和微信（Server酱）。

本仓库只负责“发现、筛选、结构化”。公众号文章、配图等内容创作应作为下游流程，消费本仓库输出的 JSON 数据。

## 功能

- 聚合 LearnPrompt `ai-news-radar`、AI 公司官方 RSS 和中文科技信息源
- 对通用信源先做 AI 相关性过滤，再进行分类、评分和 S/A/B/C 分级
- 合并相似事件为故事线，记录信源健康和 48 小时趋势
- 发布 GitHub Pages 看板，并生成稳定的热点信息卡片
- 飞书卡片与 Server酱微信提醒（可选）

## 输出

| 文件 | 用途 |
| --- | --- |
| `data/latest-snapshot.json` | 网站使用的完整本次抓取结果 |
| `data/daily-brief.json` | 下游创作流程使用的标准热点卡片 |
| `data/latest-24h.json` | S/A/B 级热点卡片 |
| `data/stories-merged.json` | 合并后的故事线 |
| `data/source-status.json` | 信源可用性和抓取统计 |
| `data/trend.json` | 最近 48 次运行的趋势数据 |

热点卡片字段见 [`DATA_CONTRACT.md`](DATA_CONTRACT.md)。

## 项目结构

```text
config/                 信源和评分规则
scripts/radar.py        拉源、过滤、评分、故事线与 JSON 输出
scripts/push.py         飞书与 Server酱通知
data/                   自动生成的公开数据
index.html              GitHub Pages 看板
.github/workflows/      每小时更新与部署
```

## 在 Cursor 本地运行

1. 使用 Python 3.11 或更高版本。
2. 可选：复制 `.env.example` 为 `.env`，填入通知密钥。
3. 在 Cursor 终端运行：

```bash
python3 scripts/radar.py
```

没有配置 `FEISHU_WEBHOOK` 和 `SERVERCHAN_KEY` 时，脚本会跳过通知，仍会生成本地 JSON 数据。

## GitHub Actions 配置

工作流每小时执行一次，并将新数据提交到仓库、部署到 GitHub Pages。若需要通知，在 GitHub 仓库 Secrets 中配置：

| Secret | 说明 |
| --- | --- |
| `FEISHU_WEBHOOK` | 飞书机器人 Webhook URL（可选） |
| `SERVERCHAN_KEY` | Server酱 SendKey（可选） |

请勿将真实 Token、Webhook 或 SendKey 写入 `.env.example`、提交记录或 Git remote URL。

## 数据来源与致谢

本项目消费 [LearnPrompt/ai-news-radar](https://github.com/LearnPrompt/ai-news-radar) 的公开聚合数据，并补充若干公开 RSS。各条热点均保留原文链接和来源；请以原始发布为准。
