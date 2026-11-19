import { useState, type ReactNode } from "react";
import { ArrowLeft, ArrowRight, ChartNoAxesCombined } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Plot } from "./charts";
import {
  displayZone,
  fmt,
  localTime,
  reasons,
  sourceNames,
  sources,
  type Data,
  type Source,
  type Point,
} from "./data";

export function Value({
  value,
  time = false,
}: {
  value: number | string | null | undefined;
  time?: boolean;
}) {
  if (value == null)
    return (
      <span className="null" title="未知" aria-label="未知">
        —
      </span>
    );
  if (time)
    return (
      <time
        dateTime={String(value)}
        title={new Date(value).toLocaleString(undefined, { timeZone: displayZone })}
      >
        {localTime.format(new Date(value))}
      </time>
    );
  return (
    <span className="mono">
      {typeof value === "number" ? fmt.format(value) : value}
    </span>
  );
}
export function Badge({ value }: { value: string }) {
  const names: Record<string, string> = {
    ...sourceNames,
    success: "成功",
    error: "失败",
    client_disconnected: "客户端断开",
  };
  return (
    <span className="badge" title={value}>
      <i className={"dot " + value} />
      {names[value] ?? value}
      <span className="sr-only"> {value}</span>
    </span>
  );
}
function Panel({
  title,
  note,
  children,
}: {
  title: string;
  note?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Card className="panel">
      <div className="panel-head">
        <h2>{title}</h2>
        {note && <span className="muted">{note}</span>}
      </div>
      {children}
    </Card>
  );
}
function Metrics({ items }: { items: [string, number | null, string?][] }) {
  return (
    <div className="metrics">
      {items.map(([title, value, mark]) => (
        <Card className="metric" key={title}>
          <div>
            {mark && <i className={"dot " + mark} />} {title}
          </div>
          <strong>
            <Value value={value} />
          </strong>
        </Card>
      ))}
    </div>
  );
}
function Table({ headers, rows }: { headers: string[]; rows: ReactNode[][] }) {
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            {headers.map((h) => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((cells, i) => (
            <tr key={i}>
              {cells.map((cell, j) => (
                <td key={j}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function Coverage({ known, requests }: { known: number; requests: number }) {
  return (
    <span className="coverage mono" title="有已知数值的请求 / 此组请求">
      {known} / {requests}
    </span>
  );
}
function PlotPanel({
  items,
  token = false,
}: {
  items: Point[];
  token?: boolean;
}) {
  const [source, select] = useState<Source>("reported");
  return (
    <Panel
      title={token ? "Token 趋势" : "请求数趋势"}
      note={
        token ? (
          <select
            aria-label="趋势用量来源"
            value={source}
            onChange={(e) => select(e.target.value as Source)}
          >
            {sources.map((s) => (
              <option key={s} value={s}>
                {sourceNames[s]} · {s}
              </option>
            ))}
          </select>
        ) : (
          <>
            <ChartNoAxesCombined size={14} /> 每小时
          </>
        )
      }
    >
      <Plot items={items} source={token ? source : undefined} />
    </Panel>
  );
}
export function Content({
  data,
  goPage,
}: {
  data: Data;
  goPage: (page: number) => void;
}) {
  switch (data.view) {
    case "overview":
      return (
        <>
          <Metrics
            items={[
              ["请求数", data.requests],
              ["供应商成功", data.success, "success"],
              ["供应商失败", data.errors, "error"],
              ["已记录缺口窗口 · 累计", data.gap_windows],
            ]}
          />
          <Panel title="Token 用量" note="按来源 · 已知数值小计">
            <div className="token-grid">
              {data.usage.map((u) => (
                <div key={u.usage_source}>
                  <small className="mono">{u.usage_source}</small>
                  <span>{sourceNames[u.usage_source]}</span>
                  <strong>
                    <Value value={u.tokens} />
                  </strong>
                  <small>
                    已知 <Coverage {...u} />
                  </small>
                </div>
              ))}
            </div>
          </Panel>
          {data.disconnected_estimated?.requests > 0 && (
            <Panel title="其中：客户端断开的推估用量" note={data.disconnected_estimated.label}>
              <Table headers={["请求数", "Token 已知小计", "已知 / 请求"]} rows={[[
                <Value value={data.disconnected_estimated.requests} />,
                <Value value={data.disconnected_estimated.total_tokens} />,
                <Coverage {...data.disconnected_estimated} />,
              ]]} />
            </Panel>
          )}
          {data.trend.length > 0 && <PlotPanel items={data.trend} />}
          <Panel title="费用估算（非账单）" note="按币种、来源和价目版本">
            {data.costs.length ? (
              <Table
                headers={[
                  "币种",
                  "费用来源",
                  "价目版本",
                  "已知金额小计",
                  "已知 / 请求",
                ]}
                rows={data.costs.map((c) => [
                  <Value value={c.cost_currency} />,
                  <Value value={c.cost_source} />,
                  <Value value={c.price_version} />,
                  <Value value={c.amount} />,
                  <Coverage {...c} />,
                ])}
              />
            ) : (
              <p className="inline-empty">
                当前范围没有费用记录，请调整时间范围。
              </p>
            )}
          </Panel>
          <div className="status-strip">
            <span>采集累计</span>
            <span>
              事件丢失{" "}
              <b>
                <Value value={data.lost} />
              </b>
            </span>
            <span>
              转发前拒绝{" "}
              <b>
                <Value value={data.auth_rejected} />
              </b>
            </span>
          </div>
        </>
      );
    case "trend":
      return (
        <>
          <PlotPanel items={data.items} />
          <PlotPanel items={data.items} token />
        </>
      );
    case "requests":
      return (
        <Panel title="请求记录" note={<>{fmt.format(data.total)} 条</>}>
          <Table
            headers={[
              "时间 / 调用 ID",
              "模型",
              "Header / Metadata",
              "供应商结果",
              "客户端结果",
              "用量来源",
              "Token",
              "耗时 ms",
            ]}
            rows={data.items.map((e) => [
              <div className="stack">
                <Value value={e.started_at} time />
                <small className="mono id" title={e.litellm_call_id ?? "未知"}>
                  {e.litellm_call_id?.slice(0, 12) ?? <Value value={null} />}
                </small>
              </div>,
              <div className="stack">
                <Value value={e.model_alias} />
                <small>{e.stream ? "SSE" : "HTTP"}</small>
              </div>,
              <div className="stack">
                <Value value={e.client_header} />
                <small>
                  <Value value={e.client_metadata} />
                </small>
              </div>,
              <Badge value={e.provider_outcome} />,
              e.client_outcome === "unknown" ? (
                <span className="badge" title="未知">
                  <i className="dot" />
                  <Value value={null} />
                </span>
              ) : (
                <Badge value={e.client_outcome} />
              ),
              <div className="stack">
                <Badge value={e.usage_source} />
                {e.client_outcome === "client_disconnected" && e.usage_source === "estimated" && (
                  <small>估算（不可靠，通常明显偏低）</small>
                )}
              </div>,
              <Value value={e.total_tokens} />,
              <Value value={e.duration_ms} />,
            ])}
          />
          <div className="pager">
            <span>每页 {data.size} 条</span>
            <div>
              <Button
                variant="outline"
                disabled={data.page <= 1}
                aria-label="上一页"
                onClick={() => goPage(data.page - 1)}
              >
                <ArrowLeft size={14} />
              </Button>
              <span className="mono">
                {data.page} / {Math.max(1, Math.ceil(data.total / data.size))}
              </span>
              <Button
                variant="outline"
                disabled={data.page * data.size >= data.total}
                aria-label="下一页"
                onClick={() => goPage(data.page + 1)}
              >
                <ArrowRight size={14} />
              </Button>
            </div>
          </div>
        </Panel>
      );
    case "models":
    case "clients": {
      const model = data.view === "models";
      return (
        <Panel
          title={model ? "按模型统计" : "按客户端标签统计"}
          note="按用量来源分组"
        >
          <Table
            headers={[
              ...(model ? ["模型别名"] : ["Header 标签", "Metadata 标签"]),
              "用量来源",
              "请求数",
              "Token 已知小计",
              "已知 / 请求",
            ]}
            rows={data.items.map((r) => [
              ...(model
                ? [<Value value={r.model_alias} />]
                : [
                    <Value value={r.client_header} />,
                    <Value value={r.client_metadata} />,
                  ]),
              <Badge value={r.usage_source} />,
              <Value value={r.requests} />,
              <Value value={r.tokens} />,
              <Coverage {...r} />,
            ])}
          />
        </Panel>
      );
    }
    case "health":
      return (
        <>
          <Metrics
            items={[
              ["已入队 · 累计", data.counters.accepted],
              ["已提交观察 · 累计", data.counters.written],
              ["事件丢失 · 累计", data.lost, "estimated"],
              ["队列深度 · 未持久化", data.queue_depth],
            ]}
          />
          <Panel title="采集计数器" note="数据库累计">
            <Table
              headers={["字段", "计数器", "累计数"]}
              rows={Object.entries({
                ...reasons,
                storage_failed: "事务写失败",
                auth_rejected: "转发前拒绝",
              }).map(([k, v]) => [
                v,
                <code>{k}</code>,
                <Value value={data.counters[k]} />,
              ])}
            />
          </Panel>
          <Panel
            title="丢失窗口"
            note={
              <>
                最近 50 个 · 累计 {data.gap_windows} 个 / {data.gap_events} 条
              </>
            }
          >
            {data.gaps.length ? (
              <Table
                headers={["原因", "丢失数量", "首次观察", "末次观察"]}
                rows={data.gaps.map((g) => [
                  reasons[g.reason] ?? "未知",
                  <Value value={g.count} />,
                  <Value time value={g.first_at} />,
                  <Value time value={g.last_at} />,
                ])}
              />
            ) : (
              <p className="inline-empty">
                尚无已记录的丢失窗口，请在采集后刷新。
              </p>
            )}
          </Panel>
        </>
      );
  }
}
