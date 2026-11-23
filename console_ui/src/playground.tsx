import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  ArrowUpRight,
  Bot,
  Check,
  ChevronRight,
  CircleDot,
  FlaskConical,
  History,
  LoaderCircle,
  RefreshCw,
  Send,
  ShieldCheck,
  SlidersHorizontal,
  Terminal,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import "./style.css";
import "./playground.css";

type Policy = {
  id: string;
  models: string[];
  rpm: number;
  enabled: boolean;
  revision: number;
};
type Audit = Policy & {
  sequence: number;
  at_utc: string;
  actor: string;
  target: string;
};
type State = {
  apps: Policy[];
  models: string[];
  audit: Audit[];
  upstream_calls: number;
};
type Event = {
  litellm_call_id: string;
  started_at: string | null;
  key_alias: string | null;
  model_alias: string;
  provider_outcome: string;
  total_tokens: number | null;
  usage_source: string;
  duration_ms: number | null;
};
type Result = {
  status: number | null;
  error?: string;
  reply?: string;
  duration_ms?: number;
  app?: string;
  model?: string;
};
type View = "call" | "policies" | "events" | "audit";
const names: Record<string, string> = {
  "demo-app-a": "阿汐 · 模拟应用",
  "demo-app-b": "知识库 · 模拟应用",
};
const modelNames: Record<string, string> = {
  "model-a": "模型 A",
  "model-b": "模型 B",
};
const reasons: Record<string, string> = {
  access_denied: "访问被拒绝：应用已停用，或没有所选模型的权限。",
  rpm_exceeded: "调用已达本分钟上限，请等下一分钟或调整限额。",
  identity_rejected: "演示身份验证失败，请重新启动演示。",
  proxy_unavailable: "模型服务无法连接，请检查演示服务是否仍在运行。",
  browser_connection_failed:
    "未收到可用结果，调用是否已执行未知。恢复连接后查看调用记录。",
  revision_conflict: "其他操作已修改该应用。请刷新后重新编辑。",
  policy_store_unavailable: "权限库暂时不可用，请检查演示服务。",
  event_store_unavailable: "调用记录暂时不可用，请稍后刷新。",
  invalid_demo_request: "请选择应用和模型，输入 1–4096 个字符。",
  invalid_policy: "权限设置无效，每分钟上限须为 1–10000 的整数。",
};
const description = (code?: string) =>
  reasons[code ?? ""] ?? "操作未完成，请刷新后重试。";
const localTime = (value: string | null) =>
  value ? new Date(value).toLocaleString() : "—";
function Value({ value }: { value: string | number | null | undefined }) {
  return (
    <span title={value == null ? "未知" : undefined}>
      {value == null ? "—" : value}
    </span>
  );
}
async function api(
  path: string,
  method = "GET",
  payload?: object,
): Promise<Result & State & { events: Event[] }> {
  const response = await fetch("/api/demo/" + path, {
    method,
    headers: {
      "X-Playground": "1",
      ...(payload ? { "Content-Type": "application/json" } : {}),
    },
    ...(payload ? { body: JSON.stringify(payload) } : {}),
  });
  const data = await response.json();
  return { ...data, status: response.status };
}
function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="pg-empty">
      <CircleDot size={28} />
      <p>{children}</p>
    </div>
  );
}
function PolicyEditor({
  policy,
  models,
  saved,
}: {
  policy: Policy;
  models: string[];
  saved: () => Promise<void>;
}) {
  const [selected, setSelected] = useState(policy.models);
  const [rpm, setRpm] = useState(String(policy.rpm));
  const [enabled, setEnabled] = useState(policy.enabled);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMessage("");
    try {
      const result = await api("apps/" + policy.id, "PUT", {
        models: selected,
        rpm: Number(rpm),
        enabled,
        expected_revision: policy.revision,
      });
      if (result.status !== 200) {
        setMessage(description(result.error));
        return;
      }
      await saved();
    } catch {
      setMessage("保存失败，演示服务无法连接。");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card className="pg-policy">
      <form onSubmit={submit}>
        <div className="pg-row">
          <div className="pg-avatar">
            <Bot size={22} />
          </div>
          <div>
            <h3>{names[policy.id] ?? policy.id}</h3>
            <code>{policy.id}</code>
          </div>
          <span className={policy.enabled ? "pg-tag good" : "pg-tag bad"}>
            {policy.enabled ? "已启用" : "已停用"}
          </span>
        </div>
        <fieldset disabled={busy}>
          <legend>允许使用的模型</legend>
          <div className="pg-checks">
            {models.map((model) => (
              <label key={model}>
                <input
                  type="checkbox"
                  checked={selected.includes(model)}
                  onChange={(e) =>
                    setSelected(
                      e.target.checked
                        ? [...selected, model]
                        : selected.filter((v) => v !== model),
                    )
                  }
                />
                {modelNames[model] ?? model}
              </label>
            ))}
          </div>
          <label className="pg-field">
            每分钟调用上限
            <input
              type="number"
              min="1"
              max="10000"
              step="1"
              required
              value={rpm}
              onChange={(e) => setRpm(e.target.value)}
            />
          </label>
          <label className="pg-enable">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
            />
            启用应用
          </label>
          <p className="pg-help">同一应用共享限额；停用后阻断新请求。</p>
        </fieldset>
        {message && (
          <p className="pg-error" role="alert">
            {message}
          </p>
        )}
        <div className="pg-row pg-save">
          <small>版本 {policy.revision}</small>
          <Button disabled={busy} type="submit">
            {busy ? <LoaderCircle className="pg-spin" /> : <Check />}保存设置
          </Button>
        </div>
      </form>
    </Card>
  );
}

function Playground() {
  const [view, setView] = useState<View>("call");
  const [state, setState] = useState<State | null>(null);
  const [events, setEvents] = useState<Event[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [eventError, setEventError] = useState("");
  const [app, setApp] = useState("demo-app-a");
  const [model, setModel] = useState("model-a");
  const [prompt, setPrompt] = useState("你好，请介绍一下这个网关。");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [notice, setNotice] = useState("");
  async function refresh() {
    setLoading(true);
    setError("");
    try {
      const data = await api("state");
      if (data.status !== 200) throw new Error(description(data.error));
      setState(data);
      try {
        const records = await api("events");
        if (records.status !== 200) {
          setEventError(description(records.error));
        } else {
          setEvents(records.events);
          setEventError("");
        }
      } catch {
        setEventError("调用记录加载失败，请检查演示服务后刷新。");
      }
    } catch (e) {
      setError(
        e instanceof Error && e.message !== "Failed to fetch"
          ? e.message
          : "加载失败，演示服务无法连接。",
      );
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    void refresh();
  }, []);
  const policy = state?.apps.find((p) => p.id === app);
  async function send(e: FormEvent) {
    e.preventDefault();
    const submitted = { app, model, prompt };
    setBusy(true);
    setResult(null);
    try {
      const response = await api("call", "POST", submitted);
      setResult({ ...response, app: submitted.app, model: submitted.model });
    } catch {
      setResult({
        status: null,
        error: "browser_connection_failed",
        app: submitted.app,
        model: submitted.model,
      });
    } finally {
      setBusy(false);
      void refresh();
    }
  }
  const nav = [
    ["call", "调用试验", FlaskConical],
    ["policies", "应用权限", SlidersHorizontal],
    ["events", "调用记录", Activity],
    ["audit", "治理审计", History],
  ] as const;
  const titles = {
    call: "模型 Playground",
    policies: "应用权限",
    events: "调用记录",
    audit: "治理审计",
  };
  const subtitles = {
    call: "选一个应用，亲手验证模型访问规则。",
    policies: "为每个应用设置模型范围、调用上限和启用状态。",
    events: "读取当前演示环境的 P2 事件库，仅保留调用元数据。",
    audit: "查看应用权限、限额和启用状态的变更记录。",
  };
  return (
    <div className="pg-shell">
      <aside className="pg-sidebar">
        <div className="pg-brand">
          <span className="pg-logo">
            <Terminal size={20} />
          </span>
          <div>
            <strong>ItbigtecGateway</strong>
            <small>LOCAL WORKSPACE</small>
          </div>
        </div>
        <span className="pg-section-label">试用工作区</span>
        <nav aria-label="Playground 导航">
          {nav.map(([key, label, Icon]) => (
            <button
              key={key}
              aria-current={view === key ? "page" : undefined}
              onClick={() => {
                setView(key);
                setNotice("");
              }}
            >
              <Icon size={18} />
              {label}
              {view === key && <ChevronRight size={15} />}
            </button>
          ))}
        </nav>
        <div className="pg-sidebar-note">
          <ShieldCheck size={18} />
          <strong>本机离线演示</strong>
          <p>两套模拟应用，两个演示模型。所有模型回复均为合成示例。</p>
          <span className="pg-tag">不连接真实模型</span>
        </div>
        <div className="pg-sidebar-bottom">
          <span className="pg-dot" />
          127.0.0.1<span>PLAYGROUND</span>
        </div>
      </aside>
      <div className="pg-workspace">
        <header className="pg-topbar">
          <span>
            工作区 <ChevronRight size={14} /> {titles[view]}
          </span>
          <span className="pg-tag">
            <FlaskConical size={13} />
            DEMO
          </span>
        </header>
        <main className="pg-main">
          <div className="pg-page-title">
            <div>
              <div className="pg-eyebrow">MODEL ACCESS / SANDBOX</div>
              <h1>{titles[view]}</h1>
              <p>{subtitles[view]}</p>
            </div>
            <Button onClick={() => void refresh()} disabled={loading}>
              <RefreshCw size={15} className={loading ? "pg-spin" : ""} />
              刷新
            </Button>
          </div>
          <div className="pg-banner">
            <FlaskConical size={17} />
            <span>
              当前为演示环境 · 无需真实 Key · 固定假回复 ·
              本机操作员可管理合成应用
            </span>
          </div>
          {error && (
            <Card className="pg-feedback" role="alert">
              <strong>
                {state ? "刷新失败，当前显示上次数据" : "加载失败"}
              </strong>
              <p>{error}</p>
              <Button onClick={() => void refresh()}>重新加载</Button>
            </Card>
          )}
          {!state ? (
            !error && <Empty>正在加载演示环境…</Empty>
          ) : (
            <>
              {notice && (
                <div className="pg-notice" role="status">
                  <Check size={16} />
                  {notice}
                </div>
              )}
              {view === "call" && (
                <>
                  <div className="pg-metrics">
                    <div>
                      <span>模拟应用</span>
                      <strong>
                        {state.apps.length}
                        <small>个</small>
                      </strong>
                    </div>
                    <div>
                      <span>演示模型</span>
                      <strong>
                        {state.models.length}
                        <small>个</small>
                      </strong>
                    </div>
                    <div>
                      <span>本次运行到达假上游</span>
                      <strong data-testid="upstream-count">
                        {state.upstream_calls}
                        <small>次</small>
                      </strong>
                    </div>
                  </div>
                  {!state.apps.length || !state.models.length ? (
                    <Empty>暂无可用的演示应用或模型。</Empty>
                  ) : (
                    <div className="pg-call-grid">
                      <Card className="pg-input-card">
                        <div className="pg-card-title">
                          <span className="pg-step">01</span>
                          <h2>发起调用</h2>
                        </div>
                        <form onSubmit={send}>
                          <div className="pg-two-fields">
                            <label className="pg-field">
                              应用身份
                              <select
                                value={app}
                                onChange={(e) => {
                                  setApp(e.target.value);
                                  setResult(null);
                                }}
                              >
                                {state.apps.map((p) => (
                                  <option key={p.id} value={p.id}>
                                    {names[p.id] ?? p.id}
                                  </option>
                                ))}
                              </select>
                            </label>
                            <label className="pg-field">
                              调用模型
                              <select
                                value={model}
                                onChange={(e) => {
                                  setModel(e.target.value);
                                  setResult(null);
                                }}
                              >
                                {state.models.map((m) => (
                                  <option key={m} value={m}>
                                    {modelNames[m] ?? m}
                                  </option>
                                ))}
                              </select>
                            </label>
                          </div>
                          <div className="pg-policy-hint">
                            <span
                              className={
                                policy?.enabled ? "pg-dot" : "pg-dot off"
                              }
                            />
                            {policy?.enabled ? "应用已启用" : "应用已停用"}
                            <span>·</span>
                            <span>{policy?.rpm ?? "—"} 次 / 分钟</span>
                            <button
                              type="button"
                              onClick={() => setView("policies")}
                            >
                              调整权限
                              <ArrowUpRight size={13} />
                            </button>
                          </div>
                          <label className="pg-field">
                            测试输入
                            <textarea
                              value={prompt}
                              onChange={(e) => setPrompt(e.target.value)}
                              maxLength={4096}
                              rows={7}
                              placeholder="输入一条测试消息…"
                            />
                          </label>
                          <div className="pg-input-footer">
                            <small>内容仅用于本次请求，不写入事件库。</small>
                            <span>{prompt.length} / 4096</span>
                          </div>
                          <Button
                            className="pg-send"
                            type="submit"
                            disabled={busy || !prompt.trim()}
                          >
                            {busy ? (
                              <LoaderCircle className="pg-spin" />
                            ) : (
                              <Send size={16} />
                            )}
                            {busy ? "正在调用…" : "发送测试请求"}
                          </Button>
                        </form>
                      </Card>
                      <Card className="pg-result-card">
                        <div className="pg-card-title">
                          <span className="pg-step">02</span>
                          <h2>调用结果</h2>
                          {result && (
                            <span
                              className={
                                result.status === 200
                                  ? "pg-tag good"
                                  : "pg-tag bad"
                              }
                            >
                              {result.status == null
                                ? "未收到可用结果"
                                : `HTTP ${result.status}`}
                            </span>
                          )}
                        </div>
                        {busy ? (
                          <Empty>请求正在经过网关…</Empty>
                        ) : !result ? (
                          <Empty>发送请求后，在这里查看回复或拒绝原因。</Empty>
                        ) : (
                          <div className="pg-result" aria-live="polite">
                            <div
                              className={
                                result.status === 200
                                  ? "pg-result-icon"
                                  : "pg-result-icon rejected"
                              }
                            >
                              {result.status === 200 ? (
                                <Bot size={25} />
                              ) : (
                                <ShieldCheck size={25} />
                              )}
                            </div>
                            <h3>
                              {result.status === 200
                                ? "请求已放行"
                                : result.status == null
                                  ? "调用结果未知"
                                  : "请求未完成"}
                            </h3>
                            <p data-testid="call-result">
                              {result.reply ?? description(result.error)}
                            </p>
                            <div className="pg-result-meta">
                              <span>
                                应用{" "}
                                <code>
                                  <Value value={result.app} />
                                </code>
                              </span>
                              <span>
                                模型{" "}
                                <code>
                                  <Value value={result.model} />
                                </code>
                              </span>
                              <span>
                                耗时 <Value value={result.duration_ms} /> ms
                              </span>
                            </div>
                          </div>
                        )}
                      </Card>
                    </div>
                  )}
                  <div className="pg-guide">
                    <strong>试一试权限控制</strong>
                    <p>
                      切换到模型 B，观察阿汐被拒绝；在「应用权限」中授予模型 B
                      后再试。将上限改为
                      1，连续发送两次观察限流；停用应用后再次调用。
                    </p>
                  </div>
                </>
              )}
              {view === "policies" &&
                (state.apps.length ? (
                  <div className="pg-policy-grid">
                    {state.apps.map((p) => (
                      <PolicyEditor
                        key={p.id + p.revision}
                        policy={p}
                        models={state.models}
                        saved={async () => {
                          await refresh();
                          setNotice("设置已保存，后续请求按新规则检查。");
                        }}
                      />
                    ))}
                  </div>
                ) : (
                  <Empty>暂无演示应用。</Empty>
                ))}
              {view === "events" &&
                (eventError ? (
                  <Card className="pg-feedback" role="alert">
                    <strong>调用记录加载失败</strong>
                    <p>{eventError}</p>
                  </Card>
                ) : !events.length ? (
                  <Empty>暂无调用记录，先发送一次获准的测试请求。</Empty>
                ) : (
                  <Card className="pg-table-card">
                    <div className="pg-card-title">
                      <h2>最近调用</h2>
                      <small>最多 50 条 · 用量为合成数据</small>
                    </div>
                    <div className="pg-table-scroll">
                      <table>
                        <thead>
                          <tr>
                            <th>本地时间</th>
                            <th>应用身份</th>
                            <th>模型</th>
                            <th>结果</th>
                            <th>Token</th>
                            <th>用量来源</th>
                          </tr>
                        </thead>
                        <tbody>
                          {events.map((e) => (
                            <tr key={e.litellm_call_id}>
                              <td>{localTime(e.started_at)}</td>
                              <td>
                                <Value value={e.key_alias} />
                              </td>
                              <td>
                                {modelNames[e.model_alias] ?? e.model_alias}
                              </td>
                              <td>
                                <span className="pg-tag">
                                  {e.provider_outcome}
                                </span>
                              </td>
                              <td>
                                <Value value={e.total_tokens} />
                              </td>
                              <td>{e.usage_source}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </Card>
                ))}
              {view === "audit" &&
                (state.audit.length ? (
                  <Card className="pg-table-card">
                    <div className="pg-card-title">
                      <h2>管理操作</h2>
                      <small>最近 50 条</small>
                    </div>
                    <div className="pg-table-scroll">
                      <table>
                        <thead>
                          <tr>
                            <th>本地时间</th>
                            <th>操作员 / 应用</th>
                            <th>模型授权</th>
                            <th>每分钟上限</th>
                            <th>状态</th>
                            <th>版本</th>
                          </tr>
                        </thead>
                        <tbody>
                          {state.audit.map((a) => (
                            <tr key={a.sequence}>
                              <td>{localTime(a.at_utc)}</td>
                              <td>
                                <small>{a.actor}</small>
                                <br />
                                {a.target}
                              </td>
                              <td>
                                {a.models
                                  .map((m) => modelNames[m] ?? m)
                                  .join("、") || "无"}
                              </td>
                              <td>{a.rpm}</td>
                              <td>
                                <span
                                  className={
                                    a.enabled ? "pg-tag good" : "pg-tag bad"
                                  }
                                >
                                  {a.enabled ? "启用" : "停用"}
                                </span>
                              </td>
                              <td>{a.revision}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </Card>
                ) : (
                  <Empty>暂无管理操作记录。</Empty>
                ))}
            </>
          )}
          <footer className="pg-footer">
            <span>ItbigtecGateway · Playground</span>
            <span>
              时间按浏览器本地时区显示 ·{" "}
              {Intl.DateTimeFormat().resolvedOptions().timeZone}
            </span>
          </footer>
        </main>
      </div>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<Playground />);
