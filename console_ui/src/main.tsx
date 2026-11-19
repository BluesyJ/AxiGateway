import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  BarChart3,
  Boxes,
  ChevronRight,
  Database,
  Grid2X2,
  List,
  PanelLeft,
  RefreshCw,
  ScanLine,
  Users,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Content } from "./panels";
import {
  apiURL,
  displayZone,
  filterFields,
  ranges,
  sourceNames,
  sources,
  titles,
  type Data,
  type View,
} from "./data";
import "./style.css";

const nav = [
  {
    section: "监控",
    items: [
      ["overview", Grid2X2],
      ["trend", BarChart3],
      ["requests", List],
    ],
  },
  {
    section: "统计",
    items: [
      ["models", Boxes],
      ["clients", Users],
    ],
  },
  { section: "诊断", items: [["health", Activity]] },
] as const;
function Filters({
  params,
  apply,
}: {
  params: URLSearchParams;
  apply: (updates: Record<string, string>) => void;
}) {
  return (
    <form
      className="filters"
      onSubmit={(e) => {
        e.preventDefault();
        apply({
          ...(Object.fromEntries(new FormData(e.currentTarget)) as Record<
            string,
            string
          >),
          page: "1",
        });
      }}
    >
      <label>
        模型
        <input
          name="model"
          placeholder="模型别名"
          maxLength={64}
          defaultValue={params.get("model") ?? ""}
        />
      </label>
      <label>
        客户端
        <input
          name="client"
          placeholder="任一标签"
          maxLength={64}
          defaultValue={params.get("client") ?? ""}
        />
      </label>
      <label>
        供应商结果
        <select
          name="provider_outcome"
          defaultValue={params.get("provider_outcome") ?? ""}
        >
          <option value="">全部结果</option>
          <option value="success">成功</option>
          <option value="error">失败</option>
          <option value="unknown">未知</option>
        </select>
      </label>
      <label>
        客户端结果
        <select
          name="client_outcome"
          defaultValue={params.get("client_outcome") ?? ""}
        >
          <option value="">全部结果</option>
          <option value="unknown">未知</option>
          <option value="client_disconnected">客户端断开</option>
        </select>
      </label>
      <label>
        用量来源
        <select
          name="usage_source"
          defaultValue={params.get("usage_source") ?? ""}
        >
          <option value="">全部来源</option>
          {sources.map((s) => (
            <option key={s} value={s}>
              {sourceNames[s]}
            </option>
          ))}
        </select>
      </label>
      <Button type="submit" variant="outline">
        筛选
      </Button>
      <Button
        type="button"
        variant="ghost"
        onClick={() =>
          apply(
            Object.fromEntries([...filterFields, "page"].map((k) => [k, ""])),
          )
        }
      >
        重置
      </Button>
    </form>
  );
}
function App() {
  const [search, setSearch] = useState(window.location.search);
  const [revision, refresh] = useState(0);
  const params = new URLSearchParams(search);
  const raw = params.get("view") ?? "overview";
  const view: View = Object.hasOwn(titles, raw) ? (raw as View) : "overview";
  const source = params.get("source") === "live" ? "live" : "demo";
  const range = params.get("range") ?? "all";
  const [state, setState] = useState<{
    key: string;
    data?: Data;
    error?: boolean;
  }>({ key: "" });
  const key = search + "|" + revision;
  function update(updates: Record<string, string>) {
    const next = new URLSearchParams(search);
    Object.entries(updates).forEach(([k, v]) =>
      v ? next.set(k, v) : next.delete(k),
    );
    const query = "?" + next.toString();
    window.history.pushState(null, "", query);
    setSearch(query);
  }
  useEffect(() => {
    const pop = () => setSearch(window.location.search);
    window.addEventListener("popstate", pop);
    return () => window.removeEventListener("popstate", pop);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    fetch(apiURL(new URLSearchParams(search), view, source), {
      signal: controller.signal,
      cache: "no-store",
      credentials: "omit",
    })
      .then(async (r) => {
        if (!r.ok) throw Error();
        const data = (await r.json()) as Data;
        if (data.view !== view) throw Error();
        if (!controller.signal.aborted) setState({ key, data });
      })
      .catch(() => {
        if (!controller.signal.aborted) setState({ key, error: true });
      });
    return () => controller.abort();
  }, [search, revision, source, view, key]);
  const data = state.key === key ? state.data : undefined;
  const failed = state.key === key && state.error;
  const display = failed
    ? "error"
    : !data
      ? "loading"
      : data.empty
        ? "empty"
        : "normal";
  const section = nav.find((n) => n.items.some((i) => i[0] === view))!.section;
  return (
    <div className="shell">
      <aside>
        <div className="brand">
          <span className="brand-mark">
            <Activity size={19} />
          </span>
          <strong>AxiGateway</strong>
        </div>
        <nav aria-label="页面">
          {nav.map((group) => (
            <div className="nav-group" key={group.section}>
              <small>{group.section}</small>
              {group.items.map(([v, Icon]) => (
                <button
                  key={v}
                  aria-current={view === v ? "page" : undefined}
                  onClick={() => update({ view: v, page: "" })}
                >
                  <Icon size={16} />
                  {titles[v]}
                </button>
              ))}
            </div>
          ))}
        </nav>
        <div className="side-foot">
          <Database size={15} />
          <span>
            SQLite <span className="sep">/</span> 只读
          </span>
        </div>
      </aside>
      <main>
        <header>
          <div className="crumb">
            <PanelLeft size={16} />
            <span>控制台</span>
            <ChevronRight size={12} />
            {section}
          </div>
          <div className="source">
            <Database size={14} />
            <select
              aria-label="数据源"
              value={source}
              onChange={(e) => update({ source: e.target.value, page: "" })}
            >
              <option value="demo">演示库</option>
              <option value="live">实际事件库</option>
            </select>
          </div>
        </header>
        <div className="workspace">
          <div className="page-head">
            <div>
              <div className="kicker">
                {section} <span>/</span>{" "}
                <span className="mono">
                  {String(Object.keys(titles).indexOf(view) + 1).padStart(
                    2,
                    "0",
                  )}
                </span>
              </div>
              <h1>{titles[view]}</h1>
            </div>
            <div className="toolbar">
              {view !== "health" && (
                <select
                  aria-label="时间范围"
                  value={range}
                  onChange={(e) => update({ range: e.target.value, page: "" })}
                >
                  {Object.entries(ranges).map(([v, n]) => (
                    <option key={v} value={v}>
                      {n}
                    </option>
                  ))}
                </select>
              )}
              <Button variant="outline" onClick={() => refresh((n) => n + 1)}>
                <RefreshCw size={14} />
                刷新
              </Button>
            </div>
          </div>
          {view === "requests" && (
            <Filters key={search} params={params} apply={update} />
          )}
          <section className="content" data-state={display} aria-live="polite">
            {display !== "normal" && (
              <div className={"state " + display}>
                <ScanLine size={24} />
                <p>
                  {failed
                    ? "无法读取事件库，请检查所选库后刷新。"
                    : !data
                      ? "正在读取事件库…"
                      : view === "health"
                        ? "当前库尚无采集计数，请在采集后刷新。"
                        : "当前条件下没有请求记录，请调整筛选条件或切换数据源。"}
                </p>
                {failed && (
                  <Button
                    variant="outline"
                    onClick={() => refresh((n) => n + 1)}
                  >
                    重试
                  </Button>
                )}
              </div>
            )}
            {data && (!data.empty || data.view === "overview") && (
              <Content
                data={data}
                goPage={(page) => update({ page: String(page) })}
              />
            )}
          </section>
          <footer>
            <span>
              北京时间 <b>{displayZone}</b>
            </span>
            <span>
              {source === "demo" ? "demo-events.sqlite3" : "events.sqlite3"}
            </span>
          </footer>
        </div>
      </main>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
