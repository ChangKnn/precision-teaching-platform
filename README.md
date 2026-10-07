# 思阶｜AI 精准教学工作台

这是供少量教师开展课题实验的试用版，已实现教师建项、诊断发布、学生作答、反馈分析、干预设计与报告导出的主要流程。后端使用 FastAPI 与 Python 标准库 `sqlite3`，AI 功能通过版本化 Skill 服务调用。未配置真实模型时可使用 `mock` 模式体验基础流程；正式生成目标路径、学习活动和整合报告需要真实模型。

## 本地运行

### 一键启动

安装 Python 3.11+ 后，Windows 双击 `start-windows.bat`，macOS 双击 `start-macos.command`。首次运行自动创建 `.venv`、安装依赖，并在缺少 `.env` 时从示例创建配置；已有 `.env` 和教学数据不会被覆盖。启动成功后自动打开浏览器，保持启动窗口打开，按 Ctrl+C 停止服务。正式 AI 功能仍需自行在 `.env` 配置模型与密钥。

首次启动需要联网。macOS 下载 ZIP 后若提示脚本没有执行权限，在项目目录运行 `chmod +x start-macos.command`；操作系统安全提示需由你确认允许打开。也可在终端使用 `python3 start.py`（Windows 用 `py -3 start.py`）。端口占用时不会停止已有服务，可通过 `--port 8001` 指定其他端口，`--no-browser` 可关闭自动打开浏览器。

### 手动启动

要求 Python 3.11 或更新版本（代码使用 `datetime.UTC`），建议 Python 3.13。Windows、macOS 和 Linux 使用同一套 Python 后端与前端，无需复制 macOS 的 `.venv`。

macOS / Linux：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Windows PowerShell（先切换到项目根目录）：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# 编辑 .env 配置模型与密钥；已有 .env 时跳过上面的复制命令。
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

无需激活虚拟环境，因此不受 PowerShell 脚本执行策略影响。安装了其他受支持版本时，将 `py -3.13` 替换为对应版本。`.env` 的相对数据库／上传路径以项目根目录为基准；Windows 自定义绝对路径建议写成 `D:/precision-teaching/data/precision_teaching.db`。

仓库不包含已有教学数据、上传附件、运行日志、真实密钥或虚拟环境：`data/`、`.env` 和数据库文件均由 `.gitignore` 排除。新部署首次启动自动创建空数据库，保持 `SEED_DEMO_DATA=0`（默认值），不会导入现有教师或学生记录。示例配置和 Skill 测试样例仅用于开发，不会自动导入数据库。若要保留本机数据，仍需单独备份本机 `data/`，Git 推送不会替你备份。

打开 <http://127.0.0.1:8000> 进入教师端；打开 <http://127.0.0.1:8000/student/> 进入学生端。接口文档位于 <http://127.0.0.1:8000/docs>，健康检查位于 <http://127.0.0.1:8000/api/health>。

教师端使用“学校＋学科＋教师姓名”作为教学空间标识，不要求密码。首次填写新组合会自动创建空空间，之后填写同一组合返回原空间。学校、学科输入框会提示现有选项，也允许填写新值；新学校可在“教师与班级背景信息”添加班级。项目、当前项目选择、教师资料和报告操作按空间隔离；学生端继续按学校与班级领取任务。教师会话使用 12 小时有效的 HttpOnly Cookie，退出后失效。请注意，这三项资料只能作便捷识别，不能证明登录者身份：知道相同组合的人也能进入同一空间，因此不能直接面向不可信用户部署，正式公开使用仍需增加身份验证。

学生端已经接入后端：根据学校、班级和姓名建立学生身份会话，读取本班已发布任务，将完整对话和最终作答文本写入 SQLite。学生提交后，教师端“学生会话记录与分析结果”会读取真实证据，并可生成个体 SOLO 诊断报告。历史图片提交继续保留只读兼容。

学生登录页的学校、班级下拉选项只显示教师已发布诊断任务的班级。未发布任务的班级不可登录；无论开发或生产环境，都不会在学生登录时自动创建班级。教师需先建立班级并发布任务。

## 当前已接入

- SQLite 自动建表与 WAL 模式；默认不写入演示数据。需要旧版示例项目时可在首次启动前设置 `SEED_DEMO_DATA=1`
- 教师信息读取与保存
- 教师端学校／学科／姓名自动建空间、会话保持、退出及教师 API 跨空间访问保护
- 精准教学项目创建与列表读取
- 每个精准教学独立保存诊断、反馈和干预数据
- 阶段门禁：发布诊断后开放反馈，确认反馈后开放干预
- AI 表述优化 Skill（本地模拟，可替换真实模型）
- `solo-task-rubric-builder` v1.2.0：依据精准教学内容、诊断任务和 Skill 内置的通用 SOLO 量规生成 P/U/M/R/EA 任务量规
- `precision-diagnostic-task-design` v1.0.0：诊断任务页依据当前精准教学信息生成三个有实质差异的任务候选，展示推荐理由；教师选择后可编辑，再保存草稿并生成量规。已移除单独的“诊断目的”步骤，历史诊断类型仍保留在数据中
- `precision-diagnostic-student-ai-dialogue` v1.0.0：学生正式诊断会话使用发布时冻结的教师 AI 角色与对话规则；每轮读取完整历史、输出并校验结构化回复，原始学生发言及内部执行记录分别留存，学生只看到对话回复。已推送报告后的复盘仍走原有逻辑
- 量规 JSON Schema 校验、教师编辑与确认、任务变更失效、发布前门禁
- 教师可选填自定义分析标准，分别启用个人、班级补充反馈；`teacher-custom-analysis` Skill 独立读取学生原始文字证据，不改变 SOLO 定级或干预设计依据。标准或学生证据变化后，旧补充反馈会标记为需重新生成；图片附件暂不参与这项分析
- `solo-student-diagnosis-feedback` v1.1.0：综合完整对话和文字成果生成个体层级、证据、文字思维结构图、四维分析、Plus One 目标与主题学习策略
- 学生个体报告生成、Schema 校验、教师编辑、确认与推送状态持久化
- `solo-class-diagnosis-intervention` v1.0.0：从已确认的个体报告生成班级 SOLO 聚合诊断
- `precision-intervention-goal-path-design` v1.0.0：在“精准干预设计 → 目标设计与活动路径”读取当前班级聚合诊断、精准教学信息和教师确认的教学分析，生成共同目标、分层目标及阶段—活动单元路径；教师可修改、保存与确认
- `precision-intervention-activity-formative-regulation` v1.0.0：在“精准干预设计 → 学习活动设计”承接已确认的目标与路径，生成逐项教案式活动表和关键形成性评价；教师可修改、保存与确认。并行小组汇总在同一活动内，阶段顺序、时长、目标和学生归属由服务端校验
- `precision-intervention-plan-integration-review` v1.2.0：在“报告预览与导出”读取当前班级已确认的诊断、目标路径、学习活动与教师条件，生成七段表格式教案及 R1—R8 八项审核；结果按精准教学和班级保存，上游变化后提示重新生成。项目内副本兼容班级诊断输出 `1.1`，并保留真实来源版本
- 当前有效报告可一键下载独立 PDF，也可导出 HTML 文件；PDF 由后端直接排版生成，不含浏览器页眉页脚。旧版项目缺少结构化上游数据时不能生成新版报告
- PDF 导出依赖 `reportlab`；若部署环境没有可嵌入的中文字体，可通过 `PDF_CJK_FONT_PATH` 指定中文 TTF/TTC 字体文件

- 干预设计第 1 步不再重复填写学生诊断摘要；平台 AI 先基于班级报告起草“教学内容与课标分析”和“教学重点与难点”，教师修改确认后作为目标路径 Skill 的输入。缺少课标原文时会提示教师核对，不编造条款
- 目标与路径按“精准教学＋班级”持久化；上游班级诊断更新后标记旧方案失效，确认前不开放后续学习活动设计
- 教师推送报告后，学生端实时出现反馈通知卡片与报告查看弹窗
- 报告推送后开放复盘对话，AI 同时使用教师报告、原对话和学生最终成果作为上下文
- 干预设计 Skill 的结构化输出示例
- AI 任务记录、Skill 版本与审计日志
- 学生身份会话、班级任务分配与学习会话
- 学生 AI 对话逐轮持久化
- 学生最终作答文本提交与教师端证据回流（历史图片只读兼容）
- FastAPI 同源托管前端，无需单独启动两个服务

## 目录

PDF 会依次尝试指定字体、Windows 宋体／黑体、macOS 黑体及 Linux Noto 字体；不兼容的字体会跳过，最终使用 ReportLab 的中文 CID 字体回退。为保证不同 PDF 阅读器中的中文显示一致，建议显式设置可嵌入的 TrueType 中文字体，例如 Windows 的 `PDF_CJK_FONT_PATH=C:/Windows/Fonts/simsun.ttc`。Word 导出使用 Arial 与中文宋体，接收端未安装对应字体时由 Word/WPS 替换字体，不依赖 macOS 专用字体。

```text
frontend/                 教师端页面及后端连接代码
frontend/student/         学生端页面
backend/app/              FastAPI 应用、数据层与 AI 服务层
backend/skills/           可版本化的 Skill 说明
data/                     本地 SQLite 数据库（首次启动自动生成）
```

## 接入真实模型

教师端 Skill 由 `backend/app/services/ai.py` 隔离，学生诊断对话由 `backend/app/services/student_chat.py` 隔离。两者分别读取项目根目录 `.env` 中的配置；没有 `.env` 时可参考 `.env.example` 创建。当前使用 OpenRouter 的 GPT-6 Luna Pro 时填写：

```dotenv
OPENROUTER_API_KEY=你的OpenRouter密钥
AI_PROVIDER=openrouter
AI_BASE_URL=https://openrouter.ai/api/v1
AI_MODEL=openai/gpt-6-luna-pro

STUDENT_AI_PROVIDER=openrouter
STUDENT_AI_BASE_URL=https://openrouter.ai/api/v1
STUDENT_AI_MODEL=openai/gpt-6-luna-pro
```

`OPENROUTER_API_KEY` 同时供教师端和学生端使用；已有的 `AI_API_KEY` 与 `STUDENT_AI_API_KEY` 仅供其他 Provider 使用，切换到 OpenRouter 后不会读取。使用 DeepSeek 时填写：

```dotenv
AI_PROVIDER=deepseek
AI_BASE_URL=https://api.deepseek.com
AI_API_KEY=你的教师端密钥
AI_MODEL=deepseek-flash

STUDENT_AI_PROVIDER=deepseek
STUDENT_AI_BASE_URL=https://api.deepseek.com
STUDENT_AI_API_KEY=你的学生端密钥
STUDENT_AI_MODEL=deepseek-flash
```

`AI_*` 用于教师端生成任务、分析和报告；`STUDENT_AI_*` 用于学生诊断对话。修改 `.env` 后需重启后端，刷新浏览器不够。不要把真实密钥提交到代码仓库或发送到聊天中。学生姓名和本地学生 ID 不会发送给外部模型。

OpenRouter 与 DeepSeek 的任务量规、学生报告、班级报告、目标路径、学习活动及最终整合报告调用 `/chat/completions`，并在返回后继续执行本地 JSON Schema 校验；不合格结果不会写成有效报告。目标路径、学习活动和最终整合报告要求真实模型及已确认的上游结果，`mock` 不会生成正式方案。OpenAI Responses API 接入仍然保留，可通过 Provider 配置切换。

开发环境下，教师端顶部的“LLM 请求”可查看每次真实模型调用的完整 JSON 入参；浏览器开发者控制台也会打印同一内容，包括模型重试。学生对话的实际系统提示和历史消息同样记录。记录从启用此功能后开始，保存在本地 SQLite 的 `llm_request_logs` 表中，不包含 API Key 或 Authorization 请求头。由于内容可能包含学生作答和报告，查看接口仅在 `APP_ENV=development` 且从本机访问时开放；生产环境不记录，也不展示此入口。

当前对话需求只是“依据教师规则进行中性追问”，没有工具调用、检索或多智能体编排，因此没有引入 LangChain。若后续需要多模型路由、知识库检索、复杂工具链或可视化追踪，再在现有 Provider 接口后增加 LangChain，不需要改动学生数据结构和前端。

## 生产部署建议

### 跨平台验证

```bash
python -m unittest discover -s backend -p "test_*.py"
```

测试使用临时数据库或模拟数据，不需要真实 API Key。GitHub Actions 已配置 Windows、macOS、Linux 和 Python 3.11／3.13 的测试矩阵；实际 Windows 可用性以该矩阵运行结果及目标机器部署测试为准。

初期使用一台中国大陆云服务器：Nginx 反向代理到 Uvicorn/Gunicorn，前后端同域，SQLite 文件放在持久化磁盘并定时备份。前端需要独立 CDN 时，再将 `frontend/` 部署到 EdgeOne Pages，并把 `/api` 反代到后端域名。
