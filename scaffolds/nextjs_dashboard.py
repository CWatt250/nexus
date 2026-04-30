"""nextjs-dashboard: Next.js + Tailwind + shadcn + Recharts + auth."""
from __future__ import annotations

from .nextjs_base import make_nextjs_recipe


def _templates(ctx: dict) -> dict[str, str]:
    return {
        "src/app/page.tsx": _PAGE,
        "src/components/metric-card.tsx": _METRIC_CARD,
        "src/components/revenue-chart.tsx": _CHART,
        "src/lib/supabase.ts": _SUPABASE_CLIENT,
        ".env.example": _ENV_EXAMPLE,
        "README.md": _README.format(name=ctx["name"]),
    }


_PAGE = """import MetricCard from "@/components/metric-card";
import RevenueChart from "@/components/revenue-chart";

export default function Dashboard() {
  return (
    <main className="min-h-screen bg-slate-950 p-8 text-slate-100">
      <h1 className="text-3xl font-bold">Dashboard</h1>
      <div className="mt-6 grid gap-4 md:grid-cols-3">
        <MetricCard label="MRR" value="$12,400" delta="+8.4%" />
        <MetricCard label="Active users" value="1,284" delta="+3.1%" />
        <MetricCard label="Churn" value="2.0%" delta="-0.4%" />
      </div>
      <div className="mt-8 rounded-md border border-slate-800 bg-slate-900 p-4">
        <RevenueChart />
      </div>
    </main>
  );
}
"""

_METRIC_CARD = """interface Props {
  label: string;
  value: string;
  delta: string;
}

export default function MetricCard({ label, value, delta }: Props) {
  return (
    <div className="rounded-md border border-slate-800 bg-slate-900 p-4">
      <div className="text-sm text-slate-400">{label}</div>
      <div className="mt-2 text-2xl font-semibold">{value}</div>
      <div className="mt-1 text-xs text-emerald-400">{delta}</div>
    </div>
  );
}
"""

_CHART = """\"use client\";

import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from \"recharts\";

const data = [
  { month: \"Jan\", revenue: 4000 }, { month: \"Feb\", revenue: 5200 },
  { month: \"Mar\", revenue: 6100 }, { month: \"Apr\", revenue: 7800 },
  { month: \"May\", revenue: 9500 }, { month: \"Jun\", revenue: 12400 },
];

export default function RevenueChart() {
  return (
    <ResponsiveContainer width=\"100%\" height={260}>
      <LineChart data={data}>
        <CartesianGrid strokeDasharray=\"3 3\" stroke=\"#1e293b\" />
        <XAxis dataKey=\"month\" stroke=\"#94a3b8\" />
        <YAxis stroke=\"#94a3b8\" />
        <Tooltip />
        <Line type=\"monotone\" dataKey=\"revenue\" stroke=\"#06b6d4\" strokeWidth={2} />
      </LineChart>
    </ResponsiveContainer>
  );
}
"""

_SUPABASE_CLIENT = """import { createBrowserClient } from \"@supabase/ssr\";
export const supabase = createBrowserClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
);
"""

_ENV_EXAMPLE = """NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
"""

_README = """# {name}

Internal dashboard scaffold (Recharts + shadcn) by Nexus (`nextjs-dashboard`).

## Run
```bash
npm install
cp .env.example .env.local
npm run dev
```
"""


RECIPE = make_nextjs_recipe(
    slug="nextjs-dashboard",
    display="Next.js 14 Dashboard (Recharts + Supabase)",
    description="Internal dashboard skeleton: metric cards, revenue chart, "
                "auth client wired.",
    extra_templates=_templates,
    extra_packages=["recharts", "@supabase/ssr", "@supabase/supabase-js"],
)
