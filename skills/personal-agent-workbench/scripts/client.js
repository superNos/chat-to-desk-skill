// 个人工作台页面可选使用的接口封装。只负责和本机运行支持通信，不含任何界面、栏目或文案模板。
// 页面用 <script src="/_workbench/client.js"></script> 引入，然后调用 window.Workbench 上的方法。
(function () {
  "use strict";
  const meta = document.querySelector('meta[name="workbench-token"]');
  const token = meta ? meta.getAttribute("content") : "";

  async function call(method, path, body) {
    const options = { method, headers: { "X-Workbench-Token": token }, credentials: "same-origin" };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    const response = await fetch(path, options);
    let data = {};
    try {
      data = await response.json();
    } catch (error) {
      data = { ok: false, error: "工作台没有返回可读的内容" };
    }
    if (!response.ok || !data.ok) {
      throw new Error(data.error || "工作台出错了（" + response.status + "）");
    }
    return data;
  }

  const handoffPath = (id, action) => "/api/handoffs/" + encodeURIComponent(id) + "/" + action;

  // 给用户看的状态。证据状态（handoff.status）留在 events 里，用户问“为什么”时再展开。
  const USER_STATUS = {
    "需手动完成": "待你手动交接",
    "排队中": "排队中",
    "已发送": "处理中",
    "已开始": "处理中",
    "已完成": "有结果",
    "待确认": "等你决定",
    "失败": "出错了",
  };

  function statusText(handoff) {
    if (!handoff) return "";
    const shown = USER_STATUS[handoff.status] || handoff.status;
    return handoff.unknown ? "暂时联系不上（上次：" + shown + "）" : shown;
  }

  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  }

  window.Workbench = {
    // 读取：目标（不含对话编号）、四个交接标签、页面记录、交接记录
    state: () => call("GET", "/api/state"),
    // 页面自己的记录，内容由页面决定；覆盖已有记录或删除前，页面必须先请用户确认这一次
    saveRecord: (key, value) => call("PUT", "/api/records/" + encodeURIComponent(key), { value }),
    replaceRecord: (key, value, confirmed) =>
      call("PUT", "/api/records/" + encodeURIComponent(key), { value, replace: true, confirmed: confirmed === true }),
    deleteRecord: (key, confirmed) =>
      call("DELETE", "/api/records/" + encodeURIComponent(key), { confirmed: confirmed === true }),
    // 在后台交给 Agent：{ target, intent: "new"|"continue"|"revise", about, ref, background, ask, expect, confirmAfter, continues }
    // 同一对话按先后排队，不同对话并行；不会打开或切换用户正在看的 Codex 页面
    run: (fields) => call("POST", "/api/runs", fields),
    // 重新查一次最新情况（不会重新发送）
    refresh: (id) => call("POST", handoffPath(id, "refresh"), {}),
    // 待确认的事由用户自己处理完（例如他本人发出了回复）后记一笔；工作台本身不做这类动作
    markDone: (id, note) => call("POST", handoffPath(id, "done"), { note }),
    // 用户看到了明确的失败信息
    markFailed: (id, reason) => call("POST", handoffPath(id, "failed"), { reason }),
    statusText,
    copyText,
    // 保留旧页面使用的顶层别名，升级运行支持不破坏已有工作台。
    handoff: (fields) => call("POST", "/api/handoffs", fields),
    markPasted: (id) => call("POST", handoffPath(id, "pasted"), {}),
    addResult: (id, text) => call("POST", handoffPath(id, "result"), { text }),
    // 旧版兼容：只给还在用手动交接的旧页面，新页面不要用
    legacy: {
      handoff: (fields) => call("POST", "/api/handoffs", fields),
      markPasted: (id) => call("POST", handoffPath(id, "pasted"), {}),
      addResult: (id, text) => call("POST", handoffPath(id, "result"), { text }),
    },
  };
})();
