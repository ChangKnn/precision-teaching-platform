"use strict";

// Development-only viewer for the *actual* outbound request JSON, not a
// reconstructed prompt. The server never includes Authorization headers here.
window.LLMDebug = (() => {
  const button = document.getElementById("llmDebugButton");
  const dialog = document.getElementById("llmDebugDialog");
  const list = document.getElementById("llmDebugList");
  const body = document.getElementById("llmDebugBody");
  const meta = document.getElementById("llmDebugMeta");
  const records = [];
  let lastId = 0;
  let selectedId = null;
  let polling = false;
  let timer = null;

  function label(record) {
    return `${record.channel === "student" ? "学生端" : "教师端"} · ${record.purpose}`;
  }

  function select(record) {
    selectedId = record.id;
    meta.textContent = `#${record.id} · ${label(record)} · ${record.provider} · ${record.created_at} · ${record.endpoint}`;
    body.value = JSON.stringify(record.request, null, 2);
    for (const item of list.querySelectorAll("button")) item.classList.toggle("active", Number(item.dataset.id) === selectedId);
  }

  function renderList() {
    list.replaceChildren();
    for (const record of [...records].reverse()) {
      const item = document.createElement("button");
      item.type = "button";
      item.dataset.id = String(record.id);
      const title = document.createElement("strong");
      title.textContent = `#${record.id} ${label(record)}`;
      const detail = document.createElement("small");
      detail.textContent = `${record.created_at} · ${record.provider}`;
      item.append(title, detail);
      item.addEventListener("click", () => select(record));
      list.append(item);
    }
    if (!records.length) list.textContent = "还没有真实 LLM 请求。";
    const selected = records.find(record => record.id === selectedId);
    if (selected) select(selected);
  }

  async function poll() {
    if (polling) return;
    polling = true;
    try {
      const response = await fetch(`/api/dev/llm-requests?after_id=${lastId}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`请求记录不可用（${response.status}）`);
      const payload = await response.json();
      for (const record of payload.requests || []) {
        if (record.id <= lastId) continue;
        lastId = record.id;
        records.push(record);
        console.groupCollapsed(`[LLM 请求 #${record.id}] ${label(record)} · ${record.provider}`);
        console.log("实际发送的 JSON 入参（不含鉴权头）：", record.request);
        console.log("接口：", record.endpoint, "时间：", record.created_at);
        console.groupEnd();
      }
      if (records.length > 100) records.splice(0, records.length - 100);
      if (dialog.open) renderList();
    } catch (error) {
      console.warn("读取本地 LLM 请求记录失败：", error);
    } finally {
      polling = false;
    }
  }

  button.addEventListener("click", () => {
    renderList();
    dialog.showModal();
    poll();
  });
  document.getElementById("llmDebugClose").addEventListener("click", () => dialog.close());

  return {
    start(enabled) {
      if (!enabled || timer) return;
      button.hidden = false;
      poll();
      timer = window.setInterval(() => {
        if (!document.hidden || dialog.open) poll();
      }, 5000);
    },
    refresh: poll,
  };
})();
