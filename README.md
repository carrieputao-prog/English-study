# English Study Automation

每天由 GitHub Actions 按北京时间 04:00 运行：

- 周一、周四：从 `words0621` 选择 10 个词，生成中英穿插荒诞故事和同篇全英文版。
- 周二、周五：针对本轮 10 个词生成第 1 套自测题。
- 周三、周六：生成第 2 套自测题，并校验不与前一天重复。
- 周日：轮空，不生成、不推送。

生成内容保存在 `daily/YYYY/MM/`，运行状态保存在 `data/state.json`。脚本会保证同一故事内的词不重复、相邻故事不重复，并在词表尚未用尽时不复用历史故事词；测试题会在当天、相邻测试日及历史精确题目范围内去重。词表耗尽后自动开启新一轮，但仍排除上一则故事的 10 个词。

## Repository Secrets

- `GEMINI_API_KEY`（也兼容 `GOOGLE_API_KEY`）
- `DINGTALK_WEBHOOK`
- `DINGTALK_SECRET`

Gemini 模型固定为 `gemini-2.5-flash`。工作流需要仓库的 Actions `Workflow permissions` 允许写入内容。

## 本地检查

```bash
python -m unittest discover -s tests -v
python scripts/daily_english.py --date 2026-06-21 --generate-only
```

可在 GitHub Actions 页面手动运行工作流，并通过可选的 `date` 输入指定北京时间日期。
