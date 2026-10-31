# 素材检索与结果导出交付说明

## 仓库与提交

- Fork：[Ubehind/AI-Engineer-Quiz](https://github.com/Ubehind/AI-Engineer-Quiz)
- 分支：`feat/asset-search-export`
- 起点：`3aa137c`，保留上游历史，按开发过程分次提交，未 squash、amend 或 rebase。
- 最终提交 SHA：在本次交付所在分支执行 `git rev-parse HEAD`；交付消息也提供该完整 SHA。
- 目前只提交到本地。交给 reviewer 前，还需要推送该分支，并确认仓库访问权限：

```powershell
git push -u origin feat/asset-search-export
```

## 本地运行

需要 Python 3.10+，推荐 3.12。无需前端构建、外部数据库、模型或 API 密钥。
在仓库根目录用 PowerShell 执行：

```powershell
uv venv --python 3.12
uv pip install -e ".[dev]"

$env:KEEPSTACK_AI_ENABLED = "false"
$env:KEEPSTACK_OCR_ENABLED = "false"
$env:KEEPSTACK_HOST = "127.0.0.1"

# 首次生成原应用演示数据：24 条素材、3 个集合
uv run --no-sync python -m keepstack seed

# 可选：生成本题验收数据，重复执行不会增加重复素材或集合
uv run --no-sync python -m keepstack seed-search

uv run --no-sync python -m keepstack
```

打开 [http://localhost:8000](http://localhost:8000)，默认账号 `admin` / `admin`。
端口被占用时可设置 `$env:KEEPSTACK_PORT = "8002"` 后启动。
后续启动不必重新 seed。运行文件在 `data/`，已被 Git 忽略。

Linux/macOS 的环境变量写法如下，其他安装、seed 和运行命令相同：

```bash
export KEEPSTACK_AI_ENABLED=false
export KEEPSTACK_OCR_ENABLED=false
export KEEPSTACK_HOST=127.0.0.1
```

## 可复现验收

`seed-search` 新增 38 条素材和 2 个集合，包含同一创建时间、非 active、缺标签、内部标签、其他集合及文档等对照数据。
重复运行只会重置这些固定 UUID 的验收素材，也会覆盖对它们的手动修改。

在 Library 页面执行：

1. 关键词输入 `harbor`，集合选择 **Search Acceptance**，素材类型选择 **image**。
2. Include tags 分别添加 `approved`、`campaign`，Exclude tags 添加 `internal`。
3. 选择 Oldest：应匹配 **31 条**，三页分别为 **12 / 12 / 7** 条。中间 29 条创建时间相同，以素材 ID 确定稳定次序。
4. 从第三页点击 **Export all matches (CSV)**：应导出全部 **31 条**，顺序等于三页列表拼接。
5. 改为 Newest：返回第一页，顺序与 Oldest 相反。
6. 类型改为 document：匹配并导出 **1 条**；类型改为 All media types：匹配 **32 条**。
7. 再排除 `approved`：匹配 0 条，CSV 仍有表头。移除此条件后恢复结果。
8. 打开素材详情，修改标题、描述或增删标签；下一次检索和导出反映修改。完整标签可以包含空格、逗号、引号、中文及 `/`。

当前条件会显示为按钮，点击即可移除。清除筛选时，关键词、集合、类型和标签会一起清除，排序保持不变。
侧栏统计明确标为全部 active 素材的数量，Matches 是当前匹配总数。
首次验收素材包含中文、逗号、双引号和换行，用于检查 CSV。

## 自动化测试与实测结果

```powershell
$env:KEEPSTACK_AI_ENABLED = "false"
$env:KEEPSTACK_OCR_ENABLED = "false"
uv run --no-sync python -m pytest -q

# 可选的 JavaScript 语法检查，需要本机已有 Node.js
node --check web/app.js
```

测试数据在临时目录生成，不依赖本地数据库或外部服务。

| 测试文件 | 主要验证 |
| --- | --- |
| `tests/test_filters.py` | 组合交集、标签完整匹配、大小写与重复、包含排除冲突、FTS 字段与分词、特殊字符、鉴权 |
| `tests/test_pagination.py` | 多页拼接、相同创建时间、关键词下的升降序、边界页、非法及超大分页参数 |
| `tests/test_export.py` | 列表与导出的筛选和顺序一致、超过 200 条、中文及 CSV 转义、空结果表头、鉴权 |
| `tests/test_updates.py` | 通过原有编辑接口更新后，新旧关键词、标签条件和 CSV 内容同步变化 |
| `tests/test_seed_search.py` | 验收数据可重复生成，31 条图片跨三页且导出一致，取消类型限制得到 32 条 |

2026-10-30 实测：

- Windows / Python **3.12.13**：**89 passed, 2 skipped**。
- 全新虚拟环境 / Python **3.10.20**：安装 `.[dev]` 后同样 **89 passed, 2 skipped**；原 seed、验收 seed 连续运行两次、应用启动、登录、静态文件、分页和 CSV 均验证通过。
- 跳过项是未安装的可选本地视觉模型及关闭的 OCR。测试输出的 3 条提示来自现有 FastAPI 生命周期和测试客户端的弃用接口。
- 浏览器验证了登录、三页翻页、条件和排序修改后回到第一页、第三页导出全部结果、素材类型对导出的限制、编辑后刷新、中文特殊标签删除，以及窄屏布局。
- 模拟旧响应延迟到新关键词输入后，列表仍对应最新条件；模拟列表及导出失败，错误提示、重试和按钮恢复正常。列表重试保留失败时的页码。
- 已解析浏览器实际下载的 CSV，与各页 UUID 和顺序逐项核对，并检查中文、引号、换行及标签变化。

## 实现选择与限制

- 保留 FastAPI、SQLite/FTS5 和原生 JavaScript，没有新增运行时组件或依赖。
- 列表和导出共用接口参数解析、SQL 筛选构造和排序白名单。包含标签逐一用 EXISTS 检查，排除标签用 NOT EXISTS，避免连接查询产生重复素材。
- 标签转小写、去首尾空白并去重。所有用户值使用 SQL 参数绑定；关键词沿用原 FTS 查询构造和 `porter unicode61` 分词器。纯空白不限制关键词，纯标点返回空结果。
- 创建时间相同时，ID 也按所选的升序或降序排列。分页参数在 API 入口校验；超出末页返回空列表及匹配总数。
- 前端用 AbortController 和请求序号避免旧响应覆盖新条件，关键词防抖期间也立即使旧请求失效。
- CSV 使用带 BOM 的 UTF-8，字段为 `uuid,title,description,filename,media_type,tags,created_at`。**tags 是按名称排序的 JSON 数组字符串**，由标准库 csv 处理外层转义；缺失文本输出为空字段。
- 导出查询和标签每批读取 200 条，最终 CSV 仍在内存中生成，页面通过 Blob 下载。因此，导出的文本越多，服务端和浏览器占用的内存也越多。本题采用同步导出，没有实现异步任务。
- 仅提供关键词搜索入口。分页与导出在数据不变时保持一致，不提供跨请求数据库快照。
- 修复了原请求函数对 JSON 请求未默认使用 POST 的问题，以及带 `/` 的标签无法删除的问题。
- 范围外问题：原素材文件的 Download 链接未携带 Bearer 凭据；本题 CSV 下载已通过带鉴权的请求独立处理。原 FastAPI 启动钩子仍使用待迁移的弃用接口。

本题要求的功能和本地验证均已完成。远程推送和 reviewer 权限确认留到实际交付时处理。本地数据库、上传素材、凭据和虚拟环境均未提交。
