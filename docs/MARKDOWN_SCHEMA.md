# Markdown Report Schema

## Schema v1

```markdown
---
schema_version: 1
analysis_id: ""
source_url: ""
platform: ""
video_id: ""
title: ""
author: ""
published_at: null
analyzed_at: ""
summary_mode: ""
language: ""
tags: []
---

# 标题
## 核心摘要
## 核心收获
## 时间轴大纲
## 内容分析
## 评论风向
## 视频信息
## 原始链接
## 完整字幕（可选）
```

## 不变量

- UTF-8、标准 Markdown、YAML Frontmatter；不依赖 Obsidian 专属语法。
- 图片路径相对报告文件；报告可以连同资源目录整体迁移。
- Windows 文件名移除 `< > : \ / | ? *`、控制字符、尾随点/空格及保留名；空结果回退到 `analysis-{id}`。
- 文件名模板可配置，但渲染后仍需路径规范化并限制在批准目录内。
- 默认不覆盖用户修改：保存生成内容哈希；哈希不匹配时生成冲突副本并提示。
- 报告采用同目录临时文件 + 校验 + 原子替换，避免断电产生半文件。
- 完整字幕可选；API 默认不返回超长字幕。

## 索引重建

扫描批准目录中的 `.md`，解析已知 `schema_version` 和必填识别字段。合法报告恢复 Job/报告元数据与 FTS 文本；损坏或未知版本进入隔离诊断列表，不删除文件。重复 `analysis_id` 通过路径、时间和内容哈希显式报告冲突。

## 版本演进

- 新字段优先可选，旧读取器忽略未知字段。
- 破坏性变化提升 `schema_version` 并提供迁移器、备份与回滚。
- 迁移不得静默覆盖用户编辑；先生成新文件或经确认后原子替换。

