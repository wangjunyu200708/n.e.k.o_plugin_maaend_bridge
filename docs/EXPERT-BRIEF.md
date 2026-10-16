# MaaEnd Bridge Plugin — Project Brief

*A third-party N.E.K.O. plugin. Written 2026-10-16, revised the same day after the
two blocking bugs were found and fixed; the revision is marked throughout.*

---

## 1. What this is

A plugin for **N.E.K.O.** (a desktop AI-companion app — Python 3.11 / FastAPI backend,
Electron + web frontend, v0.9.0.1 on Windows; https://github.com/Project-N-E-K-O).

It bridges an **N.E.K.O. session** to a **self-built automation executor for the game
*Arknights: Endfield***, so the in-app character can be told "go run my dailies" and
narrate live progress.

The executor is derived from the open-source **MaaEnd** project (MaaFramework-based
Endfield automation, AGPL-3.0). Upstream ships **MXU**, a Tauri + React desktop host;
this project **replaces MXU with its own Python control-panel backend**, embedded in
the plugin.

| | |
|---|---|
| Plugin repo | `E:\n.e.k.o_plugin_maaend_bridge` (standalone Git repo, public on GitHub, AGPL-3.0 per upstream, MIT for our own code) |
| Host app source | `E:\NEKOdm\N.E.K.O` |
| Client under test | `F:\N.E.K.O\N.E.K.O_v0.9.0.1_win` |
| Build artifact | `dist/maaend_bridge-0.1.0.neko-plugin`, 333.6 MiB, sha256 `45080217…8108e` |

---

## 2. Architecture

```
N.E.K.O. client (Electron / web)
  │
  ├─ Plugin platform (plugin/core/, plugin/server/)         ← host side, not ours
  │    ├─ loads plugin.toml manifest
  │    ├─ runs each plugin in its own subprocess ("plugin host")
  │    └─ renders hosted-TSX panels inside a sandboxed iframe
  │
  └─ maaend_bridge plugin  (our code)
       ├─ __init__.py        façade: @neko_plugin, 10 @plugin_entry + @ui.action,
       │                     2 @llm_tool, one @ui.context(id="maaend")
       ├─ service.py         semantics: connection state machine, task bookkeeping,
       │                     token rotation, narration. No SDK import.
       ├─ client.py          transport only: typed HTTP over loopback. No SDK import.
       ├─ launcher.py        finds/launches the executor, probes interpreters
       ├─ prompts.py         8-locale phrase tables
       ├─ surfaces/maaend.tsx   the whole UI (hosted TSX, ~845 lines)
       ├─ i18n/{8 locales}.json
       └─ executor/          ← shipped inside the plugin, self-contained
            ├─ panel_server.py   our control-panel backend (port 12702)
            ├─ runner.py         MaaFramework driver
            ├─ override/         option → pipeline_override compiler (Python port)
            ├─ vendor/           embeddable CPython 3.11.9 + maa/numpy/Pillow (~149 MiB)
            └─ maaend/           the game resource layer (AGPL-3.0, unmodified)
                 ├─ interface.json, tasks/, resource/, locales/, data/
                 ├─ agent/       go-service.exe + cpp-algo.exe
                 └─ maafw/       the native runtime those two agents dlopen
```

**Runtime flow:** browser panel → `props.api.call('maaend_status')` → N.E.K.O. host →
plugin subprocess → `service.py` → loopback HTTP → `executor/panel_server.py` (a
separate, *elevated* Python process) → MaaFramework → synthetic input to the game window.

**Why a separate elevated process:** the executor drives the game via Seize/SendInput,
which needs administrator rights. The plugin subprocess is not elevated, so launching
the executor means `ShellExecuteW(..., "runas", ...)` — a UAC prompt — and then talking
to it over loopback HTTP.

**Panel auth:** the panel writes a freshly generated token to
`executor/panel_token.txt` on every start; the plugin re-reads it per request and treats
a token change as proof the panel restarted.

---

## 3. Platform constraints that shape the code

- **Hosted TSX panels** run in a sandboxed iframe. No npm packages, no `fetch`. The
  only channel out is `props.api.call(actionId, args)`.
- Which actions a panel may call is **whitelisted by the host**; anything else is
  rejected with HTTP 403 `action_not_exposed`.
- `plugin.toml` UI surfaces declare `kind` (`panel` vs `guide`/`docs`) and `mode`
  (`static` is the **default**; hosted TSX must set `mode = "hosted-tsx"` explicitly,
  or the `.tsx` file is served as a static asset and the iframe renders raw TypeScript).
- A plugin's `@ui.context` handler runs **under a host watchdog of about 5 seconds**.
  Blowing it does not surface as a timeout — it surfaces as
  `Failed to query plugin UI action context`, with nothing about the panel being down.
- Plugin identity is rigid: `plugin_id`, folder name, `[plugin].id`/`name`/`entry`, and
  the entry class must all derive from one name.
- **(added in revision)** Under the plugin market's verify workflow, `ruff` runs with
  `--isolated` and `--ignore-noqa`, so neither `ruff.toml` nor `# noqa` can suppress
  anything. Style exceptions are simply not available.
- **(added in revision)** An embeddable CPython with a `._pth` file is isolated:
  `PYTHONPATH` is ignored, **and the running script's own directory is not on
  `sys.path`** (`sys.path[0]` is the stdlib zip; the `.` entry means the interpreter's
  directory). Anything a script needs importable must be named in the `._pth`.

---

## 4. What works

- Executor migrated into the plugin and fully self-contained: no external Python, no
  Node at runtime (MXU's TypeScript option compiler was ported to Python, guarded by a
  golden suite — 11/11 snapshots match), no dependency on any `.workbuddy` path.
- Game resource layer vendored (5809 nodes; content hash `8a3b256b99ec48e1`).
- Self-contained Python runtime vendored and verified from a clean clone (149.3 MiB).
- AGPL-3.0 compliance: per-subtree licence table, `NOTICE.md`, upstream `SOURCE.txt`
  with per-file sha256.
- Published to a public GitHub repo.
- Verified after the fixes below: `ruff` gate clean (was 41 errors), plugin suite
  206 passed / 14 skipped / 0 failed, live-panel contract tests 9/9 against a running
  executor, compiler golden tests 11/11, `/api/prepare` returns ready in 3.3 s,
  `/api/screenshot` returns a 521 KB PNG in 0.57 s.

---

## 5. What was actually wrong (both found and fixed on 2026-10-16)

### 5.1 The executor could not start — `maafw/` was not bundled

**Symptom.** Pressing **"Start executor"** in the panel did raise UAC and did launch a
process, and the plugin could even reach the panel's `/api/status`. But **"Initialise"**
and **"Screenshot"** both hung and surfaced as a *network* error — which sent the
investigation after timeouts and connection handling for a while.

**Root cause**, from `executor/run_logs/go-service.stdout.log`:

```
FTL github.com/MaaXYZ/MaaEnd/agent/go-service/agent.go:35 > Failed to initialize MAA framework
error="failed to load library \"MaaFramework\" (path:
  \"…\plugins\maaend_bridge\executor\maaend\maafw\MaaFramework.dll\"):
  The specified module could not be found."
```

There was **no `maafw/` directory at all**. The resource layer had been synced from the
upstream MaaEnd distribution with `maafw` explicitly excluded, on this reasoning, which
was recorded in `executor/maaend/SOURCE.txt`:

> `maafw` — native DLLs; the pip 'maa' wheel already ships the same set (verified: 17 DLLs incl. MaaFramework.dll)

That is true about the bytes — the two sets **are byte-identical** — and false about the
semantics. The two consumers resolve the library by different rules:

- the Python `maa` binding looks **beside itself**, in `site-packages/maa/bin/`;
- `go-service.exe` and `cpp-algo.exe` look in **`<resource layer>/maafw/`**, which is
  where upstream puts it.

So the agents died at startup while the Python side was perfectly happy. And the
*network* error was a second-order effect: `ensure_ready()` holds an `RLock` while it
waits on the agent, the HTTP request blows the plugin's 90 s budget, and the screenshot
request queues behind the same lock and blows its 20 s budget — one cause, two symptoms.

**Fix.** Sync `maafw/` as part of the asset layer (upstream's `debug/` and `config/`,
which are the upstream author's own run state, are excluded), and document why the
duplication is deliberate in `SOURCE.txt`, `README.md`, `executor/README.md` and
`executor/vendor/README.md` so nobody "optimises" it away again.

**Verified.** `/api/prepare` → `HTTP 200` in 3.34 s, `agents: go-service alive,
cpp-algo alive`, 5809 nodes, 16:9, elevated. `/api/screenshot` → `HTTP 200`, 521 KB,
0.57 s. `go-service.stdout.log` no longer grows.

### 5.2 The repo's CI was red — 41 ruff errors, and a misread command line

**Symptom.** `Verify N.E.K.O Plugin: All jobs have failed`, in 30–40 s.

The failing step is:

```
uvx ruff==0.12.4 check --ignore-noqa --isolated --target-version py311 --line-length 120 \
    --select E4,E7,E9,F,I --exclude vendor plugin-repo
```

Note that `plugin-repo` here is **a positional path argument, not a second exclude
pattern** — `--exclude` takes one value. So the gate lints the plugin repository itself
(after the intended `vendor` exclusion), not the host checkout. 41 errors:

| Rule | Count | Cause |
|---|---|---|
| E402 | ~29 | scripts called `sys.path.insert(...)` before importing, so every later import was "not at top of file" |
| I001 | ~6 | import blocks unsorted, partly a consequence of the same |
| F401 | 4 | genuinely unused imports |
| F541 | 2 | `f""` with no placeholders |

`--ignore-noqa` is what makes this a real problem rather than a one-line annotation:
the E402s existed precisely because the scripts **need** those paths, and `# noqa: E402`
— which every one of them carried — is void under that flag.

**Fix.** Move the path configuration to where it belongs. All 20 E402s of the
`sys.path.insert` kind disappear once `executor/` and `executor/override/` are listed in
`vendor/python/python311._pth`; the handful in `probe_agent.py` were module-level
constants sitting between imports and simply moved below them. Remaining F401/F541/I001
were fixed directly. `service.py`'s PIL availability probe became
`importlib.util.find_spec("PIL.Image")` — it only ever asked "is PIL there", and a bare
`import` for that question is an unused import no matter how it is annotated.

This is also a strict improvement over the inline version: scripts now import correctly
from **any** working directory, not only when run from `executor/`.

**Verified.** The exact CI command reports `All checks passed!`.

### 5.3 A 403 that is no longer reproducing (unconfirmed)

Earlier, the host rejected two specific panel actions while others in the same surface
succeeded:

```
Hosted UI action rejected: plugin_id=maaend_bridge, surface=panel:maaend,
    action_id=maaend_logs,        reason=action_not_exposed
Hosted UI action rejected: plugin_id=maaend_bridge, surface=panel:maaend,
    action_id=maaend_launch_panel, reason=action_not_exposed
```

The authorisation path needs both an entry in the surface's `@ui.context` action list
**and** the id present in `entry_ids`, which is read from the host's **in-memory**
plugin registry (`state.plugins[plugin_id]`), not from the on-disk `plugin.meta.json` —
and the on-disk file was verified to contain all 10 entries with correct ids, as was a
byte-for-byte comparison of the installed and repository `__init__.py`. The suspicion
was a registry entry left over from an earlier package after a re-import without a full
client restart. The two actions that succeeded are exactly the two the panel calls *on
mount*, which also fits a "the action list is only complete on the first context query"
reading. **The user has since pressed the button successfully, so this is not currently
reproducing, and no root cause is confirmed.** Recorded here so it is not mistaken for
something that was fixed.

---

## 6. The one thing still open: the panel renders text the user calls garbled

Reported as: *"面板乱码（描述文字你按md格式写的？…又不是非要 tsx）"* — the panel shows
gibberish, and the user's own hypothesis is that the descriptive copy was authored in
Markdown and rendered as raw text.

What is **verified clean**, by direct inspection rather than assumption:

1. **Our locale files.** `i18n/zh-CN.json` is valid UTF-8 with correct Chinese
   (`"ui.launchExecutor": "启动执行器"`), and a programmatic scan of every string finds no
   `**`, backticks, `##`, or literal `\n`.
2. **The text the executor supplies.** `GET /api/tasks` returns 46 tasks whose `label`
   and `description` fields are correct Chinese with emoji, e.g.
   `label: "🛒据点交易"`, `description: "启用需要售卖的地区或据点…"`. No mojibake.
3. **Our rendering code.** The panel draws it as `<Text>{task.description}</Text>` —
   plain text, no Markdown, no raw source.

So the corruption, if real, happens between source and screen, and this is the one
question where an outside expert is genuinely more useful than more inspection here.
Candidates, none confirmed:

1. The `.neko-plugin` **build or install** path re-encodes the i18n JSON (source is
   UTF-8; the artifact may not be).
2. The host injects the locale payload into the sandbox with the wrong encoding.
3. `t()` misses its keys, so the panel renders raw key names (`ui.launchExecutor`), and
   a wall of those reads as noise.
4. Something specific to this machine.

The user's design note is worth taking as a requirement regardless: the panel's copy
should be authored in whatever actually renders — HTML if a markup string has to be
embedded — rather than Markdown that a plain-text renderer will show raw.

**What would settle it fastest:** a screenshot of the panel as it appears, and the
host's own log for the same session (`%USERPROFILE%\Documents\N.E.K.O\logs\`). Nothing
in the plugin's or the executor's logs so far shows a rendering fault, which is why this
is the one item still open.

---

## 7. Deliberate design decisions (context, not questions)

- **No SSE.** The panel backend exposes a process-global log bus whose ids restart at 1
  on every restart, with no per-task framing. Everything the plugin needs is in
  `/api/status`, which takes no lock, so the plugin polls instead.
- **The token doubles as instance identity.** No session id exists in the API; a
  token-file change is the only proof that the panel restarted, and therefore the signal
  to discard bookkeeping from the previous process.
- **Two booleans, three outcomes.** `token_ok` and `connected` are tracked separately so
  "the panel is down" and "the panel is up but rejected our token" say different things.
- **A guard was deliberately removed.** An earlier version refused to launch the
  executor when the game resource layer was incomplete. That conflated "can this work"
  with "may this process start" and broke four of the user's own tests; the tests were
  right and the guard was wrong.
- **`executor/maaend/maafw/` is LGPL-3.0, not AGPL-3.0**, and the licence table in
  `NOTICE.md` carves it out: those DLLs are MaaFramework's own binaries travelling with
  the MaaEnd distribution, not MaaEnd code.
