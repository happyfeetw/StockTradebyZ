# 通达信结果导入 产品化方案

## 场景

```
┌─ macOS ────────────────────────┐      ┌─ Windows ───────────────────────────┐
│                                │      │                                      │
│  Streamlit workbench :8601     │◄─────│  Chrome/Edge 浏览器                   │
│  (HTTPS, 自签名证书)            │      │  https://mac-ip:8601                  │
│                                │      │        │                             │
│  pipeline/tdx_export.py        │      │        │ 用户点击"一键导入"             │
│  生成 .blk 内容                 │      │        ▼                             │
│                                │      │  File System Access API              │
│                                │      │  写入文件到：                          │
│                                │      │  C:\zd_ths\T0002\blocknew\           │
│                                │      │  (用户首次在浏览器中选择过一次的目录)    │
└────────────────────────────────┘      └──────────────────────────────────────┘
```

**一句话**：利用浏览器原生的 File System Access API，用户在页面上选一次通达信目录，之后一键写入。

---

## 1. 为什么不需要辅助服务

浏览器已经提供了 `showDirectoryPicker()` API（Chrome 105+、Edge 105+ 均支持），允许网页：

1. 弹出**操作系统原生目录选择器**，让用户选择通达信 `blocknew` 目录
2. 获取目录句柄，存入浏览器 IndexedDB（持久化存储）
3. 后续用该句柄直接创建/写入文件，**无需再次弹窗选择**

目录句柄跨页面会话保持，用户只需选一次。浏览器会记住授权，后续写入不需要用户确认（和浏览器记住下载目录的机制类似）。

**唯一前提**：页面需要 HTTPS 安全上下文。Streamlit 支持 SSL，自签名证书即可（浏览器会提示不安全，但用户点一次"继续访问"后就能正常使用所有 API）。

---

## 2. 完整用户流程

### 2.1 一次性配置（仅首次）

```
┌─ 结果中心 ────────────────────────────────────────────┐
│                                                        │
│  尚未配置通达信目录                                      │
│  ┌─────────────────────────────────────────────────┐  │
│  │ 💡 一键导入前需要先指定通达信板块目录。              │  │
│  │                                                 │  │
│  │     [📂 选择通达信板块目录]                        │  │
│  │                                                 │  │
│  │  点击后浏览器会弹出目录选择器，请选择：              │  │
│  │     C:\zd_ths\T0002\blocknew\                   │  │
│  │  （您的通达信安装目录 \T0002\blocknew\）           │  │
│  │                                                 │  │
│  │  提示：不是 T0002 本身，是其下的 blocknew 子目录。 │  │
│  └─────────────────────────────────────────────────┘  │
│                                                        │
└────────────────────────────────────────────────────────┘
```

点击按钮 → 浏览器弹出原生目录选择器 → 用户选择 `blocknew` 目录 → 浏览器保存句柄，workbench 显示状态：

```
  通达信目录：C:\zd_ths\T0002\blocknew  ✅ 已验证可写入
  [📂 更换目录]
```

### 2.2 日常导入（每次操作）

弹窗顶部显示选股日期和导入范围切换：

```
┌─ 导入通达信 ──────────────────────────────────────────┐
│                                                        │
│  选股日期：2026-05-29                                   │
│                                                        │
│  导入范围： ⬤ 仅推荐（94只）  ○ 全部候选（324只）        │
│                                                        │
│  将生成以下板块：                                        │
│  ┌────────────┬──────┬──────────────────────────────┐ │
│  │ 板块名称     │ 数量  │ 前5只（按评分降序）              │ │
│  ├────────────┼──────┼──────────────────────────────┤ │
│  │ 0529QB1    │  37  │ 300885(5.0) 300840(5.0)...  │ │
│  │ 0529QBrick │  56  │ 000070(4.5) 000402(4.3)...  │ │
│  │ 0529QB2    │   7  │ 600206(4.2) 002222(4.0)...  │ │
│  └────────────┴──────┴──────────────────────────────┘ │
│                                                        │
│  目标目录：C:\zd_ths\T0002\blocknew                      │
│                                                        │
│                        [取消]  [✅ 一键导入]             │
└────────────────────────────────────────────────────────┘
```

切换到"全部候选"时：

```
  导入范围： ○ 仅推荐   ⬤ 全部候选（324只）

  ┌────────────┬──────┬──────────────────────────────┐ │
  │ 板块名称     │ 数量  │ 排序说明                       │ │
  ├────────────┼──────┼──────────────────────────────┤ │
  │ 0529QB1    │ 125  │ 推荐→已复评→未复评              │ │
  │ 0529QBrick │ 179  │ 推荐→已复评→未复评              │ │
  │ 0529QB2    │  20  │ 推荐→已复评→未复评              │ │
  └────────────┴──────┴──────────────────────────────┘ │
```

板块内排序规则（全部候选模式）：推荐票（按 score 降序）→ 已复评未推荐（按 score 降序）→ 未复评（按代码排序）。保证在通达信翻票时最有价值的票排最前面。

点击"一键导入" → 文件直接写入 → 显示结果：

```
  ✅ 已导入 3 个板块，共 94 只股票
  0529QB1.blk (37只) ✅
  0529QBrick.blk (56只) ✅
  0529QB2.blk (7只) ✅

  📌 打开通达信 → 自选 → 自定义板块 → 查看 0529Q* 板块
```

---

## 3. HTTPS 配置

File System Access API 要求安全上下文。Streamlit 原生支持 SSL。

### 3.1 生成自签名证书（一次性）

```bash
# 在 macOS 项目目录下执行
openssl req -x509 -newkey rsa:2048 \
  -keyout .certs/key.pem \
  -out .certs/cert.pem \
  -days 3650 -nodes \
  -subj "/CN=AgentTrader"
```

### 3.2 修改启动脚本

```bash
# start_workbench 改为 HTTPS 模式
exec .venv/bin/streamlit run workbench/app.py \
  --server.port 8601 \
  --server.sslCertFile .certs/cert.pem \
  --server.sslKeyFile .certs/key.pem \
  ...
```

### 3.3 用户体验

首次访问 `https://mac-ip:8601` 时浏览器提示"您的连接不是私密连接"，用户点击"高级 → 继续访问"即可。浏览器会记住此例外，之后不再提示。

---

## 4. File System Access API 集成方式

### 4.1 架构

workbench 页面中嵌入 `st.components.html` 自定义组件，该组件包含一个独立的小型 JS 应用：

```
Streamlit Python 端                   浏览器 JS 端 (components.html iframe)
─────────────────                    ─────────────────────────────────────
                                      ┌──────────────────────────┐
  生成 .blk 内容                       │  IndexedDB               │
  ↓                                   │  ├─ tdx_dir_handle       │
  通过组件 value 传给 JS               │  └─ ...                  │
                                      └──────────────────────────┘
                                               │
                                      ┌──────────────────────────┐
                                      │  按钮逻辑                  │
                                      │  ├─ "选择通达信目录"       │
                                      │  │   → showDirectoryPicker │
                                      │  ├─ 验证可写入             │
                                      │  └─ "一键导入"             │
                                      │     → 写入 .blk 文件       │
                                      └──────────────────────────┘
```

### 4.2 核心 JS 逻辑

```javascript
// 选择目录（首次配置）
async function selectDirectory() {
  const handle = await window.showDirectoryPicker({ mode: 'readwrite' });
  // 存入 IndexedDB，跨会话持久化
  await saveHandleToIndexedDB(handle);
  return handle.getName(); // 返回目录名用于展示
}

// 写入 .blk 文件
async function writeBlocks(handle, files) {
  for (const { name, content } of files) {
    const fileHandle = await handle.getFileHandle(name, { create: true });
    const writable = await fileHandle.createWritable();
    // content 是 GBK 编码的 Uint8Array
    await writable.write(content);
    await writable.close();
  }
}

// 验证可写入
async function verifyWritable(handle) {
  const testFile = await handle.getFileHandle('.agentrader_test', { create: true });
  const writable = await testFile.createWritable();
  await writable.write(new Uint8Array([0]));
  await writable.close();
  await handle.removeEntry('.agentrader_test');
}
```

### 4.3 与 Streamlit 通信

通过 `Streamlit.setComponentValue()` 将 JS 端的状态回传给 Python：

```javascript
// JS → Python: 目录选择完成
Streamlit.setComponentValue({ event: 'dir_selected', path: dirName });

// JS → Python: 导入完成
Streamlit.setComponentValue({ event: 'import_done', written: 4, total: 94 });
```

Python 端通过 `st.components.html` 的返回值接收：

```python
result = st_components.html(tdx_component_html, height=400)
if result and result.get('event') == 'import_done':
    st.success(f"已导入 {result['written']} 个板块，共 {result['total']} 只股票")
```

---

## 5. 容错与降级

### 5.1 浏览器不支持 File System Access API

检测到 API 不可用时（如 Firefox），自动降级为下载模式：

```
  您的浏览器不支持直接写入本地文件。
  [⬇ 下载 ZIP 文件]  → 解压到 C:\...\T0002\blocknew\
```

### 5.2 目录不可访问

存储的句柄失效时（如用户移动了通达信目录），提示重新选择：

```
  ⚠️ 之前配置的目录无法访问，请重新选择。
  [📂 重新选择通达信目录]
```

### 5.3 写入失败

逐文件报告状态：

```
  0529QB1.blk ❌ 写入失败 (Permission denied)
  0529QBrick.blk  ✅
  0529QB2.blk  ✅
```

---

## 6. 板块文件格式

### 6.1 命名规则

板块文件名 = `MMDD` + `Q` + 策略名，例如：

| 文件 | 内容 |
|------|------|
| `0529QB1.blk` | B1 策略候选（推荐或全部，取决于导入范围） |
| `0529QBrick.blk` | Brick 策略候选 |
| `0529QB2.blk` | B2 策略候选 |

- `MMDD` = 选股日期（月日，两位数字，不足补零），如 `0529`、`1203`
- `Q` = 选股标识前缀，在通达信板块列表中与其他自定义板块区分
- 策略名 = `B1`、`Brick`、`B2`（与项目内部策略名一致）

同一选股日期的所有策略板块共享相同日期前缀，在通达信板块列表中会天然聚合在一起。

### 6.2 板块内排序

**仅推荐模式**：按 `total_score` 降序。

**全部候选模式**：
1. 推荐票（verdict=PASS 且 score ≥ 门槛），按 score 降序
2. 已复评未推荐，按 score 降序
3. 未复评，按股票代码升序（确保稳定排序）

### 6.3 文件格式

GBK 编码，每行 `市场标识 + 6位代码`：

```
0300885
0300840
...
7600206
```

- 上海 (60xxxx, 68xxxx): `7` + 代码
- 深圳 (00xxxx, 30xxxx): `0` + 代码

### 6.4 代码转换

```python
import re

def _to_tdx_code(code: str) -> str | None:
    code = code.strip().zfill(6)
    if not re.fullmatch(r"\d{6}", code):
        return None
    if code.startswith(("60", "68")):
        return f"7{code}"
    if code.startswith(("00", "30")):
        return f"0{code}"
    return None  # 无法识别 → 告警跳过
```

---

## 7. 准确性保障

- 空板块不生成文件（某策略无推荐时不创建 `.blk`）
- 原子写入（先写临时文件再 rename）
- GBK 编码，兼容所有通达信版本
- 逐条代码校验，不合格的跳过并报告
- 板块内去重（防御性）

---

## 8. 实现计划

### 8.1 新增文件

| 文件 | 职责 |
|------|------|
| `pipeline/tdx_export.py` | .blk 内容生成、代码转换、板块分组（纯 Python，不涉及 I/O） |
| `workbench/assets/tdx_importer.html` | 自定义组件 HTML/JS：目录选择器 + 文件写入 + 降级下载 |

### 8.2 修改文件

| 文件 | 改动 |
|------|------|
| `start_workbench` | 添加 SSL 证书参数 |
| `workbench/app.py` | `render_result_center()` 和 `render_history_center()` 中集成导入按钮 + 弹窗 |
| `.gitignore` | 添加 `.certs/` |

### 8.3 工作量

- `pipeline/tdx_export.py`：~100 行
- `workbench/assets/tdx_importer.html`：~200 行（独立 HTML，含所有 JS 逻辑）
- `workbench/app.py`：~150 行（弹窗逻辑 + 组件集成）
- `start_workbench`：~5 行
- 总计约 455 行

### 8.4 不做

- 不单独做 CLI 入口（workbench 驱动即可）
- 不反向读取通达信现有板块
- 不处理 `.ebk` 加密格式
