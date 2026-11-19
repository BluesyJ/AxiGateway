import {
  LineChart,
  Line,
  ResponsiveContainer,
  CartesianGrid,
  XAxis,
  YAxis,
  Tooltip,
} from "recharts";
import { fmt, localHour, localTime, type Point, type Source } from "./data";

export function Plot({ items, source }: { items: Point[]; source?: Source }) {
  const buckets = new Map<
    string,
    { hour: string; value: number | null; known: number; requests: number }
  >();
  for (const item of items) {
    if (!item.hour) continue;
    const point = buckets.get(item.hour) ?? {
      hour: item.hour,
      value: null,
      known: 0,
      requests: 0,
    };
    buckets.set(item.hour, point);
    if (source && source !== item.usage_source) continue;
    point.requests += item.requests;
    point.known += item.known;
    const value = source ? item.tokens : item.requests;
    if (value !== null) point.value = (point.value ?? 0) + value;
  }
  const points = [...buckets.values()].sort((a, b) =>
    a.hour.localeCompare(b.hour),
  );
  if (!points.some((p) => p.value !== null))
    return (
      <p className="chart-empty">
        当前范围没有可绘制的数值，请切换时间或用量来源。
      </p>
    );
  return (
    <div
      className="plot"
      aria-label={source ? "Token 时间趋势" : "请求数时间趋势"}
    >
      <ResponsiveContainer width="100%" height="100%" minWidth={0}>
        <LineChart
          data={points}
          margin={{ top: 12, right: 20, left: -6, bottom: 4 }}
        >
          <CartesianGrid
            vertical={false}
            stroke="#24303b"
            strokeDasharray="2 5"
          />
          <XAxis
            dataKey="hour"
            tickFormatter={(x) => localHour.format(new Date(x))}
            minTickGap={32}
            padding={{ left: 8, right: 8 }}
            axisLine={false}
            tickLine={false}
            tick={{ fill: "#8a98a7", fontSize: 10 }}
          />
          <YAxis
            allowDecimals={false}
            axisLine={false}
            tickLine={false}
            tick={{ fill: "#8a98a7", fontSize: 10 }}
            width={52}
          />
          <Tooltip
            isAnimationActive={false}
            cursor={{ stroke: "#667586", strokeDasharray: "3 4" }}
            content={({ active, payload }) => {
              const p = payload?.[0]?.payload as
                (typeof points)[number] | undefined;
              return active && p ? (
                <div className="tip">
                  <time>{localTime.format(new Date(p.hour))}</time>
                  <div>
                    {source ? "Token" : "请求数"}{" "}
                    <b>{p.value === null ? "—" : fmt.format(p.value)}</b>
                  </div>
                  {source && (
                    <small>
                      已知 {p.known} / {p.requests} 条
                    </small>
                  )}
                </div>
              ) : null;
            }}
          />
          <Line
            type="linear"
            dataKey="value"
            stroke="#22d3ee"
            strokeWidth={1.5}
            dot={{ r: 2, fill: "#22d3ee", strokeWidth: 0 }}
            connectNulls={false}
            activeDot={{ r: 4 }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
