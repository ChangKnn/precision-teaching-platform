# 学生端实时语音输入

学生输入框旁点击“语音”，停止后等待尾句，编辑确认后再发送。不会自动提交聊天或最终成果；键盘输入保留。

## 配置与部署

1. 安装 `requirements.txt`（新增 `websockets>=15,<18`）。
2. 本机 `.env` 或生产 `/etc/precision-teaching/app.env` 填写 `DASHSCOPE_API_KEY=`，值为百炼北京地域、具备模型权限的Key。真实配置禁止提交Git，生产文件保持root所有、0600权限。
3. 可选 `ASR_MAX_SECONDS=180`（10—600秒）、`ASR_MAX_CONNECTIONS=64`（单进程1—128）。同一学生同一时刻仅允许一个语音连接。生产目前是单个Uvicorn worker；多worker部署需改用共享并发配额。
4. 合并 `deploy/nginx/precision-teaching.conf` 中 `/api/student/asr/ws` 的专用代理配置，确保 `Upgrade` / `Connection` 头透传。不要覆盖现有证书配置；执行 `nginx -t` 后重载Nginx，重启后端。
5. 手机通过HTTPS访问。用户点击后授权麦克风；页面进入后台、切换任务或退出登录会停止录音。iPhone建议Safari，Android建议Chrome。iframe嵌入还需要宿主允许麦克风。

未配置Key时不建立阿里云连接，点击语音会明确提示未配置并恢复文本编辑。配置Key后仍需用真实手机验证权限、录音、尾句和中断恢复；自动化测试不等同于真机及供应商验收。

## 数据与协议

- 麦克风的Web Audio实际采样率写入初始化，不固定48000；AudioWorklet优先，旧浏览器回退ScriptProcessor。编码为单声道PCM signed 16-bit、小端。
- `GET /api/student/asr/config` 沿用 `X-Student-Token` 鉴权，仅返回是否已配置与时长，不返回Key。
- WebSocket `/api/student/asr/ws` 必须同源，URL不接收查询参数。第一条文本帧为 `{ "type":"start", "token":"学生登录令牌", "session_id":"本人的会话ID", "sample_rate":44100 }`；初始化8秒超时，校验令牌、任务归属和可输入状态。
- 收到 `header.event=task-started` 后浏览器才发送 `{ "audio":"Base64 PCM" }` 文本帧；后端严格解码并以 **bytes二进制帧** 转给阿里云。浏览器不能指定上游地址、模型、Key或task_id。
- 停止采集并刷新worklet的尾部PCM后，浏览器发送 `{ "type":"finish" }`。后端为同一UUID构造 `finish-task` JSON文本帧，等待末尾结果和 `task-finished`；12秒服务端、14秒浏览器兜底。不会在finish后继续转发PCM。
- 转发识别结果中的sentence_id/text/sentence_end；忽略heartbeat。前端按句子ID替换中间结果，最终结果不被迟到的中间结果覆盖。
- 浏览器缓冲超过256KiB会中止；后端限制单帧、发送速率、总音频量、连接数和时长，处理过期登录及任务撤回。上游错误只显示经过密钥脱敏的code/message，不记录音频或转写文本。
- 录音实时发送至阿里云；本平台中继不落盘、不记录音频或识别文本，只有学生主动发送后，文字才按原聊天流程保存。阿里云的数据处理适用该服务的约定。
- ASR直连固定北京地址 `wss://dashscope.aliyuncs.com/api-ws/v1/inference`，不沿用面向GPT/Claude的代理；对话模型设置不受影响。

官方协议：[客户端事件](https://help.aliyun.com/zh/model-studio/qwen-audio-asr-streaming-client-events)、[服务端事件](https://help.aliyun.com/zh/model-studio/qwen-audio-asr-streaming-server-events)。
