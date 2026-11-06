# 文档转 Markdown

命令行输入飞书 `/docx/` 链接或本地文件路径，生成 Markdown。仅做转换，不含 UI、数据库或多轮 Agent。

## 启动（PowerShell）

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe convert.py
```

已有 `.venv` 可直接运行最后一行。输入地址后按提示确认发送和隐藏输入 Key；输入 `exit` 退出。
仅本地提取：`python convert.py --local-only`。
单次转换：`python convert.py sample.txt -o output/demo.md`。

## 配置

默认公司 GLM-5.2，可通过进程环境变量覆盖：

```dotenv
LLM_BASE_URL=http://221.131.102.69:18088/v1
LLM_MODEL=glm-5.2
LLM_API_KEY=<你的 key>
```

`.env.example` 仅为模板，不自动加载。推荐运行时隐藏输入 Key，不写入代码。HTTP 明文传输，不发送敏感材料。

## 结构

- `convert.py`：命令行负责确认授权、读取 Key 和显示错误；转换流程负责提取、调用模型和保存。
- `feishu.js`：Browser MCP 使用的页面读取脚本。
- `test_convert.py`：运行 `python -m unittest -v` 自检。

本地文件由 MarkItDown 读取；飞书使用已登录 Chrome 与 Agent360 Browser MCP，需要 Node.js 和浏览器扩展。
自动输出到 `output/`，本地文件按文件名、飞书按文档 ID 命名，重名追加编号。
原始结果为 `.raw.md`，模型结果为 `.md`；取消发送仍保留原始结果。
最终 `.md` 在正文末尾追加「文档总结（AI 生成）」及 1–5 条要点，不替代正文、不补充外部事实。
纯本地模式及 `.raw.md` 不生成 AI 总结；总结缺失或为空时拒绝保存为成功结果。

## 限制与提交

不做 OCR；只支持飞书 `/docx/`。动态页面、复杂表格及嵌入内容可能遗漏，必须核对。
模型最多处理 12000 字符，120 秒超时、不自动重试，空输出或截断不保存为成功结果。
模型可能改写内容；本地输出明文保存，没有自动密级识别、脱敏或清理。
默认 GLM-5.2 此前生成请求超时，真实成功截图待补；单元测试不代表服务已接通。

提交正文见 `submission/01-正文.md`，完整运行说明见 `submission/02-说明.md`。
不要提交密钥、内部文档、`.venv/` 或整个 `output/`。
