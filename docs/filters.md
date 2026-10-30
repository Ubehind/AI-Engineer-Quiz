# 组合筛选

## 页面使用

- 顶部输入关键词；纯空白表示不限制关键词，沿用现有 FTS5 字段、前缀搜索及 `porter unicode61` 分词器。
- 在 Library 中选择一个集合和素材类型，或选择 All 取消对应限制。
- Include tags 每次添加一个完整标签，多个标签必须全部满足。
- Exclude tags 每次添加一个完整标签，命中任意一个即排除。
- 标签可以从建议中选择，也可以输入任意完整名称。空格、逗号等字符属于标签名称，不作为分隔符。
- 名称去首尾空白并转为小写；重复条件不改变结果。包含与排除同一标签时没有匹配结果。
- 生效条件显示在筛选面板底部；点击条件移除，再添加即可修改。Clear filters 清除关键词、集合、类型和所有标签条件。
- 仅返回 active 素材。侧栏数字表示全部 active 素材中的统计，Matches 才是当前匹配总数。

语义搜索入口已隐藏。本次完成组合筛选；分页和 CSV 导出留待后续步骤。

## 接口约定

登录后调用 `GET /api/assets`。数组使用重复的查询参数，不使用逗号分隔：

```text
/api/assets?q=harbor&collection_id=1&media_type=image&include_tags=approved&include_tags=campaign&exclude_tags=internal
```

`include_tags` 取交集，`exclude_tags` 命中任意标签即排除，其他条件之间均取交集。
保留旧的单标签 `tag` 参数，视为额外的包含标签。标签按完整名称相等匹配，不使用 LIKE。
参数需由 URLSearchParams 或 HTTP 客户端编码，避免 `&`、`+`、引号及中文被错误解释。
不存在的包含标签或集合返回空结果；不存在的排除标签不影响结果。
非空但不含可搜索字符的关键词（如 `"%_*()`）返回空结果。
`collection_id` 必须是正整数；`status` 仅允许 `active`，其他值返回 422。

后端 `_filter_sql()` 集中构造查询条件，前端 `assetFilterParams()` 集中序列化筛选与排序条件，包含 `media_type`。
后续导出应复用这两个入口，并独立处理列表的分页参数。

## 验证

```powershell
$env:KEEPSTACK_AI_ENABLED = "false"
$env:KEEPSTACK_OCR_ENABLED = "false"
uv run --no-sync python -m pytest -q
```

`tests/test_filters.py` 每项测试在临时目录生成独立数据库，包含 10 条素材、2 个集合，以及包含、排除、完整名称和特殊字符标签。
覆盖条件交集、active 限制、重复与冲突、未知条件、中文与特殊字符、原有 FTS 字段及词干/前缀行为、鉴权和重复查询参数。
无须修改本地演示数据库或调用外部服务。

使用现有 seed 数据进行页面验证：搜索 `coastal`，选择 Environment & Survey、image，包含 `coast` 和 `survey`，排除 `internal`，应得到 2 条结果。
重复添加 `COAST` 不增加条件；再排除 `coast` 应为 0 条；移除该排除条件恢复 2 条；清除全部条件恢复 24 条。
还已在浏览器中模拟延迟的旧请求及请求失败，确认最新条件生效，失败提示和 Retry search 可以恢复列表。
