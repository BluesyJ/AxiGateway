export const sources = [
  "reported",
  "derived",
  "estimated",
  "missing",
  "unknown",
] as const;
export type Source = (typeof sources)[number];
export const sourceNames: Record<Source, string> = {
  reported: "实报",
  derived: "推导",
  estimated: "推估",
  missing: "缺失",
  unknown: "未证实",
};
export const titles = {
  overview: "流量总览",
  trend: "流量趋势",
  requests: "请求列表",
  models: "模型汇总",
  clients: "客户端汇总",
  health: "采集健康",
};
export type View = keyof typeof titles;
export type Num = number | null;
export interface Usage {
  usage_source: Source;
  requests: number;
  known: number;
  tokens: Num;
}
export interface Point extends Usage {
  hour: string | null;
}
export interface Group extends Usage {
  model_alias?: string | null;
  client_header?: string | null;
  client_metadata?: string | null;
}
export interface Estimate {
  cost_currency: string | null;
  cost_source: string | null;
  price_version: string | null;
  amount: Num;
  known: number;
  requests: number;
}
export interface EventRow {
  litellm_call_id: string | null;
  started_at: string | null;
  model_alias: string | null;
  client_header: string | null;
  client_metadata: string | null;
  stream: 0 | 1;
  provider_outcome: string;
  client_outcome: string;
  usage_source: Source;
  total_tokens: Num;
  duration_ms: Num;
}
export interface Gap {
  reason: string;
  count: number;
  first_at: string | null;
  last_at: string | null;
}
type Details =
  | {
      view: "overview";
      requests: number;
      success: number;
      errors: number;
      usage: Usage[];
      disconnected_estimated: { requests: number; known: number; total_tokens: Num; label: string };
      costs: Estimate[];
      trend: Point[];
      lost: Num;
      auth_rejected: Num;
      gap_windows: number;
    }
  | { view: "trend"; items: Point[] }
  | {
      view: "requests";
      items: EventRow[];
      page: number;
      size: number;
      total: number;
    }
  | { view: "models" | "clients"; items: Group[] }
  | {
      view: "health";
      counters: Record<string, Num>;
      lost: Num;
      gap_windows: number;
      gap_events: number;
      queue_depth: Num;
      gaps: Gap[];
    };
export type Data = Details & { empty: boolean };
export const fmt = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 6 });
export const displayZone = "Asia/Shanghai";
export const localTime = new Intl.DateTimeFormat(undefined, {
  timeZone: displayZone,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
export const localHour = new Intl.DateTimeFormat(undefined, {
  timeZone: displayZone,
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  hour12: false,
});
export const ranges: Record<string, string> = {
  "24": "最近 24 小时",
  "168": "最近 7 天",
  "720": "最近 30 天",
  all: "全部时间",
};
export const reasons: Record<string, string> = {
  queue_full: "队列已满",
  projection_failed: "投影失败",
  sink_failed: "事件写入失败",
  rejected_after_close: "关闭后拒绝",
};
export const filterFields = [
  "model",
  "client",
  "provider_outcome",
  "client_outcome",
  "usage_source",
] as const;

export function apiURL(
  params: URLSearchParams,
  view: View,
  source: string,
): string {
  const query = new URLSearchParams();
  const range = params.get("range") ?? "all";
  if (view !== "health" && range !== "all" && Object.hasOwn(ranges, range)) {
    const end = Date.now();
    query.set("start", new Date(end - Number(range) * 3600000).toISOString());
    query.set("end", new Date(end).toISOString());
  }
  if (view === "requests") {
    for (const name of filterFields) {
      const value = params.get(name);
      if (value) query.set(name, value);
    }
    query.set("page", params.get("page") ?? "1");
    query.set("size", "15");
  }
  return "/api/" + source + "/" + view + (query.size ? "?" + query : "");
}
