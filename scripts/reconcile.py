"""Read-only, source-separated reconciliation against manually supplied totals."""

import argparse
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from axi_gateway.queries import disconnected_estimates, read_connection

SOURCES = ("reported", "derived", "estimated", "missing", "unknown")
TOKENS = ("prompt_tokens", "completion_tokens", "total_tokens")
TOKEN_SUMS = ",".join(
    f"sum({k}) AS {k}, count({k}) AS {k}_known_requests" for k in TOKENS
)
# Event timestamps are written as UTC datetime.isoformat(). Direct comparisons
# retain microseconds at hour boundaries and use the existing started_at index.
WINDOW = " WHERE started_at>=? AND started_at<?"


def timestamp(value: str) -> datetime:
    """Require an explicit UTC offset; normalize the half-open window to UTC."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timezone_required")
    return parsed.astimezone(UTC)


def reported_hours(
    db: sqlite3.Connection, start: datetime, end: datetime
) -> list[dict]:
    """Split [start,end) at whole hours in the start argument's fixed UTC offset."""
    end = end.astimezone(start.tzinfo)
    hours = []
    while start < end:
        stop = min(
            end, start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        )
        row = db.execute(
            "SELECT count(*) AS requests,"
            + TOKEN_SUMS
            + " FROM events"
            + WINDOW
            + " AND usage_source='reported'",
            (start.astimezone(UTC).isoformat(), stop.astimezone(UTC).isoformat()),
        ).fetchone()
        hours.append(
            {
                "start": start.isoformat(),
                "end": stop.isoformat(),
                "timezone": start.tzname(),
                "reported": dict(row),
            }
        )
        start = stop
    return hours


def reconcile(
    path: Path, start: str, end: str, console: dict[str, int], *, by_hour: bool = False
) -> dict:
    """Compare complete reported quantities only, without estimating missing data."""
    lower, upper = timestamp(start), timestamp(end)
    if lower >= upper or not set(console) <= set(TOKENS):
        raise ValueError("invalid_window_or_fields")
    if any(type(v) is not int or v < 0 for v in console.values()):
        raise ValueError("invalid_console_count")
    summary = {
        source: {
            "requests": 0,
            **{k: None for k in TOKENS},
            **{k + "_known_requests": 0 for k in TOKENS},
        }
        for source in SOURCES
    }
    with read_connection(path) as connection:
        connection.execute("BEGIN")
        rows = connection.execute(
            "SELECT usage_source, count(*) AS requests,"
            + TOKEN_SUMS
            + " FROM events"
            + WINDOW
            + " GROUP BY usage_source",
            (lower.isoformat(), upper.isoformat()),
        )
        for row in rows:
            result = dict(row)
            summary[result.pop("usage_source")] = result
        disconnected = disconnected_estimates(
            db=connection, where=WINDOW, values=(lower.isoformat(), upper.isoformat())
        )
        hourly = (
            reported_hours(connection, datetime.fromisoformat(start), upper)
            if by_hour
            else None
        )
        undated = connection.execute(
            "SELECT count(*) FROM events WHERE started_at IS NULL"
        ).fetchone()[0]
        gaps = connection.execute(
            "SELECT coalesce(sum(count),0) FROM gaps WHERE "
            "julianday(last_at)>=julianday(?) AND julianday(first_at)<julianday(?)",
            (lower.isoformat(), upper.isoformat()),
        ).fetchone()[0]
    reported = summary["reported"]
    comparisons = {}
    for field, expected in console.items():
        actual = reported[field]
        complete = (
            reported["requests"] > 0
            and reported[field + "_known_requests"] == reported["requests"]
        )
        difference = actual - expected if complete else None
        percent = 100 * difference / expected if complete and expected else None
        passed = abs(difference) <= expected * 0.02 if complete else None
        comparisons[field] = {
            "console": expected,
            "reported": actual,
            "complete": complete,
            "difference": difference,
            "difference_percent": percent,
            "within_2_percent": passed,
            "percent_defined": expected != 0,
        }
    verdicts = [c["within_2_percent"] for c in comparisons.values()]
    verdict = "indeterminate"
    if verdicts and all(v is not None for v in verdicts):
        verdict = "pass" if all(verdicts) else "fail"
    return {
        "window": {
            "boundary": "[start,end), by started_at",
            "start_utc": lower.isoformat(),
            "end_utc": upper.isoformat(),
            "start_local": lower.astimezone().isoformat(),
            "end_local": upper.astimezone().isoformat(),
        },
        "by_usage_source": summary,
        "disconnected_estimated": disconnected,
        **({"by_hour": hourly} if by_hour else {}),
        "comparisons": comparisons,
        "reported_only_target": verdict,
        "undated_requests_excluded": undated,
        "overlapping_gap_count": gaps,
        "notes": [
            "仅 reported 参与2%判定，其他来源的 token 是已知小计，不并入。",
            "reported 无记录或字段不完整时无法判定；"
            "控制台为0时百分比未定义，仅实际也为0才通过。",
            "本地时间取运行机器时区，输入必须含时区偏移；请确认供应商统计窗口与请求开始时间口径一致。",
            "网关重试/fallback关闭；客户端重试会产生额外调用，按供应商实际计量核对。",
            "同一供应商Key的全部流量须经过网关，绕过网关的组件应使用另一个供应商Key。",
            "缺口数量按重叠窗口列出，不能精确分摊；写入未完成、断开后扣费及控制台更新延迟影响完整性。",
        ],
    }


def main() -> int:
    """Print only numeric aggregates and controlled diagnostic messages."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument(
        "--by-hour",
        action="store_true",
        help="Show reported subtotals by clock hour in --start's UTC offset",
    )
    for field in TOKENS:
        parser.add_argument("--console-" + field.replace("_", "-"), type=int)
    args = parser.parse_args()
    console = {
        field: getattr(args, "console_" + field)
        for field in TOKENS
        if getattr(args, "console_" + field) is not None
    }
    try:
        result = reconcile(args.db, args.start, args.end, console, by_hour=args.by_hour)
    except (ValueError, OSError, sqlite3.Error):
        print(json.dumps({"error": "invalid_window_counts_or_database"}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
