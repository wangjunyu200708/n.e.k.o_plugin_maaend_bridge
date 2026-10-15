/**
 * MaaEnd executor control panel.
 *
 * A hosted TSX surface: it runs in a sandboxed iframe and reaches the plugin
 * only through ``props.api.call``, which needs a matching ``@ui.action`` on the
 * other side. Nothing here talks to the executor directly — its backend is on
 * 127.0.0.1:12702 and its token never reaches the browser.
 *
 * Layout follows MXU 2.7.1's desktop view (the upstream Tauri host this plugin
 * replaces): a wide left column of task cards above a toolbar, a narrow right
 * column with a live screenshot over a log view, and a resizable split between
 * them. Two MXU things are deliberately *not* ported, because this backend has
 * no notion to hang them on:
 *
 * * the tab strip — MXU tabs are saved configurations (selected tasks, option
 *   values, controller, resource, schedules). Here there is one executor
 *   running one task at a time.
 * * the pretask / pre-action / task zones — that split exists because MXU's
 *   backend is a queue that submits one batch. Every task here is independently
 *   runnable, and the catalogue carries a real ``group`` field, so the list is
 *   grouped by that instead.
 *
 * Everything user-visible goes through ``t()`` — the kit's i18n helper, which
 * reads this plugin's ``i18n/<locale>.json`` off ``window.__NEKO_PAYLOAD``.
 * Task names, descriptions and option labels come from the executor itself,
 * already localised on its side, so they are rendered verbatim.
 */
import {
  Accordion,
  Alert,
  Button,
  ButtonGroup,
  Card,
  CheckboxGroup,
  Columns,
  Divider,
  EmptyState,
  Field,
  Heading,
  ImagePreview,
  Inline,
  Input,
  KeyValue,
  LogViewer,
  Modal,
  Page,
  Progress,
  Select,
  Stack,
  StatusBadge,
  Switch,
  Text,
  Tip,
  Toolbar,
  ToolbarGroup,
  t,
  useEffect,
  useRef,
  useState,
} from "@neko/plugin-ui"
import type { PluginSurfaceProps } from "@neko/plugin-ui"

const STATUS_REFRESH_MS = 5000
const LOG_POLL_MS = 2000
const SHOT_AUTO_MS = 2500
const LOG_KEEP = 400
const LOG_SHOW = 120

type OptionInput = {
  name: string
  label?: string
  default?: string
  pipeline_type?: string
  verify?: string
  pattern_msg?: string
}

type OptionCase = { name: string; label?: string; option?: OptionNode[] }

type OptionNode = {
  name: string
  type: string
  label?: string
  description?: string
  depth?: number
  default_case?: string | null
  cases?: OptionCase[]
  inputs?: OptionInput[]
  hotkeys?: OptionInput[]
}

type OptionValue = {
  type: string
  value?: boolean
  caseName?: string
  caseNames?: string[]
  values?: Record<string, string>
}

type TaskItem = { name: string; label: string; description?: string; group?: string[] }

type StatusSnapshot = {
  connected?: boolean
  token_ok?: boolean
  state?: string
  ready?: boolean
  elevated?: boolean | null
  focused?: boolean | null
  resolution_ok?: boolean | null
  resolution?: { w?: number; h?: number; ratio?: number } | null
  current_task?: string
  current_label?: string
  current_nodes?: string[]
  elapsed?: number
  task_count?: number
  summary?: string
  last_result?: { task?: string; succeeded?: boolean; failed?: boolean; elapsed?: number } | null
  executor?: {
    executor_present?: boolean
    python_ok?: boolean
    python?: string
    python_reason?: string
    node?: string
    manual_launcher?: string
  }
}

type LogLine = { seq: number; t?: string; src?: string; text?: string }

type Shot = { image: string; width?: number; height?: number; ts?: number }

type PanelState = {
  loading: boolean
  busy: boolean
  notice: string
  error: string
  status: StatusSnapshot
  tasks: TaskItem[]
  filter: string
  openTask: string
  options: OptionNode[]
  optionsLoading: boolean
  values: Record<string, OptionValue>
  logs: LogLine[]
  logCursor: number
  follow: boolean
  shot: Shot | null
  shotAuto: boolean
  fullscreen: boolean
  accordionEpoch: number
  expandAll: boolean
}

/**
 * ``props.api.call`` resolves to the host's envelope, whose nesting has varied
 * (``result`` / ``data`` / bare). Unwrap defensively rather than assuming — a
 * wrong guess here renders an empty panel with no error.
 */
function unwrap(envelope: any): any {
  return envelope?.result ?? envelope?.data ?? envelope
}

function asRecord(value: any): Record<string, any> {
  return value && typeof value === "object" ? value : {}
}

function formatElapsed(seconds: any): string {
  const total = Math.max(0, Math.floor(Number(seconds) || 0))
  if (total < 60) return `${total}s`
  const minutes = Math.floor(total / 60)
  const rest = total % 60
  if (minutes < 60) return `${minutes}m${String(rest).padStart(2, "0")}s`
  return `${Math.floor(minutes / 60)}h${String(minutes % 60).padStart(2, "0")}m`
}

/**
 * The option nodes reachable under a node's *currently selected* cases.
 *
 * A nested option only exists while its parent's selected case lists it
 * (``cases[].option``), and it must be submitted only then — the executor
 * merges the overrides of the selected cases and nothing else.
 */
function activeChildren(node: OptionNode, value: OptionValue | undefined): OptionNode[] {
  const cases = Array.isArray(node.cases) ? node.cases : []
  const selected = new Set<string>()
  if (value) {
    if (value.type === "checkbox") (value.caseNames || []).forEach((name) => selected.add(name))
    else if (value.type === "select" && value.caseName) selected.add(value.caseName)
    else if (value.type === "switch") selected.add(value.value ? "Yes" : "No")
  } else if (node.default_case) {
    selected.add(node.default_case)
  }
  const out: OptionNode[] = []
  for (const item of cases) {
    if (selected.has(item.name) && Array.isArray(item.option)) out.push(...item.option)
  }
  return out
}

export default function MaaendPanel(props: PluginSurfaceProps) {
  const [state, setState] = useState<PanelState>({
    loading: false,
    busy: false,
    notice: "",
    error: "",
    status: {},
    tasks: [],
    filter: "",
    openTask: "",
    options: [],
    optionsLoading: false,
    values: {},
    logs: [],
    logCursor: 0,
    follow: true,
    shot: null,
    shotAuto: false,
    fullscreen: false,
    accordionEpoch: 0,
    expandAll: true,
  })

  const refreshing = useRef(false)
  const unmounted = useRef(false)
  // The mount effect below runs once, but ``state`` is rebuilt every render:
  // reading the state variable inside those callbacks would always see the
  // first render's snapshot. The ref is the live one.
  const latest = useRef(state)
  latest.current = state

  const patch = (next: Partial<PanelState>) => setState((prev) => ({ ...prev, ...next }))

  const unwrapError = (exc: any): string => {
    const raw = String(exc?.message || exc || "")
    const code = String(exc?.code || "")
    // "the plugin is not running" is a state, not a failure worth shouting about.
    if (code === "PLUGIN_NOT_RUNNING" || /not running|not started/i.test(raw)) return ""
    return t("ui.errorPrefix") + raw
  }

  const refresh = async () => {
    if (refreshing.current || unmounted.current) return
    refreshing.current = true
    patch({ loading: true })
    try {
      const status = asRecord(unwrap(await props.api.call("maaend_status")))
      if (unmounted.current) return
      patch({ loading: false, status: status as StatusSnapshot, error: "" })
      // The catalogue only changes when the resource layer is updated, so it is
      // fetched once per session rather than on every five-second status tick.
      if (status.connected && !latest.current.tasks.length) void loadTasks()
    } catch (exc: any) {
      if (unmounted.current) return
      patch({ loading: false, error: unwrapError(exc) })
    } finally {
      refreshing.current = false
    }
  }

  const loadTasks = async (force = false) => {
    try {
      const data = asRecord(unwrap(await props.api.call("maaend_list_tasks", { refresh: force })))
      if (unmounted.current) return
      const raw = Array.isArray(data.tasks) ? data.tasks : []
      patch({
        tasks: raw.map((item: any) => ({
          name: String(item?.name || ""),
          label: String(item?.label || item?.name || ""),
          description: String(item?.description || ""),
          group: Array.isArray(item?.group) ? item.group.map(String) : [],
        })),
      })
    } catch (exc: any) {
      if (unmounted.current) return
      const message = unwrapError(exc)
      if (message) patch({ error: message })
    }
  }

  const toggleOptions = async (name: string) => {
    if (latest.current.openTask === name) {
      patch({ openTask: "", options: [], values: {} })
      return
    }
    patch({ openTask: name, options: [], values: {}, optionsLoading: true, error: "" })
    try {
      const data = asRecord(unwrap(await props.api.call("maaend_task_options", { task: name })))
      if (unmounted.current) return
      patch({ options: Array.isArray(data.tree) ? data.tree : [], optionsLoading: false })
    } catch (exc: any) {
      if (unmounted.current) return
      patch({ optionsLoading: false, error: unwrapError(exc) })
    }
  }

  const setValue = (name: string, value: OptionValue) =>
    setState((prev) => ({ ...prev, values: { ...prev.values, [name]: value } }))

  const runTask = async (name: string) => {
    if (latest.current.busy) return
    patch({ busy: true, notice: "", error: "" })
    try {
      const payload: Record<string, unknown> = { task: name }
      // Options only ride along for the task they were edited for.
      if (latest.current.openTask === name && Object.keys(latest.current.values).length) {
        payload.options = latest.current.values
      }
      const data = asRecord(unwrap(await props.api.call("maaend_start_task", payload)))
      if (unmounted.current) return
      patch({ busy: false, notice: String(data.summary || t("ui.started")) })
      void refresh()
    } catch (exc: any) {
      if (unmounted.current) return
      patch({ busy: false, error: unwrapError(exc) })
    }
  }

  const callSimple = async (action: string) => {
    if (latest.current.busy) return
    patch({ busy: true, notice: "", error: "" })
    try {
      const data = asRecord(unwrap(await props.api.call(action, {})))
      if (unmounted.current) return
      patch({ busy: false, notice: String(data.summary || "") })
      void refresh()
    } catch (exc: any) {
      if (unmounted.current) return
      patch({ busy: false, error: unwrapError(exc) })
    }
  }

  const grabShot = async () => {
    if (unmounted.current) return
    try {
      const data = asRecord(unwrap(await props.api.call("maaend_screenshot", {})))
      if (unmounted.current) return
      const image = String(data.image || "")
      if (image) patch({ shot: { image, width: data.width, height: data.height, ts: data.ts } })
    } catch (exc: any) {
      if (unmounted.current) return
      // A failed frame must not blank the panel: stop the auto loop and let the
      // placeholder carry the message.
      const message = unwrapError(exc)
      patch({ shot: null, shotAuto: false, ...(message ? { error: message } : {}) })
    }
  }

  const pollLogs = async () => {
    if (unmounted.current) return
    try {
      const data = asRecord(
        unwrap(await props.api.call("maaend_logs", { since: latest.current.logCursor, limit: LOG_KEEP })),
      )
      if (unmounted.current) return
      const incoming: LogLine[] = Array.isArray(data.lines)
        ? data.lines.map((line: any) => ({
            seq: Number(line?.seq || 0),
            t: String(line?.t || ""),
            src: String(line?.src || ""),
            text: String(line?.text || ""),
          }))
        : []
      // `reset` means the mirror started over (the panel or the plugin
      // restarted), so the cursor we hold means nothing and the buffer is new.
      const base = data.reset === true ? [] : latest.current.logs
      const next = Number(data.next)
      patch({
        logs: incoming.length ? [...base, ...incoming].slice(-LOG_KEEP) : base,
        logCursor: Number.isFinite(next) ? next : latest.current.logCursor,
      })
    } catch {
      // The log view is decoration; a failure here must not surface as an error.
    }
  }

  useEffect(() => {
    void refresh()
    void loadTasks()
    const statusTimer = window.setInterval(() => void refresh(), STATUS_REFRESH_MS)
    const logTimer = window.setInterval(() => void pollLogs(), LOG_POLL_MS)
    return () => {
      unmounted.current = true
      window.clearInterval(statusTimer)
      window.clearInterval(logTimer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!state.shotAuto || !state.status.connected) return undefined
    const timer = window.setInterval(() => void grabShot(), SHOT_AUTO_MS)
    return () => window.clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.shotAuto, state.status.connected])

  const status = state.status
  const connected = Boolean(status.connected)
  const running = status.state === "busy"
  const badgeTone = !connected ? "warning" : running ? "info" : "success"
  const badgeText = !connected ? t("ui.disconnected") : running ? t("ui.running") : t("ui.connected")

  const resolution = asRecord(status.resolution)
  const resolutionText =
    status.resolution_ok === null || status.resolution_ok === undefined
      ? t("ui.unknown")
      : status.resolution_ok
        ? t("ui.resolutionOk")
        : t("ui.badResolution", { w: Number(resolution.w || 0), h: Number(resolution.h || 0) })

  const executor = asRecord(status.executor)
  const nodes = status.current_nodes || []
  const lastResult = asRecord(status.last_result)

  const statusItems = [
    { key: "state", label: t("ui.state"), value: String(status.summary || t("ui.unknown")) },
    {
      key: "task",
      label: t("ui.currentTask"),
      value: status.current_label || status.current_task || t("ui.idle"),
    },
    {
      key: "step",
      label: t("ui.currentStep"),
      value: nodes.length ? String(nodes[nodes.length - 1]) : t("ui.none"),
    },
    {
      key: "elapsed",
      label: t("ui.elapsed"),
      value: running ? formatElapsed(status.elapsed) : "—",
    },
    { key: "resolution", label: t("ui.resolution"), value: resolutionText },
    {
      key: "admin",
      label: t("ui.admin"),
      value:
        status.elevated === false
          ? t("ui.notAdminShort")
          : status.elevated
            ? t("ui.yes")
            : t("ui.unknown"),
    },
    { key: "done", label: t("ui.lastResult"), value: describeLastResult(lastResult) },
    { key: "count", label: t("ui.taskCount"), value: String(status.task_count || 0) },
  ]

  const filter = state.filter.trim().toLowerCase()
  const visible = state.tasks.filter(
    (task) =>
      !filter ||
      task.name.toLowerCase().includes(filter) ||
      task.label.toLowerCase().includes(filter) ||
      (task.description || "").toLowerCase().includes(filter),
  )
  const groups = groupTasks(visible)
  const logText = state.logs
    .slice(state.follow ? -LOG_SHOW : 0)
    .map((line) => `${line.t || "--:--:--"}  ${line.text || ""}`)
    .join("\n")

  return (
    <Page title={t("ui.title")}>
      <Card>
        <Stack>
          <Inline gap={8}>
            <StatusBadge tone={badgeTone}>{badgeText}</StatusBadge>
            {running ? <Progress label={t("ui.progress")} /> : null}
          </Inline>
          {state.error ? <Alert tone="warning">{state.error}</Alert> : null}
          {state.notice ? <Alert tone="info">{state.notice}</Alert> : null}
          {!connected ? (
            <Alert tone="warning">
              <Stack>
                <Text>{t("ui.disconnectedHint")}</Text>
                <ButtonGroup>
                  <Button onClick={() => void callSimple("maaend_launch_panel")} disabled={state.busy}>
                    {t("ui.launchExecutor")}
                  </Button>
                  <Button onClick={() => void refresh()}>{t("ui.refresh")}</Button>
                </ButtonGroup>
                {executor.manual_launcher ? (
                  <Tip>{t("ui.manualLaunchHint", { path: String(executor.manual_launcher) })}</Tip>
                ) : null}
              </Stack>
            </Alert>
          ) : null}
          {status.elevated === false ? <Alert tone="warning">{t("ui.notAdmin")}</Alert> : null}
          {status.resolution_ok === false ? (
            <Alert tone="warning">{t("ui.badResolutionHint")}</Alert>
          ) : null}
          {connected && executor.python_ok === false ? (
            <Alert tone="warning">
              {t("ui.noInterpreter", { reason: String(executor.python_reason || "") })}
            </Alert>
          ) : null}
          <KeyValue items={statusItems} />
          <Toolbar>
            <ToolbarGroup>
              <Button onClick={() => void refresh()} disabled={state.loading}>
                {t("ui.refresh")}
              </Button>
              <Button onClick={() => void callSimple("maaend_prepare")} disabled={state.busy || !connected}>
                {t("ui.prepare")}
              </Button>
              <Button onClick={() => void callSimple("maaend_reload_config")} disabled={state.busy}>
                {t("ui.reload")}
              </Button>
            </ToolbarGroup>
            <ToolbarGroup>
              <Button
                tone="danger"
                onClick={() => void callSimple("maaend_stop_task")}
                disabled={state.busy || !running}
              >
                {t("ui.stop")}
              </Button>
            </ToolbarGroup>
          </Toolbar>
          <Text>{t("ui.stopHint")}</Text>
        </Stack>
      </Card>

      <Columns cols={2} minWidth={340} gap={12}>
        <Card title={t("ui.taskList")}>
          <Stack>
            <Field label={t("ui.filter")}>
              <Input
                value={state.filter}
                placeholder={t("ui.filterPlaceholder")}
                onChange={(value: string) => patch({ filter: value })}
              />
            </Field>
            <Inline gap={8}>
              <Button onClick={() => patch({ expandAll: true, accordionEpoch: state.accordionEpoch + 1 })}>
                {t("ui.expandAll")}
              </Button>
              <Button onClick={() => patch({ expandAll: false, accordionEpoch: state.accordionEpoch + 1 })}>
                {t("ui.collapseAll")}
              </Button>
            </Inline>
            {!connected ? (
              <EmptyState title={t("ui.disconnected")} description={t("ui.disconnectedHint")} />
            ) : visible.length === 0 ? (
              <EmptyState title={t("ui.noTasks")} />
            ) : (
              groups.map(([group, items]) => (
                <Accordion
                  key={`${group}-${state.accordionEpoch}`}
                  id={`group:${group}:${state.accordionEpoch}`}
                  open={state.expandAll}
                  title={`${group} · ${items.length}`}
                >
                  <Stack>
                    {items.map((task) => (
                      <TaskEntry
                        key={task.name}
                        task={task}
                        running={running && status.current_task === task.name}
                        busy={state.busy}
                        opened={state.openTask === task.name}
                        changed={state.openTask === task.name ? Object.keys(state.values).length : 0}
                        options={state.openTask === task.name ? state.options : []}
                        optionsLoading={state.openTask === task.name && state.optionsLoading}
                        values={state.values}
                        onToggle={() => void toggleOptions(task.name)}
                        onRun={() => void runTask(task.name)}
                        onValue={setValue}
                      />
                    ))}
                  </Stack>
                </Accordion>
              ))
            )}
          </Stack>
        </Card>

        <Stack>
          <Card title={t("ui.screenshot")}>
            <Stack>
              <Toolbar>
                <ToolbarGroup>
                  <Button onClick={() => void grabShot()} disabled={!connected}>
                    {t("ui.shotOnce")}
                  </Button>
                  <Button
                    tone={state.shotAuto ? "danger" : "default"}
                    onClick={() => patch({ shotAuto: !state.shotAuto })}
                    disabled={!connected}
                  >
                    {state.shotAuto ? t("ui.shotStop") : t("ui.shotAuto")}
                  </Button>
                </ToolbarGroup>
                <ToolbarGroup>
                  <Button onClick={() => patch({ fullscreen: true })} disabled={!state.shot}>
                    {t("ui.fullscreen")}
                  </Button>
                </ToolbarGroup>
              </Toolbar>
              {state.shot ? (
                <Stack>
                  <ImagePreview src={state.shot.image} alt={t("ui.screenshot")} />
                  {state.shotAuto ? <Text>{t("ui.live")}</Text> : null}
                </Stack>
              ) : (
                <EmptyState
                  title={t("ui.noShot")}
                  description={connected ? t("ui.noShotHint") : t("ui.disconnectedHint")}
                />
              )}
            </Stack>
          </Card>

          <Card title={t("ui.logs")}>
            <Stack>
              <Toolbar>
                <ToolbarGroup>
                  <Button onClick={() => void pollLogs()}>{t("ui.refresh")}</Button>
                  <Button
                    tone={state.follow ? "primary" : "default"}
                    onClick={() => patch({ follow: !state.follow })}
                  >
                    {state.follow ? t("ui.followOn") : t("ui.followOff")}
                  </Button>
                  <Button onClick={() => patch({ logs: [] })}>{t("ui.clearLogs")}</Button>
                </ToolbarGroup>
              </Toolbar>
              {state.logs.length === 0 ? (
                <EmptyState title={t("ui.noLogs")} />
              ) : (
                <LogViewer text={logText} autoScroll={state.follow} />
              )}
            </Stack>
          </Card>
        </Stack>
      </Columns>

      <Modal
        open={state.fullscreen}
        title={t("ui.screenshot")}
        onClose={() => patch({ fullscreen: false })}
        size="lg"
      >
        {state.shot ? <ImagePreview src={state.shot.image} alt={t("ui.screenshot")} /> : null}
      </Modal>
    </Page>
  )
}

/** One runnable task: name, its own options toggle, and its own run button. */
function TaskEntry(props: {
  task: TaskItem
  running: boolean
  busy: boolean
  opened: boolean
  changed: number
  options: OptionNode[]
  optionsLoading: boolean
  values: Record<string, OptionValue>
  onToggle: () => void
  onRun: () => void
  onValue: (name: string, value: OptionValue) => void
}) {
  const { task, running, busy, opened, changed, options, optionsLoading, values } = props
  return (
    <Stack>
      <Heading as="h3">{task.label}</Heading>
      <Text>{task.description || task.name}</Text>
      <Inline gap={8}>
        <Text>{task.name}</Text>
        {running ? <StatusBadge tone="info">{t("ui.running")}</StatusBadge> : null}
        {changed ? <StatusBadge tone="success">{t("ui.changed", { n: changed })}</StatusBadge> : null}
      </Inline>
      <ButtonGroup>
        <Button onClick={props.onToggle}>{opened ? t("ui.hideOptions") : t("ui.showOptions")}</Button>
        <Button tone="primary" onClick={props.onRun} disabled={busy || running}>
          {t("ui.run")}
        </Button>
      </ButtonGroup>
      {opened ? (
        <>
          <Divider />
          <OptionsEditor
            options={options}
            loading={optionsLoading}
            values={values}
            onChange={props.onValue}
          />
        </>
      ) : null}
      <Divider />
    </Stack>
  )
}

/**
 * One task's option tree, rendered recursively.
 *
 * Nesting is the whole point of this component and the easiest thing to get
 * wrong. ``depth`` is carried explicitly and shown as a ``└`` prefix, because
 * the kit's components take no custom styles — indentation is not available, so
 * structure has to carry the relationship instead.
 */
function OptionsEditor(props: {
  options: OptionNode[]
  loading: boolean
  values: Record<string, OptionValue>
  onChange: (name: string, value: OptionValue) => void
}) {
  const { options, loading, values, onChange } = props

  if (loading) return <Text>{t("ui.optionsLoading")}</Text>
  if (!options.length) return <Text>{t("ui.noOptions")}</Text>

  const renderNode = (node: OptionNode, key: string, depth: number): any => {
    const value = values[node.name]
    const children = activeChildren(node, value)
    const cases = Array.isArray(node.cases) ? node.cases : []
    const indent = "└ ".repeat(depth)
    const label = indent + (node.label || node.name)

    let control = null
    if (node.type === "switch") {
      // A switch is really a two-case select; the executor matches the literal
      // case names Yes/No when the value is submitted as a boolean.
      const on = value ? value.value === true : node.default_case === "Yes"
      control = (
        <Field label={label} help={node.description}>
          <Switch
            checked={on}
            onChange={(next: boolean) => onChange(node.name, { type: "switch", value: next })}
          />
        </Field>
      )
    } else if (node.type === "select") {
      const current = value?.caseName ?? node.default_case ?? (cases[0]?.name || "")
      control = (
        <Field label={label} help={node.description}>
          <Select
            value={current}
            options={cases.map((item) => ({ value: item.name, label: item.label || item.name }))}
            onChange={(next: string) => onChange(node.name, { type: "select", caseName: next })}
          />
        </Field>
      )
    } else if (node.type === "checkbox") {
      control = (
        <Field label={label} help={node.description}>
          <CheckboxGroup
            value={value?.caseNames ?? []}
            options={cases.map((item) => ({ value: item.name, label: item.label || item.name }))}
            onChange={(next: string[]) =>
              onChange(node.name, { type: "checkbox", caseNames: next })
            }
          />
        </Field>
      )
    } else if (node.type === "input" || node.type === "hotkey") {
      const fields = (node.type === "input" ? node.inputs : node.hotkeys) || []
      control = (
        <Stack>
          {fields.map((field) => (
            <Field
              key={field.name}
              label={indent + (field.label || field.name)}
              help={[node.description, field.pipeline_type].filter(Boolean).join(" · ")}
            >
              <Input
                value={value?.values?.[field.name] ?? field.default ?? ""}
                placeholder={field.default || ""}
                onChange={(next: string) =>
                  onChange(node.name, {
                    type: node.type,
                    values: { ...(value?.values || {}), [field.name]: next },
                  })
                }
              />
            </Field>
          ))}
        </Stack>
      )
    } else {
      // An option type this plugin has not been taught. Shown rather than
      // dropped: silently hiding a control would make the executor behave
      // differently from what the panel appears to offer.
      control = (
        <Field label={label} help={t("ui.unsupportedOption", { type: node.type })}>
          <Text>{node.description || node.name}</Text>
        </Field>
      )
    }

    return (
      <Stack key={key}>
        {control}
        {children.map((child, index) => renderNode(child, `${key}.${index}`, depth + 1))}
      </Stack>
    )
  }

  return <Stack>{options.map((node, index) => renderNode(node, `${node.name}.${index}`, 0))}</Stack>
}

/** Group tasks by their catalogue ``group``, keeping catalogue order. */
function groupTasks(tasks: TaskItem[]): Array<[string, TaskItem[]]> {
  const buckets = new Map<string, TaskItem[]>()
  for (const task of tasks) {
    const labels = task.group && task.group.length ? task.group : ["other"]
    for (const label of labels) {
      if (!buckets.has(label)) buckets.set(label, [])
      buckets.get(label)!.push(task)
    }
  }
  return Array.from(buckets.entries())
}

function describeLastResult(last: Record<string, any>): string {
  if (!Object.keys(last).length) return t("ui.none")
  const task = String(last.task || "")
  const elapsed = formatElapsed(last.elapsed)
  if (last.succeeded === true) return t("ui.resultOk", { task, elapsed })
  if (last.failed === true) return t("ui.resultFailed", { task, elapsed })
  return t("ui.resultIncomplete", { task, elapsed })
}
