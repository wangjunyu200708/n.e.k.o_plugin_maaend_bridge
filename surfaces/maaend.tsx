/**
 * MaaEnd executor control panel.
 *
 * A hosted TSX surface: it runs in a sandboxed iframe and reaches the plugin
 * only through ``props.api.call``, which needs an ``@ui.action`` on the other
 * side — see the ``maaend_*`` entries in ``__init__.py``. Nothing here talks to
 * the executor directly; the panel backend is on 127.0.0.1:12702 with a token
 * the browser never sees.
 *
 * Everything user-visible goes through ``t()`` (the kit's i18n helper, which
 * reads this plugin's ``i18n/<locale>.json`` off ``window.__NEKO_PAYLOAD``), so
 * no string is hardcoded to a locale. Option labels come from the executor
 * itself, already localised on its side.
 */
import {
  Alert,
  Button,
  ButtonGroup,
  Card,
  CheckboxGroup,
  Divider,
  EmptyState,
  Field,
  Input,
  KeyValue,
  List,
  Page,
  Select,
  Stack,
  StatusBadge,
  Switch,
  Text,
  Tip,
  t,
  useEffect,
  useRef,
  useState,
} from "@neko/plugin-ui"
import type { PluginSurfaceProps } from "@neko/plugin-ui"

const STATUS_REFRESH_INTERVAL_MS = 5000
const FILTER_DEBOUNCE_MS = 200

type OptionInput = {
  name: string
  label?: string
  default?: string
  pipeline_type?: string
  verify?: string
  pattern_msg?: string
}

type OptionCase = {
  name: string
  label?: string
  option?: OptionNode[]
}

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

type Value = {
  type: string
  value?: boolean
  caseName?: string
  caseNames?: string[]
  values?: Record<string, string>
}

type TaskItem = {
  name: string
  label: string
  description?: string
  group?: string[]
}

type StatusSnapshot = {
  connected?: boolean
  token_ok?: boolean
  state?: string
  ready?: boolean
  elevated?: boolean | null
  focused?: boolean | null
  resolution_ok?: boolean | null
  resolution?: { w?: number; h?: number; ratio?: number; message?: string } | null
  current_task?: string
  current_label?: string
  current_nodes?: string[]
  elapsed?: number
  task_count?: number
  summary?: string
}

type PanelState = {
  loading: boolean
  busy: boolean
  status: StatusSnapshot
  tasks: TaskItem[]
  filter: string
  selected: string
  options: OptionNode[]
  optionsLoading: boolean
  values: Record<string, Value>
  notice: string
  error: string
}

/**
 * ``props.api.call`` resolves to the host's own envelope, whose nesting has
 * varied (``result`` / ``data`` / bare). Unwrap defensively rather than
 * assuming — a wrong guess here renders an empty panel with no error.
 */
function unwrap(envelope: any): any {
  const inner = envelope?.result ?? envelope?.data ?? envelope
  return inner
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

/** The option nodes reachable under a node's *currently selected* cases. */
function activeChildren(node: OptionNode, value: Value | undefined): OptionNode[] {
  const cases = Array.isArray(node.cases) ? node.cases : []
  const selected = new Set<string>()
  if (value) {
    if (value.type === "checkbox") (value.caseNames || []).forEach((n) => selected.add(n))
    else if (value.type === "select" && value.caseName) selected.add(value.caseName)
    else if (value.type === "switch") selected.add(value.value ? "Yes" : "No")
  } else if (node.default_case) {
    selected.add(node.default_case)
  }
  const out: OptionNode[] = []
  for (const item of cases) {
    if (!selected.has(item.name)) continue
    if (Array.isArray(item.option)) out.push(...item.option)
  }
  return out
}

export default function MaaendPanel(props: PluginSurfaceProps) {
  const [state, setState] = useState<PanelState>({
    loading: false,
    busy: false,
    status: {},
    tasks: [],
    filter: "",
    selected: "",
    options: [],
    optionsLoading: false,
    values: {},
    notice: "",
    error: "",
  })

  const refreshing = useRef(false)
  const unmounted = useRef(false)
  const filterTimer = useRef<number | null>(null)
  // Async callbacks are created once (the effect below runs with `[]`) while
  // ``state`` is rebuilt every render, so reading the state variable inside
  // them would always see the first render's snapshot. The ref is the live one.
  const latest = useRef(state)
  latest.current = state

  const patch = (next: Partial<PanelState>) =>
    setState((prev) => ({ ...prev, ...next }))

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
      // The catalogue only changes when the resource layer is updated, so
      // fetch it once per session rather than on every status tick.
      if (status.connected && !latest.current.tasks.length) void loadTasks()
    } catch (exc: any) {
      if (unmounted.current) return
      patch({ loading: false, error: unwrapError(exc) })
    } finally {
      refreshing.current = false
    }
  }

  const loadTasks = async (refreshFlag = false) => {
    try {
      const data = asRecord(unwrap(await props.api.call("maaend_list_tasks", { refresh: refreshFlag })))
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

  const selectTask = async (name: string) => {
    patch({ selected: name, options: [], values: {}, optionsLoading: true, notice: "", error: "" })
    try {
      const data = asRecord(unwrap(await props.api.call("maaend_task_options", { task: name })))
      if (unmounted.current) return
      patch({
        options: Array.isArray(data.tree) ? data.tree : [],
        optionsLoading: false,
      })
    } catch (exc: any) {
      if (unmounted.current) return
      patch({ optionsLoading: false, error: unwrapError(exc) })
    }
  }

  const runTask = async (name: string) => {
    if (state.busy) return
    patch({ busy: true, notice: "", error: "" })
    try {
      const payload: Record<string, unknown> = { task: name }
      if (state.selected === name && Object.keys(state.values).length) {
        payload.options = state.values
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

  const stopTask = async () => {
    if (state.busy) return
    patch({ busy: true, notice: "", error: "" })
    try {
      const data = asRecord(unwrap(await props.api.call("maaend_stop_task")))
      if (unmounted.current) return
      patch({ busy: false, notice: String(data.summary || "") })
      void refresh()
    } catch (exc: any) {
      if (unmounted.current) return
      patch({ busy: false, error: unwrapError(exc) })
    }
  }

  const callSimple = async (action: string) => {
    if (state.busy) return
    patch({ busy: true, notice: "", error: "" })
    try {
      const data = asRecord(unwrap(await props.api.call(action)))
      if (unmounted.current) return
      patch({ busy: false, notice: String(data.summary || "") })
      void refresh()
    } catch (exc: any) {
      if (unmounted.current) return
      patch({ busy: false, error: unwrapError(exc) })
    }
  }

  const setValue = (name: string, value: Value) =>
    setState((prev) => ({ ...prev, values: { ...prev.values, [name]: value } }))

  useEffect(() => {
    void refresh()
    void loadTasks()
    const timer = window.setInterval(() => void refresh(), STATUS_REFRESH_INTERVAL_MS)
    return () => {
      unmounted.current = true
      window.clearInterval(timer)
      if (filterTimer.current !== null) window.clearTimeout(filterTimer.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const status = state.status
  const connected = Boolean(status.connected)
  const running = status.state === "busy"
  const tone = !connected ? "warning" : running ? "info" : "success"
  const badge = !connected
    ? t("ui.disconnected")
    : running
      ? t("ui.running")
      : t("ui.connected")

  const resolution = asRecord(status.resolution)
  const resolutionText = status.resolution_ok === null || status.resolution_ok === undefined
    ? t("ui.unknown")
    : status.resolution_ok
      ? t("ui.resolutionOk")
      : t("ui.badResolution", {
          w: Number(resolution.w || 0),
          h: Number(resolution.h || 0),
        })

  const items = [
    { key: "state", label: t("ui.state"), value: String(status.summary || t("ui.unknown")) },
    {
      key: "task",
      label: t("ui.currentTask"),
      value: status.current_label || status.current_task || t("ui.idle"),
    },
    {
      key: "progress",
      label: t("ui.progress"),
      value: (status.current_nodes || []).slice(-3).join(" → ") || t("ui.none"),
    },
    { key: "resolution", label: t("ui.resolution"), value: resolutionText },
    {
      key: "admin",
      label: t("ui.admin"),
      value: status.elevated === false ? t("ui.notAdminShort") : status.elevated ? t("ui.yes") : t("ui.unknown"),
    },
  ]

  const filter = state.filter.trim().toLowerCase()
  const visible = state.tasks.filter((task) =>
    !filter ||
    task.name.toLowerCase().includes(filter) ||
    task.label.toLowerCase().includes(filter),
  )

  return (
    <Page title={t("ui.title")}>
      <Card>
        <Stack>
          <StatusBadge tone={tone}>{badge}</StatusBadge>
          {state.error ? <Alert tone="warning">{state.error}</Alert> : null}
          {state.notice ? <Alert tone="info">{state.notice}</Alert> : null}
          {status.elevated === false ? <Alert tone="warning">{t("ui.notAdmin")}</Alert> : null}
          {status.resolution_ok === false ? <Alert tone="warning">{t("ui.badResolutionHint")}</Alert> : null}
          <KeyValue items={items} />
          <ButtonGroup>
            <Button onClick={() => void refresh()} disabled={state.loading}>
              {t("ui.refresh")}
            </Button>
            <Button tone="danger" onClick={() => void stopTask()} disabled={state.busy || !running}>
              {t("ui.stop")}
            </Button>
            <Button onClick={() => void callSimple("maaend_prepare")} disabled={state.busy}>
              {t("ui.prepare")}
            </Button>
            <Button onClick={() => void callSimple("maaend_reload_config")} disabled={state.busy}>
              {t("ui.reload")}
            </Button>
          </ButtonGroup>
          <Text>{t("ui.stopHint")}</Text>
        </Stack>
      </Card>

      <Card title={t("ui.taskList")}>
        <Stack>
          <Field label={t("ui.filter")}>
            <Input
              value={state.filter}
              placeholder={t("ui.filterPlaceholder")}
              onChange={(value: string) => {
                patch({ filter: value })
                if (filterTimer.current !== null) window.clearTimeout(filterTimer.current)
                filterTimer.current = window.setTimeout(() => {
                  filterTimer.current = null
                }, FILTER_DEBOUNCE_MS)
              }}
            />
          </Field>
          {!connected ? (
            <EmptyState title={t("ui.disconnected")} description={t("ui.disconnectedHint")} />
          ) : visible.length === 0 ? (
            <EmptyState title={t("ui.noTasks")} />
          ) : (
            <List
              items={visible}
              render={(task: TaskItem) => (
                <div>
                  <Stack>
                    <Text>{task.label}</Text>
                    <Text>{task.description || task.name}</Text>
                    <ButtonGroup>
                      <Button
                        onClick={() => void selectTask(task.name)}
                        tone={state.selected === task.name ? "primary" : undefined}
                      >
                        {state.selected === task.name ? t("ui.selected") : t("ui.configure")}
                      </Button>
                      <Button onClick={() => void runTask(task.name)} disabled={state.busy || running}>
                        {t("ui.run")}
                      </Button>
                    </ButtonGroup>
                  </Stack>
                  {state.selected === task.name ? (
                    <OptionsEditor
                      options={state.options}
                      loading={state.optionsLoading}
                      values={state.values}
                      onChange={setValue}
                    />
                  ) : null}
                </div>
              )}
            />
          )}
        </Stack>
      </Card>

      <Tip>{t("ui.prepareHint")}</Tip>
    </Page>
  )
}

/**
 * Renders one task's option tree.
 *
 * Nesting is the whole point of this component and the easiest thing to get
 * wrong: a nested option only exists while its parent's *selected* case lists
 * it (``cases[].option``), and it must be submitted only then — the executor
 * merges the overrides of the selected cases and nothing else.
 */
function OptionsEditor(props: {
  options: OptionNode[]
  loading: boolean
  values: Record<string, Value>
  onChange: (name: string, value: Value) => void
}) {
  const { options, loading, values, onChange } = props

  if (loading) return <Text>{t("ui.optionsLoading")}</Text>
  if (!options.length) return <Text>{t("ui.noOptions")}</Text>

  const render = (node: OptionNode, key: string): any => {
    const value = values[node.name]
    const children = activeChildren(node, value)
    const label = node.label || node.name
    const cases = Array.isArray(node.cases) ? node.cases : []

    let control = null
    if (node.type === "switch") {
      // A switch is really a two-case select; the executor matches the
      // literal case names Yes/No when the value is submitted as a boolean.
      const on = value ? value.value === true : node.default_case === "Yes"
      control = (
        <Field label={label} help={node.description}>
          <Switch checked={on} onChange={(next: boolean) => onChange(node.name, { type: "switch", value: next })} />
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
            onChange={(next: string[]) => onChange(node.name, { type: "checkbox", caseNames: next })}
          />
        </Field>
      )
    } else if (node.type === "input" || node.type === "hotkey") {
      const fields = (node.type === "input" ? node.inputs : node.hotkeys) || []
      control = (
        <Field label={label} help={node.description}>
          <Stack>
            {fields.map((field) => (
              <Field
                key={field.name}
                label={field.label || field.name}
                help={field.pipeline_type ? `${field.pipeline_type}` : ""}
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
        </Field>
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
      <div key={key}>
        <Stack>
          {control}
          {children.map((child, index) => render(child, `${key}.${child.name}.${index}`))}
        </Stack>
        <Divider />
      </div>
    )
  }

  return (
    <Card title={t("ui.options")}>
      <Stack>
        <Text>{t("ui.optionsHint")}</Text>
        {options.map((node, index) => render(node, `${node.name}.${index}`))}
      </Stack>
    </Card>
  )
}
