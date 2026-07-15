# 热点信息卡片数据契约

`data/daily-brief.json` 是 AI 热点雷达向下游工作流提供的稳定接口。公众号创作、笔记整理或其他自动化只应依赖此文件，而不应读取抓取脚本的内部字段。

```json
{
  "schema_version": "1.0",
  "generated_at": "2026-07-14T03:00:00Z",
  "items": [
    {
      "id": "16-character-stable-id",
      "title": "热点标题",
      "url": "https://source.example/article",
      "source": "来源名称",
      "published_at": "2026-07-14T02:00:00Z",
      "category": "模型",
      "score": 88,
      "grade": "A",
      "why_it_matters": "评分与信号来源说明",
      "topic_angle": "可供创作流程使用的选题角度",
      "source_names": ["来源名称"]
    }
  ]
}
```

## 字段约定

- `schema_version`：接口版本；字段发生不兼容变更时递增。
- `generated_at` 与 `published_at`：UTC ISO 8601 时间；来源未提供时间时 `published_at` 为 `null`。
- `score`：用于排序的整数评分；仅在同一份数据中比较。
- `grade`：`S`、`A`、`B`、`C`，`S` 为最高优先级。
- `why_it_matters`：可直接显示的简短信号说明，不替代原文核实。
- `topic_angle`：写作切入角度；是结构化提示，不是完整文章大纲。
- `source_names`：参与该信号的来源列表，至少包含 `source`。

`data/latest-24h.json` 使用相同结构，仅保留 S/A/B 级条目。
