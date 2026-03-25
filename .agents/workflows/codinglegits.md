---
description: This file synthesises the documented philosophies, habits, and hard principles of 11 legendary programmers into actionable rules for the antigravity codebase. These are not soft suggestions — they are distilled from decades of craft at the absolute 
---

# ⚡ Antigravity — Coding Style Legends

> This file synthesises the documented philosophies, habits, and hard principles of 11 legendary programmers into actionable rules for the antigravity codebase. These are not soft suggestions — they are distilled from decades of craft at the absolute frontier of the discipline.
>
> When in doubt about an approach, ask: **which legend's instinct applies here?** Then follow it.

---

## How to Use This File

Each legend section has:
- **Their core worldview** — the lens they see code through
- **Concrete rules** — things you must do or never do in this project
- **The signature question** — the one question to ask yourself before shipping

At the bottom is a **Composite Checklist** — the synthesis of all 11 philosophies into a single pre-commit gate.

---

## 🔥 John Carmack
*Co-founder of id Software. Creator of Doom, Quake. CTO of Oculus. Principal Researcher at Keen Technologies.*

### Worldview
Performance is not a feature — it is a constraint. Code is a social artifact shaped by human fallibility, and you must design against that. Make state explicit. Make bugs structurally impossible, not just unlikely. A pure function is the most trustworthy unit of code that exists.

### Rules
- **Prefer pure functions.** A function that only reads its inputs and returns a value, touching no external state, is thread-safe, trivially testable, and trivially reusable. Chase purity aggressively — every function you convert is a class of bugs you have eliminated forever.
- **Make state mutations visible.** If a function mutates state, that mutation should be obvious at the call site — not buried inside four layers of abstraction. You should be "made constantly aware of the full horror of what you are doing."
- **Use static analysis religiously.** If something can syntactically be entered incorrectly, it eventually will be. Run the linter, the type checker, and any static analysis tool on every commit. Fix every warning — not most of them.
- **Worst-case performance > average-case performance.** Highly variable execution time is a bug. An operation that is usually fast but occasionally catastrophic is worse than one that is uniformly moderate. Design for the slow path.
- **No exceptions. No implicit constructors. Minimal templates.** Use a tight, deliberate subset of the language. Complexity added in the name of flexibility is the root of most development problems. C++ features that exist to be clever are not your friends.
- **Inline for awareness, extract for purity.** Inline state-mutation-heavy code so you cannot ignore it. Extract pure logic into tested functions so you can trust it.

### Signature Question
> *"Is the state this function touches explicit — and if it mutates something, is that mutation impossible to miss?"*

---

## 🚀 ThePrimeagen (Michael Paulson)
*Senior Engineer at Netflix. Neovim core contributor. Streamer, educator.*

### Worldview
The editor is not a tool, it is an extension of thought. Every millisecond of friction between intention and execution accumulates into thousands of hours of lost focus over a career. Reduce cognitive overhead relentlessly. Build muscle memory for the things you do constantly. Ship boring solutions fast and refine — don't architect in advance of the problem.

### Rules
- **Use vim motions everywhere.** Even if you are not using Neovim, install a vim extension in your editor. The goal is editing at the speed of thought — no reaching for the mouse, no hunting for a menu.
- **Use git worktrees, not stash.** Stashing is a trap. Worktrees let you maintain multiple branches checked out simultaneously without cognitive overhead of tracking what you've half-done. Use them.
- **Navigate by harpoon, not by search.** In any given feature, you visit 3–5 files repeatedly. Pin those files. Stop searching for what you know you need. (`harpoon` for Neovim, equivalent bookmarks elsewhere.)
- **Boring solutions ship.** The clever solution is not the fast solution. The fast solution is the one you can implement, test, and understand in the next 30 minutes. Reach for boring first.
- **Data structures are the interview.** If you can't draw the data structure your feature depends on on a whiteboard before you write a single line, you don't understand the problem yet. Draw it first.
- **tmux/terminal-first workflow.** Your IDE should be a thin layer over your terminal. If your workflow requires a GUI for something that a shell command does in 200ms, fix your workflow.
- **No cognitive debt.** If you're about to context-switch away from a task, leave it in a state that costs zero brain cycles to return to: tests passing, branch clean, next step written as a comment or issue.

### Signature Question
> *"If I had to context-switch right now, would returning to this cost me zero mental overhead?"*

---

## 🎯 TJ Holovachuk (TJ DeVaries)
*Author of 500+ open source Node.js packages. Creator of Express, Koa, Apex, and many more. Later moved to Go.*

### Worldview
Simplicity is the ultimate API. A module that does one thing and has a clean interface is infinitely more valuable than a framework that does everything and has a leaky one. The programmer's happiness matters — but not through magic, through clarity. Write Go-like code even in JavaScript: explicit, flat, unsurprising.

### Rules
- **One module, one responsibility — at the file level.** If you have to think for more than two seconds about where a function lives, the module boundary is wrong. Reorganise.
- **APIs should be learnable in under 5 minutes.** If explaining how to use your module requires reading more than a README section, the API is too complex. Kill parameters, collapse concepts, and reduce surface area until it can be taught in a paragraph.
- **Small is beautiful.** A 100-line module that does one thing is more powerful than a 1,000-line framework. Resist the urge to grow a module. Split it before it grows.
- **Explicit over magic.** No metaprogramming that hides what is happening. No clever `__getattr__` tricks, dynamic method generation, or framework magic that makes stack traces unreadable. Every function call in the codebase should be findable by text search.
- **Go-style error handling in any language.** Return errors — don't throw them for recoverable conditions. The caller deserves to know what went wrong without a try/catch. Use discriminated unions (`Result<T, E>`) or Go-style `[value, error]` returns.
- **Delete code mercilessly.** The best code is no code. Before adding, ask if you can solve the problem by removing something instead. Dead code rots. Unused APIs mislead. A smaller codebase is a faster codebase.
- **Contribute back.** If you use something, improve it. Extract reusable utilities into their own modules. Treat every useful abstraction as potentially public — it forces better design.

### Signature Question
> *"Could a developer use this module correctly after reading 10 lines of documentation?"*

---

## 🔓 George Hotz (geohot)
*First person to unlock the iPhone. First person to jailbreak the PS3. Creator of comma.ai and tinygrad.*

### Worldview
Understand the system all the way down. Abstractions are leaky and trust is a liability. The simplest possible implementation that works is almost always the correct one. Ship fast, ship often, let reality tell you what is wrong. Don't ask for permission from the complexity gods — just write the thing.

### Rules
- **Own your stack.** Understand what every major dependency does internally. If you can't explain what happens under the hood of a library you depend on, you are at its mercy. Read the source.
- **Minimal dependencies.** Every dependency is a surface area for bugs, security vulnerabilities, breaking changes, and supply chain attacks. Add dependencies only when the cost of writing it yourself is genuinely prohibitive.
- **The simplest implementation that could possibly work.** When in doubt, write the flat, naive, direct version. No design patterns, no frameworks, no clever abstractions. Make it work first. The complex version is the refactor — if it ever even becomes necessary.
- **If it takes more than 500 lines to explain, it's wrong.** tinygrad implements a neural network framework in ~1,000 lines. If your solution is ballooning, you have not understood the problem yet. Re-approach.
- **Ship and iterate — don't architect.** The best design comes from feedback from reality, not from a whiteboard. A working prototype that ships in 3 days beats a perfect design that ships in 3 months.
- **Think like the attacker.** For any system you build, spend time trying to break it, circumvent it, or exploit it before you ship. If you find a way in, so will someone else.
- **Comment the why of non-obvious decisions.** The code you write at 3am when you finally understand something is incomprehensible to you at 10am. Leave a breadcrumb explaining the insight — one line is enough.

### Signature Question
> *"Do I actually understand every layer of this, all the way down — or am I trusting an abstraction I haven't read?"*

---

## 🌐 Jeff Dean
*Google Senior Fellow. Lead architect of MapReduce, Bigtable, Spanner, TensorFlow, Google Brain.*

### Signature Latency Numbers (commit these to memory)

```
L1 cache reference                          0.5 ns
Branch mispredict                           5   ns
L2 cache reference                          7   ns
Mutex lock/unlock                          25   ns
Main memory reference                     100   ns
Compress 1K bytes with Zippy            3,000   ns  (3 µs)
Send 1K bytes over 1 Gbps network      10,000   ns  (10 µs)
Read 4K randomly from SSD             150,000   ns  (150 µs)
Read 1 MB sequentially from memory    250,000   ns  (250 µs)
Round trip within same datacenter     500,000   ns  (500 µs)
Read 1 MB sequentially from SSD     1,000,000   ns  (1 ms)
Disk seek                          10,000,000   ns  (10 ms)
Read 1 MB sequentially from disk   20,000,000   ns  (20 ms)
Send packet CA → Netherlands → CA 150,000,000   ns  (150 ms)
```

### Worldview
Scale is not something you add later — it is something you design for from the beginning, or pay for dearly when you retrofit it. Distributed systems fail in ways that local systems never do. Every decision at scale is a tradeoff between latency, throughput, consistency, and availability. Back-of-envelope estimation is a superpower.

### Rules
- **Back-of-envelope before you build.** Before implementing any feature involving I/O, data volume, or concurrency, estimate: How many requests per second? How many bytes? What latency budget? What happens at 10x load? Write the numbers down.
- **Design for failure, not success.** Any service, dependency, or network call will fail. What happens to the user when it does? Design the degraded experience first, the happy path second.
- **Know your access patterns.** Reads vs writes ratio, hot keys, data size distribution — these determine whether a design will hold at scale or collapse. Profile before you optimise, but design for the known pattern.
- **Distinguish between latency and throughput.** Optimising for one often harms the other. Know which you need. A batch pipeline needs throughput. A user-facing API needs latency.
- **Idempotency is non-negotiable.** Every operation that can be retried (network calls, queue consumers, webhook handlers) must be idempotent. Assume it will be called twice.
- **Avoid synchronous blocking on the critical path.** If something can be done asynchronously without compromising correctness, do it asynchronously. Never block a user-facing operation on a non-critical side effect.
- **Instrument everything.** You cannot reason about a system you cannot observe. Every service needs: request count, error rate, latency percentiles (p50, p95, p99), and saturation. Without these you are flying blind.

### Signature Question
> *"What happens to this system at 10x the expected load — and have I estimated the numbers?"*

---
