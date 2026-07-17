# 115 批量为文件去前缀工作流

> 适用场景：对 115 网盘某个目录下的批量文件统一去掉文件名开头的特定前缀。

## 核心原则

1. **目录树优先**：文件/文件夹多或层级深时，先用 `export_tree` 获取目录树，本地解析后再按需列目录，避免全量递归（0.5 QPS 下效率差异巨大）。
2. **无需去重**：同名文件 115 会自动在目标名追加 `(1)` 后缀，不会丢失数据。
3. **先核对再执行**：必须比对本地候选数与实际 API 匹配数，不一致则停止。

## 标准流程

### 第 1 步：获取目录树

```python
from agent_115.api import directory as dir_api

cid = file_api.resolve_path_to_cid(client, '目标目录')
result = dir_api.export_tree(client, cid, layer_limit=25)
Path('/tmp/tree.txt').write_text(result['content'], encoding='utf-8')
```

目录树文本包含完整层级关系，用于本地解析候选。

### 第 2 步：本地解析候选

解析目录树，统计目标前缀的文件数：

```python
lines = Path('/tmp/tree.txt').read_text(errors='replace').splitlines()
candidates = []

for line in lines:
    pos = line.rfind('|-')
    if pos < 0:
        continue
    name = line[pos+2:].strip()
    depth = line[:pos].count('|')
    # depth: 0=云下载, 1=A, 2+=子目录或文件
    if depth >= 2 and name.lower().startswith('前缀') and name.lower().endswith(视频扩展名):
        # 提取父目录路径（相对于 A）
        parent = ...
        candidates.append((parent, name))
```

### 第 3 步：SKD 获取文件 ID（只列有候选的目录）

```python
# 先列 A 一次，建立子目录名→CID 映射
entries = file_api.list_files(client, root_cid, limit=1000)
dir_map = {e.name: e.id for e in entries if e.is_dir}

# 只处理树中匹配的父目录
for parent_dir in sorted(parent_dirs):
    cid = dir_map[parent_dir]      # 直接子目录查表
    entries = file_api.list_files(client, cid, limit=1000)
    for e in entries:
        if e.name.lower().startswith('前缀') and ...:
            remote[e.name] = e.id   # 记录 file_id
```

### 第 4 步：数量核对

```python
assert len(local_candidates) == len(remote)
# 不一致 → 停止，不执行重命名
```

### 第 5 步：批量重命名（不分批去重）

```python
renames = {fid: name去除前缀 for fid, name in remote.items()}
# 115 自动处理同名冲突 → 加 (1) 后缀
result = file_api.batch_rename(client, renames=renames)
```

### 第 6 步：补漏扫尾

检查是否还有剩余带前缀的文件（包括变体前缀，如单/double W）：

```python
# 重新列出有候选的目录，查找仍带前缀的文件
for parent_dir in sorted(parent_dirs):
    ...
    for e in entries:
        for prefix in [PREFIX, PREFIX_VARIANT]:
            if e.name.lower().startswith(prefix):
                # 补重命名
```

## 前缀变体处理

实际碰到的前缀变体：

| 前缀 | 示例 |
|------|------|
| `www.98t.la@` | `www.98t.la@横版10.mp4` |
| `WWW.98T.LA@` | `WWW.98T.LA@甜糖.mp4`（大小写不敏感） |
| `WW.98T.LA@` | `WW.98T.LA@小玉5(1).mp4`（双 W） |

匹配时一律 `.lower()` 后对比。

## 停止条件

遇到以下任一情况立即停止，不执行重命名：
- 本地候选数 ≠ 远端实际匹配数
- HTTP 401 / 403 / 405
- 115 流控
- 网络请求超时或异常
- 无法确认 QPS 间隔

## 限流要求

- QPS 固定 **0.5**
- 相邻请求间隔至少 **2 秒**
- 全程**串行**执行，禁止并发
- 所有请求经过 `GlobalRateLimiter`（跨进程文件锁 + 线程锁）

## 完整参考脚本

如需完整可运行示例，参考项目仓库中的：
- `/tmp/115-rename-exec.py` — 首次批处理（验证 + 重命名）
- `/tmp/115-rename-sweep.py` — 扫尾补漏（变体前缀）
