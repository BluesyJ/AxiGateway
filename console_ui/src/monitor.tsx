import { useEffect, useState } from "react";
import { Activity, ChevronLeft, ChevronRight, X } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

type Usage = {
  usage_source: string;
  requests: number;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  total_tokens_known: number;
};
type RecordRow = {
  litellm_call_id: string;
  model_id: string;
  name: string;
  upstream_model: string | null;
  revision: number | null;
  actor: string | null;
  started_at: string | null;
  result: string;
  provider_outcome: string;
  client_outcome: string;
  usage_source: string;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  duration_ms: number | null;
  end_to_end_ms: number | null;
  error: string | null;
  error_category: string | null;
  cache_hit_tokens: number | null;
  cache_miss_tokens: number | null;
  reasoning_tokens: number | null;
  content_status: string;
};
type ModelSummary = {
  model_id: string;
  name: string;
  requests: number;
  success: number;
  errors: number;
  avg_duration_ms: number | null;
  usage: Usage[];
};
type Records = {
  total: number;
  success: number;
  errors: number;
  items: RecordRow[];
  models: ModelSummary[];
  usage: Usage[];
  catalog: { id: string; name: string }[];
};
type AuditRow = {
  sequence: number;
  at_utc: string;
  actor: string;
  action: string;
  target: string;
  name: string;
  model: string;
  revision: number;
};
type Detail = {
  record: RecordRow;
  content: {
    status: string;
    messages: { role: string; content: string }[] | null;
    reply: string | null;
  };
};
const sources: Record<string, string> = {
  reported: "供应商报告",
  derived: "推导",
  estimated: "估算",
  missing: "缺失",
  unknown: "未知",
};
const statuses: Record<string, string> = {
  success: "成功",
  error: "失败",
  unknown: "未知",
};
const bodyStatuses: Record<string, string> = {
  saved: "已加密保存",
  disabled: "本次未开启正文保存",
  not_recorded: "历史未保存正文",
  save_failed: "正文保存失败",
  unavailable: "正文无法解密",
};
export const localTime = (value: string | null) =>
  value ? new Date(value).toLocaleString() : "—";
export const caller = (value: string | null) =>
  value === "local-operator" ? "本机操作员（未登录）" : "未知";
export function Value({
  value,
  ms = false,
}: {
  value: number | string | null | undefined;
  ms?: boolean;
}) {
  return (
    <span title={value == null ? "未知" : undefined}>
      {value == null
        ? "—"
        : typeof value === "number"
          ? value.toLocaleString(undefined, {
              maximumFractionDigits: ms ? 1 : 0,
            }) + (ms ? " ms" : "")
          : value}
    </span>
  );
}
function Badge({ value }: { value: string }) {
  return (
    <span className={"pg-tag monitor-badge " + value}>
      {statuses[value] ?? sources[value] ?? value}
    </span>
  );
}
export async function localApi(path: string) {
  const response = await fetch("/api/local/" + path, {
    headers: { "X-Playground": "1" },
  });
  if (!response.ok) throw new Error("查询失败，请检查本机服务后重试。");
  return response.json();
}
function Pager({
  page,
  total,
  setPage,
}: {
  page: number;
  total: number;
  setPage: (page: number) => void;
}) {
  return (
    <div className="pg-row monitor-pager">
      <span>
        共 {total} 条 · 第 {page} / {Math.max(1, Math.ceil(total / 20))} 页
      </span>
      <div className="pg-row">
        <Button disabled={page <= 1} onClick={() => setPage(page - 1)}>
          <ChevronLeft size={15} />
          上一页
        </Button>
        <Button disabled={page * 20 >= total} onClick={() => setPage(page + 1)}>
          下一页
          <ChevronRight size={15} />
        </Button>
      </div>
    </div>
  );
}
function UsageTable({ rows }: { rows: Usage[] }) {
  return (
    <div className="pg-table-wrap">
      <table className="pg-table">
        <thead>
          <tr>
            <th>用量来源</th>
            <th>请求数</th>
            <th>输入 token</th>
            <th>输出 token</th>
            <th>总 token</th>
            <th>总用量已知请求</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.usage_source}>
              <td>
                <Badge value={r.usage_source} />
                <small>{r.usage_source}</small>
              </td>
              <td>{r.requests}</td>
              <td>
                <Value value={r.prompt_tokens} />
              </td>
              <td>
                <Value value={r.completion_tokens} />
              </td>
              <td>
                <Value value={r.total_tokens} />
              </td>
              <td>
                {r.total_tokens_known} / {r.requests}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Monitoring({
  view,
  refresh,
}: {
  view: "records" | "operations";
  refresh: number;
}) {
  const [page, setPage] = useState(1);
  const [window, setWindow] = useState("all");
  const [model, setModel] = useState("");
  const [result, setResult] = useState("");
  const [source, setSource] = useState("");
  const [actor, setActor] = useState("");
  const [data, setData] = useState<Records | null>(null);
  const [audit, setAudit] = useState<{
    items: AuditRow[];
    total: number;
  } | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const [selected, setSelected] = useState<RecordRow | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [detailError, setDetailError] = useState("");
  useEffect(() => {
    setPage(1);
    setSelected(null);
  }, [window, model, result, source, actor]);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    const params = new URLSearchParams({ page: String(page), size: "20" });
    if (window !== "all")
      params.set(
        "start",
        new Date(Date.now() - Number(window) * 3600000).toISOString(),
      );
    for (const [key, value] of [
      ["model", model],
      ["result", result],
      ["usage_source", source],
      ["caller", actor],
    ])
      if (value) params.set(key, value);
    const path =
      view === "records" ? "records?" + params : "audit?page=" + page;
    localApi(path)
      .then((value) => {
        if (active) {
          if (view === "records") setData(value);
          else setAudit(value);
        }
      })
      .catch(() => {
        if (active) setError("查询失败，请检查本机服务后重试。");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [view, refresh, page, window, model, result, source, actor, retry]);
  useEffect(() => {
    let active = true;
    setDetail(null);
    setDetailError("");
    if (selected)
      localApi(
        `records/${selected.model_id}/${selected.litellm_call_id}?content=1`,
      )
        .then((value) => {
          if (active) setDetail(value);
        })
        .catch(() => {
          if (active) setDetailError("详情加载失败，请关闭后重新打开。");
        });
    return () => {
      active = false;
    };
  }, [selected]);
  return (
    <div className="monitor-stack">
      {view === "records" && (
        <Card className="monitor-filters">
          <label className="pg-field">
            时间范围
            <select value={window} onChange={(e) => setWindow(e.target.value)}>
              <option value="all">全部历史</option>
              <option value="24">最近24小时</option>
              <option value="168">最近7天</option>
              <option value="720">最近30天</option>
            </select>
          </label>
          <label className="pg-field">
            筛选模型
            <select value={model} onChange={(e) => setModel(e.target.value)}>
              <option value="">全部模型</option>
              {data?.catalog.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
          </label>
          <label className="pg-field">
            调用结果
            <select value={result} onChange={(e) => setResult(e.target.value)}>
              <option value="">全部结果</option>
              {Object.entries(statuses).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="pg-field">
            用量来源
            <select value={source} onChange={(e) => setSource(e.target.value)}>
              <option value="">全部来源</option>
              {Object.entries(sources).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="pg-field">
            调用人
            <select value={actor} onChange={(e) => setActor(e.target.value)}>
              <option value="">全部调用人</option>
              <option value="local-operator">本机操作员（未登录）</option>
              <option value="unknown">未知</option>
            </select>
          </label>
        </Card>
      )}
      {error && (
        <Card className="pg-feedback" role="alert">
          <strong>记录加载失败</strong>
          <p>{error}</p>
          <Button onClick={() => setRetry((v) => v + 1)}>重新加载记录</Button>
        </Card>
      )}
      {loading && (
        <Card className="pg-empty" role="status">
          正在加载记录…
        </Card>
      )}
      {!loading && !error && view === "records" && data && (
        <>
          <div className="monitor-metrics">
            {[
              ["请求数", data.total],
              ["成功", data.success],
              ["失败", data.errors],
              ["结果未知", data.total - data.success - data.errors],
            ].map(([label, count]) => (
              <Card key={label} className="monitor-metric">
                <span>{label}</span>
                <strong>{count}</strong>
              </Card>
            ))}
          </div>
          <Card className="monitor-card">
            <h2>Token 用量</h2>
            <p>
              各来源分别统计；缺失显示为 —。这是网关用量记录，不是供应商账单。
            </p>
            <UsageTable rows={data.usage} />
          </Card>
          <Card className="monitor-card">
            <h2>按模型统计</h2>
            {!data.models.length ? (
              <p className="pg-empty">此筛选范围内暂无调用记录。</p>
            ) : (
              <div className="pg-table-wrap">
                <table className="pg-table">
                  <thead>
                    <tr>
                      <th>模型</th>
                      <th>请求数</th>
                      <th>成功 / 失败 / 未知</th>
                      <th>平均调用耗时</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.models.map((m) => (
                      <tr key={m.model_id}>
                        <td>
                          <strong>{m.name}</strong>
                          <details>
                            <summary>查看模型 token</summary>
                            <UsageTable rows={m.usage} />
                          </details>
                        </td>
                        <td>{m.requests}</td>
                        <td>
                          {m.success} / {m.errors} /{" "}
                          {m.requests - m.success - m.errors}
                        </td>
                        <td>
                          <Value value={m.avg_duration_ms} ms />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
          <Card className="monitor-card">
            <h2>请求明细</h2>
            <p>
              新请求显示端到端耗时，含首次网关启动；历史请求使用事件耗时。事件写入有短暂延迟。
            </p>
            <div className="pg-table-wrap">
              <table className="pg-table">
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>模型 / 调用人</th>
                    <th>结果</th>
                    <th>输入 / 输出 token</th>
                    <th>总 token / 来源</th>
                    <th>调用耗时</th>
                    <th>详情</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((r) => (
                    <tr key={r.litellm_call_id}>
                      <td>{localTime(r.started_at)}</td>
                      <td>
                        <strong>{r.name}</strong>
                        <small>{caller(r.actor)}</small>
                      </td>
                      <td>
                        <Badge value={r.result} />
                        {r.client_outcome === "client_disconnected" && (
                          <span className="pg-tag error">客户端断开</span>
                        )}
                      </td>
                      <td>
                        <Value value={r.prompt_tokens} /> /{" "}
                        <Value value={r.completion_tokens} />
                      </td>
                      <td>
                        <Value value={r.total_tokens} />
                        <small>
                          <Badge value={r.usage_source} />
                        </small>
                      </td>
                      <td>
                        <Value value={r.end_to_end_ms ?? r.duration_ms} ms />
                      </td>
                      <td>
                        <Button
                          onClick={() => setSelected(r)}
                          aria-label={"查看调用详情 " + r.litellm_call_id}
                        >
                          详情
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pager page={page} total={data.total} setPage={setPage} />
          </Card>
        </>
      )}
      {!loading && !error && view === "operations" && audit && (
        <Card className="monitor-card">
          <h2>模型配置操作</h2>
          <p>
            记录添加、修改和删除操作；调用人是本机操作员，尚无企业登录。启用此功能前的操作不补造。
          </p>
          {!audit.items.length ? (
            <div className="pg-empty">暂无模型配置操作记录。</div>
          ) : (
            <div className="pg-table-wrap">
              <table className="pg-table">
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>操作人</th>
                    <th>操作</th>
                    <th>模型</th>
                    <th>配置版本</th>
                  </tr>
                </thead>
                <tbody>
                  {audit.items.map((r) => (
                    <tr key={r.sequence}>
                      <td>{localTime(r.at_utc)}</td>
                      <td>{caller(r.actor)}</td>
                      <td>
                        {{
                          create: "添加模型",
                          update: "修改模型",
                          delete: "删除模型",
                        }[r.action] ?? r.action}
                      </td>
                      <td>
                        {r.name}
                        <small>{r.model}</small>
                      </td>
                      <td>{r.revision}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <Pager page={page} total={audit.total} setPage={setPage} />
        </Card>
      )}
      {selected && (
        <div className="monitor-overlay">
          <section
            role="dialog"
            aria-modal="true"
            aria-label="调用详情"
            className="monitor-detail"
          >
            <div className="pg-row">
              <h2>
                <Activity size={20} />
                调用详情
              </h2>
              <Button onClick={() => setSelected(null)} aria-label="关闭详情">
                <X size={18} />
              </Button>
            </div>
            {detailError ? (
              <p role="alert">{detailError}</p>
            ) : !detail ? (
              <p role="status">正在加载详情…</p>
            ) : (
              <>
                <dl className="monitor-facts">
                  {[
                    ["调用 ID", detail.record.litellm_call_id],
                    ["模型", detail.record.name],
                    ["上游模型", detail.record.upstream_model],
                    ["配置版本", detail.record.revision],
                    ["调用人", caller(detail.record.actor)],
                    ["调用时间", localTime(detail.record.started_at)],
                    ["调用结果", statuses[detail.record.result]],
                    ["供应商结果", statuses[detail.record.provider_outcome]],
                    [
                      "客户端结果",
                      detail.record.client_outcome === "unknown"
                        ? null
                        : detail.record.client_outcome,
                    ],
                    ["端到端耗时（ms）", detail.record.end_to_end_ms],
                    ["事件耗时（ms）", detail.record.duration_ms],
                    ["输入 token", detail.record.prompt_tokens],
                    ["输出 token", detail.record.completion_tokens],
                    ["总 token", detail.record.total_tokens],
                    ["用量来源", sources[detail.record.usage_source]],
                    ["缓存命中", detail.record.cache_hit_tokens],
                    ["缓存未命中", detail.record.cache_miss_tokens],
                    [
                      "推理 token（包含在输出中）",
                      detail.record.reasoning_tokens,
                    ],
                    [
                      "错误类别",
                      detail.record.error ?? detail.record.error_category,
                    ],
                  ].map(([key, value]) => (
                    <div key={key}>
                      <dt>{key}</dt>
                      <dd>
                        <Value value={value} />
                      </dd>
                    </div>
                  ))}
                </dl>
                <h3>调用内容</h3>
                <p>{bodyStatuses[detail.content.status] ?? "未记录正文"}</p>
                {detail.content.messages?.map((m, i) => (
                  <div className="pg-message" key={i}>
                    <strong>{m.role}</strong>
                    <p>{m.content}</p>
                  </div>
                ))}
                {detail.content.reply !== null && (
                  <div className="pg-message assistant">
                    <strong>模型回复</strong>
                    <p>{detail.content.reply}</p>
                  </div>
                )}
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
