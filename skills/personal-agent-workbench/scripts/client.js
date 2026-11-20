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

  // 状态的标准写法。页面可以换成用户的说法，但不能把两档合成一档，也不能说得比证据更乐观。
  function statusText(handoff) {
    if (!handoff) return "";
    return handoff.unknown ? handoff.status + "（最新情况未知）" : handoff.status;
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
    // 发起交接：{ target, intent: "new"|"continue"|"revise", about, ref, background, ask, expect, confirmAfter, continues }
    run: (fields) => call("POST", "/api/runs", fields),
    // 旧版页面兼容入口。新工作台的执行按钮调用 run。
    handoff: (fields) => call("POST", "/api/handoffs", fields),
    // 用户自己把交接内容粘贴到对话后点的
    markPasted: (id) => call("POST", handoffPath(id, "pasted"), {}),
    // 用户把对话里的结果粘贴回来
    addResult: (id, text) => call("POST", handoffPath(id, "result"), { text }),
    // 待确认的事由用户自己处理完（例如他本人发出了回复）后记一笔；工作台本身不做这类动作
    markDone: (id, note) => call("POST", handoffPath(id, "done"), { note }),
    // 用户看到了明确的失败信息
    markFailed: (id, reason) => call("POST", handoffPath(id, "failed"), { reason }),
    // 直接交给 Codex 的交接，重新查一次最新情况
    refresh: (id) => call("POST", handoffPath(id, "refresh"), {}),
    statusText,
    copyText,
  };
})();
