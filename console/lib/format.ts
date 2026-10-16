export function usd(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "—";
  if (value > 0 && value < 0.01) return "<$0.01";
  return value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function num(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : value.toLocaleString("en-US");
}

/** "1 member", "3 members". */
export function plural(n: number | null | undefined, one: string, many = `${one}s`): string {
  return `${num(n)} ${n === 1 ? one : many}`;
}

export function pct(value: number | null | undefined, digits = 0): string {
  return value === null || value === undefined ? "—" : `${(value * 100).toFixed(digits)}%`;
}

/** "5 min ago" from an ISO timestamp; timestamps without a zone are UTC. */
export function ago(iso: string | null | undefined): string {
  if (!iso) return "Never";
  const when = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + "Z");
  const s = (Date.now() - when.getTime()) / 1000;
  if (s < 60) return "Just now";
  const units: [string, number][] = [
    ["day", 86400],
    ["hr", 3600],
    ["min", 60],
  ];
  for (const [unit, size] of units) {
    if (s >= size) {
      const n = Math.floor(s / size);
      return `${n} ${unit}${unit === "day" && n > 1 ? "s" : ""} ago`;
    }
  }
  return "Just now";
}

export function initials(name: string): string {
  const parts = name.trim().split(/[\s._@-]+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts[1]?.[0] ?? "")).toUpperCase();
}

export function toCsv(rows: Record<string, unknown>[]): string {
  if (!rows.length) return "";
  const cols = Object.keys(rows[0]);
  const cell = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [cols.join(","), ...rows.map((r) => cols.map((c) => cell(r[c])).join(","))].join("\n");
}

export function downloadCsv(filename: string, rows: Record<string, unknown>[]): void {
  const blob = new Blob([toCsv(rows)], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${filename}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}
