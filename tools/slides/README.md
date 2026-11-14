# tools/slides：汇报 PPT 生成脚本（可选）

课程汇报的 PPT 由 pptxgenjs 用脚本生成，方便按章节改文案、改配色后快速重出。
这些脚本**不参与数据治理流水线**，也不包含任何患者数据。

| 文件 | 作用 |
|---|---|
| `make_presentation.js` | 生成完整汇报 PPT（封面、治理流程、质量校验、结果页） |
| `make_workflow_slide.js` | 单独生成"LangGraph 治理流程图"一页 |
| `render_presentation.ps1` | 用本机 PowerPoint COM 把 PPT 逐页导出为 PNG（用于预览/截图） |

## 使用

```powershell
npm install pptxgenjs          # 或复用原始资料目录里的 node_modules
node tools\slides\make_presentation.js
```

脚本把 `.pptx` 写到当前目录（`tools/slides/`），`.gitignore` 已排除 `*.pptx` 与
`ppt_preview/`，不要把生成物提交到仓库。

`render_presentation.ps1` 需要本机安装 PowerPoint：

```powershell
powershell -ExecutionPolicy Bypass -File tools\slides\render_presentation.ps1
```

## 注意

- 脚本里的数字（事件数、告警数）是写死在文案里的，改完流程后需要同步更新；
- 汇报材料里不要放患者级截图，见 `docs/04-数据与合规.md`。
