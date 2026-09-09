# 推题生题

面向数学试卷的本地题库处理、知识点标注、教师审核、个性化推荐和 AI 新题生成工作台。

## 功能

- 试卷 OCR：PDF/Word → Markdown、HTML。
- 题目结构化：拆分题目、答案、解析和图片资源。
- 知识点标注：题型、知识点、难度、解法信号、标签置信度和审计报告。
- 教师工作台：浏览题库、审核标签、按知识点或学情筛题、组卷、预览、打印和导出。
- 个性化推荐：根据题库标签和学生作答记录生成推荐结果。
- 实时生题：调用 Cherry Studio 本地网关生成新题，并通过独立求解、公式检查、答案校验和重复检测。
- Word 导出：将含数学公式的 HTML 转换为可编辑公式的 DOCX。

## 快速开始：打开现成题库

要求 Windows、Python 3.11+。在项目根目录打开 PowerShell：

```powershell
python .\smart-question-tagger\scripts\review_server.py .\output\示例题库_structured --open
```

浏览器访问：

```text
http://127.0.0.1:8765/review/index.html
```

终端窗口需要保持运行，按 `Ctrl+C` 停止服务。端口被占用时可以改用：

```powershell
python .\smart-question-tagger\scripts\review_server.py .\output\示例题库_structured --port 8766 --open
```

不要直接双击 `review\index.html`；实时生题和本地审核保存需要通过本地服务打开。

## 实时生成新题

实时生成依赖正在运行的 Cherry Studio 本地 OpenAI 兼容网关。请在当前电脑的用户环境中配置：

```text
CHERRY_STUDIO_BASE_URL
CHERRY_STUDIO_API_KEY
CHERRY_STUDIO_MODEL
```

其中 `CHERRY_STUDIO_MODEL` 使用 `providerId:modelId` 格式。API Key 只放在 Cherry Studio、系统凭据或用户级环境变量中，不要写入代码、HTML、`.env` 文件或 Git 历史。

配置完成后，在教师工作台选择知识点或学生，点击“开始实时生成”。生成题目只有通过校验后才会进入试卷。

## 从新试卷建立题库

### 1. OCR

MinerU 需要单独的 API Token。建议把缓存放在项目的被忽略目录中：

```powershell
python .\exam-ocr\scripts\ocr_exam.py `
  .\示例题库\新试卷.pdf `
  -o .\output\新试卷_ocr `
  --cache-dir .\.local\mineru_cache
```

Token 使用系统环境变量 `MINERU_TOKEN` 配置，不要提交到仓库。

### 2. 结构化和标注

```powershell
python .\exam-ocr\scripts\structure_outputs.py `
  .\output\新试卷_ocr `
  --structured-output .\output\新试卷_structured

python .\smart-question-tagger\scripts\tag_structured_bank.py `
  .\output\新试卷_structured `
  --simulate-students 48

python .\smart-question-tagger\scripts\build_review_workbench.py `
  .\output\新试卷_structured

python .\smart-question-tagger\scripts\review_server.py `
  .\output\新试卷_structured --open
```

`--simulate-students 48` 只用于演示或测试。正式使用时，应提供真实的学生作答记录。

如果已经有结构化题库，可以从 `tag_structured_bank.py` 开始。

## Word 导出

Word 导出需要安装 Pandoc：

```powershell
python .\html-math-to-docx\scripts\diagnose_html_math.py .\input.html
python .\html-math-to-docx\scripts\convert_html_math_to_docx.py .\input.html -o .\output.docx
python .\html-math-to-docx\scripts\validate_docx_math.py .\output.docx
```

## 运行测试

运行测试需要 `pytest`：

```powershell
python -m pytest -q `
  .\smart-question-recommender-v3\tests `
  .\smart-question-tagger\tests `
  .\exam-ocr\tests `
  .\generating-math-variants\tests
```

## 目录说明

```text
exam-ocr/                         试卷 OCR 和结构化
smart-question-tagger/            知识点标注、审核和教师工作台
smart-question-recommender-v3/    推荐、组卷、实时生题和校验
generating-math-variants/         数学变式题生成原型
html-math-to-docx/                HTML 数学题转 DOCX
示例题库/                          示例原始试卷
output/示例题库_structured/        当前可直接打开的结构化题库
```

## 常见问题

- 工作台显示离线：确认 `review_server.py` 仍在运行。
- 新题生成失败：确认 Cherry Studio 网关已启动，并检查三个 `CHERRY_STUDIO_*` 配置。
- 显示“0 道新题”：确认当前题库已经完成标注，并使用最新的 `review/index.html`。
- 端口冲突：启动服务时增加 `--port 8766` 等参数。

## 安全提示

本仓库是公开仓库。不要提交 API Key、真实学生隐私数据、内部账号信息或未授权的试卷资料。项目当前不提供一键安装包；新电脑需要自行安装 Python，以及按需安装 Pandoc、MinerU 和 Cherry Studio。
