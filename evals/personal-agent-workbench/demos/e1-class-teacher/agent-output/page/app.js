"use strict";
const api = window.Workbench;
const pending = document.querySelector("#pending");
const historyList = document.querySelector("#history");
const search = document.querySelector("#search");
const feedback = document.querySelector("#feedback");
let state;
let busy = false;

function element(type, text) {
  const node = document.createElement(type);
  if (text) node.textContent = text;
  return node;
}

async function run(action, control) {
  if (busy) return;
  busy = true;
  control.disabled = true;
  feedback.textContent = "";
  try {
    await action();
  } catch (error) {
    feedback.textContent = error.message;
  } finally {
    busy = false;
    control.disabled = false;
  }
}

function button(label, action) {
  const node = element("button", label);
  node.type = "button";
  node.onclick = () => run(action, node);
  return node;
}

function field(form, label, name, limit, multiline = false) {
  const wrapper = element("label", label);
  const input = element(multiline ? "textarea" : "input");
  input.name = name;
  input.maxLength = limit;
  input.required = true;
  wrapper.append(input);
  form.append(wrapper);
  return input;
}

function submit(form, label, action) {
  const control = element("button", label);
  form.append(control);
  form.onsubmit = event => {
    event.preventDefault();
    run(() => action(new FormData(form)), control);
  };
}

function checkedText(value) {
  const text = value.trim();
  if (!text) throw new Error("请填写内容。");
  if (/(?:\d[\s()+.\-]*){7,}/.test(text)) throw new Error("请先去掉看起来像电话号码的内容。");
  return text;
}

function handoffForm(key, message, intent, previous, result) {
  const labels = {new: "请 Agent 起草", continue: "补充这条消息", revise: `修改这次交接的第 ${result?.n} 版`};
  const details = element("details");
  details.append(element("summary", labels[intent]));
  const form = element("form");
  field(form, "本次允许交给 Agent 的家长消息摘要", "background", 3000, true).value = message.summary;
  const ask = field(form, "这次的写作要求（请去掉个人信息）", "ask", 2000, true);
  if (intent === "new") ask.value = "根据摘要起草一段家长回复。";
  const about = intent === "revise" ? `原对话中这次交接对应的第 ${result.n} 版回复` : "原对话中的这条家长消息";
  if (intent === "revise") form.append(element("p", "请在写作要求里用不含个人信息的文字说明要改哪一段。"));
  if (intent !== "new") form.append(element("p", "去向是原对话。请按下方原对话位置找回。"));
  submit(form, "确认这份材料并生成交接", async data => {
    const background = checkedText(data.get("background"));
    const request = checkedText(data.get("ask"));
    if (!confirm(`请确认这一次可交给 Agent 的内容，且其中没有学生全名、家长电话、学生代号或其他个人信息。\n\n摘要：${background}\n\n写作要求：${request}`)) return;
    await api.handoff({target: "parent", intent, about: intent === "new" ? "" : about,
      ref: key, background, ask: request, expect: "一段可供我确认的回复草稿。",
      confirmAfter: true, continues: previous?.id || ""});
    await load(key);
  });
  details.append(form);
  return details;
}

function handoffView(key, message, handoff) {
  const view = element("details");
  view.open = true;
  const unknown = handoff.status === "已发送" && !handoff.unknown ? "（最新情况未知）" : "";
  const status = handoff.status === "已完成" ? "已记录发给家长" : api.statusText(handoff) + unknown;
  const label = {new: "起草回复", continue: "补充消息", revise: "修改回复"};
  view.append(element("summary", `${label[handoff.intent]} · ${status}`));
  view.append(element("p", handoff.intent === "new" ? "请粘贴到一个新的 Agent 对话。" : "请粘贴到这条消息原来的 Agent 对话。"));
  if (handoff.note) view.append(element("p", handoff.note));
  view.append(element("pre", handoff.text), button("复制交接内容", async () => {
    if (!await api.copyText(handoff.text)) throw new Error("复制失败，请选中文字手动复制。");
    feedback.textContent = "已复制交接内容。";
  }));
  if (handoff.status === "需手动完成") {
    view.append(button("已粘贴到 Agent 对话", async () => {
      await api.markPasted(handoff.id);
      await load(key);
    }));
  }
  if (["已发送", "待确认"].includes(handoff.status)) {
    const form = element("form");
    field(form, "从该 Agent 对话复制草稿到这里（先去掉个人信息）", "result", 8000, true);
    submit(form, "保存带回的草稿", async data => {
      await api.addResult(handoff.id, checkedText(data.get("result")));
      await load(key);
    });
    view.append(form);
  }
  if (handoff.status === "已发送") {
    const form = element("form");
    field(form, "Agent 明确给出的失败信息（去掉个人信息）", "reason", 200);
    submit(form, "记录失败", async data => {
      await api.markFailed(handoff.id, checkedText(data.get("reason")));
      await load(key);
    });
    const failure = element("details");
    failure.append(element("summary", "Agent 明确报错了"), form);
    view.append(failure);
  }
  if (handoff.status !== "需手动完成" && handoff.intent === "new") {
    view.append(handoffForm(key, message, "continue", handoff));
  }
  for (const result of handoff.results) {
    view.append(element("p", `第 ${result.n} 版 · ${result.source}`), element("pre", result.text));
    view.append(handoffForm(key, message, "revise", handoff, result));
  }
  const evidence = element("details");
  evidence.append(element("summary", "状态依据"));
  for (const event of handoff.events) evidence.append(element("p", `${event.status} · ${event.evidence}`));
  view.append(evidence);
  return view;
}

function messageView(key, message, open) {
  const article = element("article");
  const details = element("details");
  details.open = open;
  const date = new Date(message.created).toLocaleDateString("zh-CN");
  details.append(element("summary", `${message.student} · ${date} · ${message.summary.slice(0, 36)}`));
  details.append(element("pre", message.summary));
  const handoffs = state.handoffs.filter(item => item.ref === key);
  const sent = state.records[`sent-${key}`];
  if (sent) {
    details.append(element("p", sent.reply ? "已发给家长 · 你记下的回复" : "已发给家长 · 你确认已发送"));
    if (sent.reply) details.append(element("pre", sent.reply));
  }
  const location = state.records[`where-${key}`];
  if (location) details.append(element("p", "原对话位置（仅本机） · " + location.text));
  if (handoffs.some(item => item.status !== "需手动完成") && !location) {
    const form = element("form");
    field(form, "原对话位置（仅本机，例如去掉个人信息的标题和日期）", "location", 200);
    submit(form, "记下原对话位置", async data => {
      await api.saveRecord(`where-${key}`, {text: checkedText(data.get("location"))});
      await load(key);
    });
    details.append(form);
  }
  if (!handoffs.some(item => item.intent === "new" && item.status !== "失败")) {
    details.append(handoffForm(key, message, "new"));
  }
  for (const handoff of handoffs) details.append(handoffView(key, message, handoff));
  if (!sent) {
    const done = element("details");
    done.append(element("summary", "我已在工作台之外回了家长"));
    const form = element("form");
    field(form, "实际回复（可留空，请先去掉个人信息）", "reply", 8000, true).required = false;
    submit(form, "记为已发给家长", async data => {
      const reply = data.get("reply").trim();
      if (reply) checkedText(reply);
      if (!confirm("确认你已经把这条回复发给家长了吗？")) return;
      await api.saveRecord(`sent-${key}`, {reply});
      const reviewed = handoffs.findLast(item => item.status === "待确认");
      if (reviewed) {
        try {
          await api.markDone(reviewed.id, "你确认已回复家长");
        } finally {
          await load(key);
        }
      } else await load(key);
    });
    done.append(form);
    details.append(done);
  }
  article.append(details);
  return article;
}

function render(open) {
  const messages = Object.entries(state.records)
    .filter(([key]) => key.startsWith("msg-"))
    .sort((a, b) => b[1].created.localeCompare(a[1].created));
  function show(container, entries, empty) {
    container.replaceChildren(...(entries.length
      ? entries.map(([key, message]) => messageView(key, message, key === open))
      : [element("p", empty)]));
  }
  show(pending, messages.filter(([key]) => !state.records[`sent-${key}`]), "目前没有记下还没回的消息。");
  const term = search.value.trim().toUpperCase();
  show(historyList, term ? messages.filter(([, message]) => message.student === term) : [],
    term ? "这个代号还没有记录。" : "输入学生代号，查看该学生的消息和回复。");
}

async function load(open) {
  state = await api.state();
  render(open);
}

search.oninput = () => { if (state) render(); };
submit(document.querySelector("#new-message"), "存到本机", async data => {
  const student = data.get("student").trim().toUpperCase();
  if (!/^[A-Z]{1,8}[0-9]{1,6}$/.test(student)) throw new Error("请使用字母加数字的学生代号，例如 S01。");
  const summary = checkedText(data.get("summary"));
  const key = "msg-" + crypto.randomUUID();
  await api.saveRecord(key, {student, summary, created: new Date().toISOString()});
  document.querySelector("#new-message").reset();
  await load(key);
});
load().catch(error => { feedback.textContent = error.message; });
