<p align="right"><a href="README.md">简体中文</a> &nbsp; / &nbsp; <strong>English</strong></p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/wordmark-dark.svg">
    <img src="docs/images/wordmark.svg" alt="Chat to Desk" width="440">
  </picture>
</p>

<p align="center">Turn the work in your chats into a personal workbench.</p>

<p align="center"><a href="#-get-started"><strong>Get started</strong></a> &nbsp; · &nbsp; <a href="#-how-you-work">How it works</a> &nbsp; · &nbsp; <a href="#-see-it-in-action">See it in action</a></p>

<br>

<p align="center">
  <img src="docs/images/hero-rounded.png" alt="Scattered conversations become connected work items and a personal workbench" width="640">
</p>

<br>

You work with agents like Codex and Claude, and conversations pile up. You repeat the same context, lose track of last week's results, and struggle to see what still needs doing.

**Chat to Desk** helps your own agent review the conversations you authorize, understand your everyday work and recurring methods, and open a personal panel inside a **coordinating conversation**. You stay in that conversation while it coordinates the overall workflow.

The panel turns familiar work into convenient actions. Choose an item, provide what's changed, and let the appropriate agent continue. Progress and results return to the panel; discuss changes to your plans in the coordinating conversation.


<br>

## 🧭 How you work

**One conversation coordinates the work, a panel provides everyday controls, and execution conversations handle individual tasks.** The workbench is your way to manage work within that conversation, not a standalone app handed over separately from it.

| Where | What happens |
| --- | --- |
| **Coordinating conversation** | Understands your work, organizes workflows, and coordinates execution. Add requirements and adjust plans here. |
| **Work panel** | Opens within the coordinating conversation. Shows actions, current progress, latest conclusions, and next steps for each work item. |
| **Execution conversations** | Handle specific work in the appropriate projects. Established work continues in its original conversation. |

Start an action in the panel. The relevant conversation works in the background, and progress and results return to the same item. You can stay in the coordinating conversation without switching between chats or copying tasks around.

<br>

## ✨ Get started

**Send this prompt in the conversation you want to use to coordinate your work.** Let your agent handle installation and panel setup.

```text
Please install $chat-to-desk from this repository:
https://github.com/superNos/personal-agent-workbench-skill

First ask which conversation history you may read, then identify
my everyday work and reusable workflows. Use this conversation to coordinate
my work, and open my personal work panel here. Start with one recurring
task we choose together. Add its action to the panel, connect it to the
appropriate project and execution conversation, and bring progress
and results back to that work item automatically.
```

Already using `$personal-agent-workbench`? Ask your agent to replace the old skill with `$chat-to-desk`, keeping your personal workbench and data. The repository URL is unchanged.

You can start with a small scope:

> Only review my event-planning conversations from the past month. Skip private chats. First, tell me which tasks you think I keep repeating.

The agent in your coordinating conversation checks its understanding and the proposed panel with you, then opens the panel, connects the execution conversation, and verifies one real task. You don't need to design the interface or write a requirements document first.

<br>

## 🗓️ See it in action

Suppose you often compare venue quotes. Your assistant turns **availability, total cost including equipment, and cancellation deadlines** into a reusable action. It also carries forward a preference you confirmed: “Flag missing cancellation terms. I'll ask the venue.”

New quotes? Compare them. A revised quote? Continue the same event.

![Illustrative venue workbench: compare availability, total costs and cancellation deadlines, flag missing terms, and update the same work item when a revised quote arrives. All venues and prices are fictional.](docs/images/venue-workflow.en.png)

Everyone's workbench can be different. If you often revise drafts, your flow might be “Open a document → Choose a version → Leave feedback.” Its features come from how you actually work.

<br>

## 🧭 How it understands your work

### 1. Find the work across authorized history

Review all accessible conversations within the agreed scope and connect those that serve the same goal. Casual questions don't automatically become tasks. Completed items leave the active view, with results retained as authorized.

### 2. Turn recurring methods into useful actions

A method becomes a reusable action when it appears across separate work items, or when you explicitly say you'll use it again. Stable context, steps, and output requirements become part of the feature. You provide what changes this time.

### 3. Coordinate in one conversation, act from the panel

The coordinating conversation organizes work items, workflows, and execution conversations. Once the panel is connected, actions run in the background in the appropriate project and conversation. Progress and conclusions return to the corresponding execution record. Established work stays in its original conversation; separate conversations can run in parallel, while work in the same conversation is queued.

<br>

## 🔐 You set the boundaries

- **What it reads.** Choose sources, dates, and exclusions. You can also provide just a few work samples.
- **What it saves.** Findings stay in the current conversation by default. Saving requires permission and a designated local folder. Personal workbenches and real records stay out of the public skill repository.
- **What it does externally.** Sending, submitting, publishing, paying, or deleting requires explicit authorization for that action. Past habits aren't permission.

A local workbench does not change how your AI provider handles data. Read the [privacy and security notes](SECURITY.md) (Chinese).

<br>

## 💬 Common questions

<details>
<summary><strong>Can I use this without being a developer?</strong></summary>

Yes. You decide which conversations are available, whether the assistant understands your work, and whether the interface feels useful. Your agent handles implementation and setup. Your environment must support an in-conversation panel and allow your agent to read and write local files and run the local service behind that panel.

</details>

<details>
<summary><strong>Does it support Codex, Claude, and WorkBuddy?</strong></summary>

The maintainer has tried it in Codex. The same work-analysis approach can be used in Claude, WorkBuddy, and other environments, but in-conversation panels, history access, background execution, and result updates depend on the host's capabilities. Your assistant checks those connections first. Unconnected actions explain what's missing instead of pretending work has started.

</details>

<details>
<summary><strong>What if history is unavailable, or I don't want to share it all?</strong></summary>

Limit access to a project, date range, or set of conversations. Alternatively, provide work samples or describe how you usually handle a task. Your assistant identifies its sources and gaps; it doesn't treat inaccessible history as reviewed.

</details>

<details>
<summary><strong>Does everyone get the same workbench?</strong></summary>

No. There is no required set of task cards, boards, or charts. Occasional work may need only a way to start a task and retrieve results. Stable workflows can become dedicated features later.

</details>

<br>

## Documentation and contributions

[Skill instructions](skills/chat-to-desk/SKILL.md) · [Contributing](CONTRIBUTING.md) · [Changelog](CHANGELOG.md) · [Report an issue](https://github.com/superNos/personal-agent-workbench-skill/issues)

The linked supporting documents are currently in Chinese. Examples use fictional data. Please keep real conversations, personal information, and credentials out of public issues. Follow the [security notes](SECURITY.md) to report vulnerabilities privately.

[MIT License](LICENSE)
