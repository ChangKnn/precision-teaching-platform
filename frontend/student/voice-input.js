/* Microphone -> authenticated same-origin relay -> DashScope. No API key here. */
(() => {
  'use strict';
  function pcm16le(samples) {
    const bytes = new ArrayBuffer(samples.length * 2), view = new DataView(bytes);
    for (let i = 0; i < samples.length; i++) {
      const value = Math.max(-1, Math.min(1, samples[i] || 0));
      view.setInt16(i * 2, value < 0 ? value * 32768 : value * 32767, true);
    }
    return bytes;
  }
  class Transcript {
    constructor() { this.sentences = new Map(); }
    update(sentence) {
      if (!sentence || sentence.heartbeat === true || !Number.isInteger(sentence.sentence_id) ||
          sentence.sentence_id < 0 || typeof sentence.text !== 'string') return false;
      const old = this.sentences.get(sentence.sentence_id);
      if (old?.final && sentence.sentence_end !== true) return false;
      this.sentences.set(sentence.sentence_id, {text: sentence.text, final: sentence.sentence_end === true});
      return true;
    }
    get text() { return [...this.sentences].sort((a, b) => a[0] - b[0]).map(item => item[1].text).join(''); }
  }

  function create({button, status, input, getContext, request, onBusyChange, onDraftChange, onError}) {
    let run = null;
    const contextOK = r => {
      const now = getContext();
      return run === r && now.token === r.context.token && now.sessionId === r.context.sessionId &&
        now.versionId === r.context.versionId && !now.disabled;
    };
    function later(r, callback, ms) {
      const id = window.setTimeout(() => { r.timeouts.delete(id); if (run === r) callback(); }, ms);
      r.timeouts.add(id); return id;
    }
    function clearLater(r, id) { window.clearTimeout(id); r.timeouts.delete(id); }
    function paint() {
      const phase = run?.phase;
      button.classList.toggle('is-recording', phase === 'recording');
      button.setAttribute('aria-pressed', phase === 'recording' ? 'true' : 'false');
      button.setAttribute('aria-label', phase === 'recording' ? '停止语音输入' : run ? '取消语音输入' : '开始语音输入');
      button.title = phase === 'recording' ? '点击停止，等待最后一句' : '语音输入';
      button.disabled = phase === 'stopping' || (!run && (!getContext().sessionId || getContext().disabled));
      button.querySelector('[data-voice-label]').textContent = phase === 'recording' ? '停止' : run ? '取消' : '语音';
    }
    function render(r) {
      if (!contextOK(r)) return;
      input.value = r.before + r.transcript.text + r.after;
      onDraftChange();
      if (input.value.length > 4000 && r.phase === 'recording') {
        stop();
        onError('文字已超过单条4000字，请在发送前编辑或分段发送。');
      }
    }
    async function releaseCapture(r, flush = false) {
      if (r.releasing) return r.releasing;
      r.releasing = (async () => {
        r.stream?.getTracks().forEach(track => { track.onended = null; track.stop(); });
        r.source?.disconnect();
        if (r.processor) r.processor.onaudioprocess = null;
        if (flush && r.worklet) {
          await new Promise(resolve => {
            r.flushResolve = resolve;
            r.flushTimer = window.setTimeout(resolve, 350);
            r.worklet.port.postMessage({type: 'stop'});
          });
        }
        window.clearTimeout(r.flushTimer);
        if (r.worklet) { r.worklet.port.onmessage = null; r.worklet.port.close(); r.worklet.disconnect(); }
        r.processor?.disconnect(); r.gain?.disconnect();
        if (r.audioContext && r.audioContext.state !== 'closed') await r.audioContext.close().catch(() => {});
      })();
      return r.releasing;
    }
    function end(r, message, error = false) {
      if (run !== r) return;
      run = null; // Invalidate all late permission/module/socket callbacks first.
      r.timeouts.forEach(id => window.clearTimeout(id));
      window.clearInterval(r.clock); r.flushResolve?.();
      releaseCapture(r).catch(() => {});
      if (r.ws) {
        r.ws.onopen = r.ws.onmessage = r.ws.onclose = r.ws.onerror = null;
        r.ws.close();
      }
      input.readOnly = r.wasReadOnly;
      status.setAttribute?.('aria-live', 'polite');
      status.textContent = message;
      onBusyChange(false); paint();
      if (error) onError(message);
    }
    function fail(r, message) { end(r, message + ' 已收到的文字仍在输入框中。', true); }
    function sendPCM(r, bytes) {
      if (!contextOK(r) || !r.started || r.finishSent || !['recording', 'stopping'].includes(r.phase)) return;
      if (r.ws?.readyState !== 1) { fail(r, '语音连接已断开，请重试。'); return; }
      if (r.ws.bufferedAmount > 256 * 1024) { fail(r, '网络较慢，音频发送积压，请换网络后重试。'); return; }
      let binary = '';
      for (const byte of new Uint8Array(bytes)) binary += String.fromCharCode(byte);
      try { r.ws.send(JSON.stringify({audio: btoa(binary)})); }
      catch (_) { fail(r, '音频发送失败，请重试。'); }
    }
    async function capture(r) {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true}, video: false
      });
      if (!contextOK(r)) { stream.getTracks().forEach(track => track.stop()); return; }
      r.stream = stream;
      stream.getTracks().forEach(track => { track.onended = () => { if (run === r) fail(r, '麦克风已被系统停止。'); }; });
      const ac = r.audioContext;
      r.source = ac.createMediaStreamSource(stream);
      r.gain = ac.createGain(); r.gain.gain.value = 0; r.gain.connect(ac.destination);
      if (ac.audioWorklet && window.AudioWorkletNode) {
        try {
          await ac.audioWorklet.addModule('/student/pcm-worklet.js?v=20261031');
          if (!contextOK(r)) return;
          r.worklet = new window.AudioWorkletNode(ac, 'student-pcm', {numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1]});
          r.worklet.onprocessorerror = () => fail(r, '音频采集异常，请重试。');
          r.worklet.port.onmessage = event => {
            if (event.data.type === 'pcm') sendPCM(r, event.data.buffer);
            if (event.data.type === 'stopped') r.flushResolve?.();
          };
          r.source.connect(r.worklet); r.worklet.connect(r.gain);
          return;
        } catch (_) {
          if (!contextOK(r)) return;
          if (r.worklet) { r.worklet.port.close(); r.worklet.disconnect(); r.source.disconnect(); r.worklet = null; }
          // Older Safari/WebViews may not support AudioWorklet modules.
        }
      }
      r.processor = ac.createScriptProcessor(2048, 1, 1);
      r.processor.onaudioprocess = event => {
        event.outputBuffer.getChannelData(0).fill(0);
        sendPCM(r, pcm16le(event.inputBuffer.getChannelData(0)));
      };
      r.source.connect(r.processor); r.processor.connect(r.gain);
    }
    async function stop() {
      const r = run;
      if (!r || r.phase === 'stopping') return;
      if (r.phase !== 'recording') { end(r, '已取消语音输入。'); return; }
      r.phase = 'stopping'; window.clearInterval(r.clock);
      status.setAttribute?.('aria-live', 'polite');
      status.textContent = '整理最后一句…'; paint();
      // Flush worklet's partial PCM chunk before the ordered finish control frame.
      await releaseCapture(r, true);
      if (run !== r) return;
      r.finishSent = true;
      if (r.ws?.readyState !== 1) { fail(r, '语音连接中断，请检查最后一句。'); return; }
      try { r.ws.send(JSON.stringify({type: 'finish'})); }
      catch (_) { fail(r, '结束语音任务失败。'); return; }
      later(r, () => fail(r, '等待最后一句识别超时，请检查后发送。'), 14000);
    }
    async function start() {
      if (run) { await stop(); return; }
      const context = {...getContext()};
      if (!context.token || !context.sessionId || context.disabled) return;
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia || !(window.AudioContext || window.webkitAudioContext)) {
        onError('语音输入需要HTTPS和麦克风支持，请使用iPhone Safari或Android Chrome。'); return;
      }
      if (input.value.length >= 4000) { onError('输入已达4000字，请先编辑或分段发送。'); return; }
      const cursor = Number.isInteger(input.selectionStart) ? input.selectionStart : input.value.length;
      const selectionEnd = Number.isInteger(input.selectionEnd) ? input.selectionEnd : cursor;
      const r = {context, phase: 'starting', timeouts: new Set(), transcript: new Transcript(),
        before: input.value.slice(0, cursor), after: input.value.slice(selectionEnd), wasReadOnly: input.readOnly};
      run = r; input.readOnly = true; status.textContent = '准备语音服务…';
      onBusyChange(true); paint();
      later(r, () => fail(r, '启动语音输入超时，请检查权限和网络。'), 25000);
      try {
        // Must be invoked synchronously from the user's tap, before network awaits.
        r.audioContext = new (window.AudioContext || window.webkitAudioContext)();
        await r.audioContext.resume();
        if (!contextOK(r)) return;
        const config = await request('/api/student/asr/config');
        if (!contextOK(r)) return;
        if (!config.enabled) { end(r, '语音服务尚未配置，请使用键盘输入。', true); return; }
        r.maxSeconds = config.max_seconds || 180;
        status.textContent = '请允许麦克风权限…';
        await capture(r);
        if (!contextOK(r)) return;
        status.textContent = '连接语音识别服务…';
        const url = new URL('/api/student/asr/ws', window.location.href);
        url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
        r.ws = new WebSocket(url.href); // No token/key in URL or subprotocol.
        r.ws.onopen = () => {
          if (!contextOK(r)) return;
          r.ws.send(JSON.stringify({type: 'start', token: context.token, session_id: context.sessionId,
                                    sample_rate: r.audioContext.sampleRate}));
        };
        r.ws.onerror = () => fail(r, '语音连接失败，请检查网络后重试。');
        r.ws.onclose = () => { if (run === r) fail(r, '语音连接提前关闭，请检查最后一句。'); };
        r.ws.onmessage = event => {
          if (!contextOK(r)) { end(r, '任务已切换，语音输入已停止。'); return; }
          try {
            const value = JSON.parse(event.data), kind = value.header?.event;
            if (value.type === 'error' || kind === 'task-failed') {
              fail(r, `${value.message || value.header.error_message || '语音识别失败'}${value.code || value.header?.error_code ? `（${value.code || value.header.error_code}）` : ''}`); return;
            }
            if (kind === 'task-started' && !r.started) {
              r.timeouts.forEach(id => clearLater(r, id)); r.started = true; r.phase = 'recording'; r.began = Date.now();
              status.setAttribute?.('aria-live', 'off'); // Don't announce the recording clock every second.
              status.textContent = '正在聆听 · 00:00 · 点击停止后可编辑'; paint();
              r.clock = window.setInterval(() => {
                const seconds = Math.floor((Date.now() - r.began) / 1000);
                status.textContent = `正在聆听 · ${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')} · 点击停止`;
                if (seconds >= r.maxSeconds) stop();
              }, 500);
            } else if (kind === 'result-generated') {
              if (r.transcript.update(value.payload?.output?.sentence)) render(r);
            } else if (kind === 'task-finished') {
              end(r, '语音已转为文字，请检查、编辑后发送。');
            } else if (value.type === 'stopping') stop();
          } catch (_) { fail(r, '语音服务返回异常，请重新开始。'); }
        };
      } catch (error) {
        if (run !== r) return;
        const message = error.name === 'NotAllowedError' ? '麦克风权限被拒绝，请在浏览器设置中允许后重试。'
          : error.name === 'NotFoundError' ? '未找到麦克风，请检查设备。'
          : error.name === 'NotReadableError' ? '麦克风被其他应用占用，请关闭后重试。'
          : '无法启动语音输入，请检查权限、网络或使用键盘输入。';
        fail(r, message);
      }
    }
    const cancel = (message = '语音已停止，已收到的文字保留。') => { if (run) end(run, message); };
    button.addEventListener('click', start);
    window.addEventListener('pagehide', () => cancel());
    document.addEventListener('visibilitychange', () => { if (document.hidden) cancel('页面已进入后台，语音输入已停止。'); });
    paint();
    return {get busy() { return Boolean(run); }, start, stop, cancel,
      refresh() { if (run && !contextOK(run)) cancel('任务或登录状态已变化，语音输入已停止。'); paint(); }};
  }
  const api = {create, pcm16le, Transcript};
  if (typeof window !== 'undefined') window.StudentVoiceInput = api;
  if (typeof module !== 'undefined') module.exports = api;
})();
