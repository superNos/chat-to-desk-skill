const api = window.Workbench;
const list = document.querySelector('#questions');
const feedback = document.querySelector('#feedback');
const openQuestions = new Set();

function element(parent, tag, text) {
  const node = document.createElement(tag);
  node.textContent = text ?? '';
  parent.append(node);
  return node;
}

async function action(button, run) {
  button.disabled = true;
  feedback.textContent = '';
  try {
    await run();
  } catch (error) {
    feedback.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function button(parent, text, run) {
  const control = element(parent, 'button', text);
  control.onclick = () => action(control, run);
}

function field(parent, label, tag = 'textarea') {
  const input = element(element(parent, 'label', label), tag);
  input.maxLength = 8000;
  return input;
}

function renderHandoff(parent, handoff, replied, route) {
  const block = element(parent, 'section');
  element(block, 'strong', api.statusText(handoff) + (handoff.status === '已发送' && !handoff.unknown ? '（最新情况未知）' : ''));
  element(block, 'p', handoff.intent === 'new' ? '请粘贴到 Agent 平台的新对话' : '请粘贴到原对话 · ' + (route || '定位提示未保存'));
  if (handoff.note) element(block, 'p', handoff.note);
  const details = element(block, 'details');
  element(details, 'summary', '交接内容与状态记录');
  element(details, 'pre', handoff.text);
  for (const event of handoff.events) {
    element(details, 'p', Object.values(event).join(' · '));
  }
  button(details, '复制交接', async () => {
    if (await api.copyText(handoff.text)) feedback.textContent = '已复制';
  });
  if (handoff.status === '需手动完成') {
    button(block, '已粘贴', async () => {
      await api.markPasted(handoff.id);
      await render();
    });
  }
  for (const result of handoff.results) {
    element(block, 'p', '你粘贴回来的结果 · 第 ' + result.n + ' 版');
    element(block, 'pre', result.text);
  }
  if (handoff.status !== '需手动完成' && !replied) {
    const result = field(block, '从对应 Agent 对话粘贴结果');
    button(block, '带回结果', async () => {
      if (!result.value.trim()) throw new Error('请先粘贴结果。');
      await api.addResult(handoff.id, result.value.trim());
      await render();
    });
  }
}

function renderQuestion(ref, question, handoffs, records) {
  const row = element(list, 'article');
  const latest = handoffs.at(-1);
  const replied = latest?.events.some(event =>
    event.evidence === '你确认：我已本人转发给同事');
  const panel = element(row, 'details');
  panel.open = openQuestions.has(ref);
  panel.ontoggle = () => panel.open ? openQuestions.add(ref) : openQuestions.delete(ref);
  element(panel, 'summary', question.title + ' · ' + question.metric + ' · ' + (replied ? '已答复' : '待答复'));
  for (const item of [...handoffs].reverse()) renderHandoff(panel, item, replied || item !== latest, records['route-' + item.id]);
  if (latest?.status === '待确认' && !replied) {
    button(panel, '标为已答复', async () => {
      if (!confirm('你已经亲自把这条答复转发给同事了吗？')) return;
      await api.markDone(latest.id, '我已本人转发给同事');
      await render();
    });
  }
  const intent = element(element(panel, 'label', '这次要做什么'), 'select');
  for (const [value, label] of [['new','新开查询'], ['continue','接着查'], ['revise','修改结果']]) {
    if (value !== 'new' && !latest) continue;
    if (value === 'revise' && !latest.results.length) continue;
    element(intent, 'option', label).value = value;
  }
  if (latest) intent.value = 'continue';
  const destination = field(panel, '继续或修改时，填原 Agent 对话的标题或定位提示（仅本机）', 'input');
  const where = element(panel, 'p');
  const updateDestination = () => {
    destination.disabled = intent.value === 'new';
    where.textContent = destination.disabled ? '去向：Agent 平台的新对话' : '去向：你指定的原 Agent 对话';
  };
  intent.onchange = updateDestination;
  updateDestination();
  const definition = field(panel, '这次交接的指标口径');
  if (question.metric === '活跃会员') definition.value = '活跃会员指近 30 天有消费的会员';
  const idea = field(panel, '查询思路（只写方法和条件）');
  button(panel, '确认并生成交接', async () => {
    if (!idea.value.trim()) throw new Error('请填写查询思路。');
    if (!destination.disabled && !destination.value.trim()) throw new Error('请先指定原 Agent 对话。');
    if (!confirm('请确认这次交接只含指标口径和查询思路。\n\n' + definition.value + '\n' + idea.value)) return;
    const handoff = await api.handoff({
      target: 'agent', intent: intent.value, ref,
      about: destination.disabled ? '' : '原查询',
      background: definition.value.trim(), ask: idea.value.trim(),
      expect: '请给出查询结果。', confirmAfter: true,
      continues: destination.disabled ? undefined : latest.id,
    });
    const id = handoff.handoff?.id ?? handoff.id;
    if (id && !destination.disabled) await api.saveRecord('route-' + id, destination.value.trim());
    openQuestions.add(ref);
    await render();
  });
}

async function render() {
  const state = await api.state();
  const handoffs = Array.isArray(state.handoffs) ? state.handoffs : Object.values(state.handoffs);
  list.replaceChildren();
  const questions = Object.entries(state.records).filter(([key]) => key.startsWith('question-')).reverse();
  if (!questions.length) element(list, 'p', '还没有问题。可以从活跃会员或复购率开始。');
  for (const [ref, question] of questions) {
    const items = handoffs.filter(item => item.ref === ref);
    renderQuestion(ref, question, items, state.records);
  }
}

document.querySelector('#new').onsubmit = event => {
  event.preventDefault();
  const form = event.currentTarget;
  return action(event.submitter, async () => {
    const data = new FormData(form);
    const title = data.get('title').trim();
    if (!title) throw new Error('请填写问题名称。');
    const id = 'question-' + crypto.randomUUID();
    await api.saveRecord(id, {title, metric: data.get('metric')});
    openQuestions.add(id);
    form.reset();
    await render();
  });
};
render().catch(error => { feedback.textContent = error.message; });
