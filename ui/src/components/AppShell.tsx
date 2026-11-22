import { useState, type ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { getTheme, toggleTheme } from "../lib/theme";

const businessNav = [
  { to: "/business/ask", label: "Ask" },
  { to: "/business/company", label: "Company" },
  { to: "/business/screen", label: "Ranking Studio" },
  { to: "/business/metrics", label: "Metrics" },
  { to: "/business/bridge", label: "Variance Bridge" },
  { to: "/business/history", label: "History" },
  { to: "/business/compare", label: "Compare" },
];

const technicalNav = [
  { to: "/tech/overview", label: "System Overview" },
  { to: "/tech/architecture", label: "Architecture Map" },
  { to: "/tech/traces", label: "Trace Explorer" },
  { to: "/tech/llm", label: "Cost & LLM" },
  { to: "/tech/lineage", label: "Numeric Lineage" },
  { to: "/tech/ingestion", label: "Ingestion & Data" },
  { to: "/tech/eval", label: "Evaluation" },
  { to: "/tech/logs", label: "Logs" },
];

/** Global query_id search — the navigational spine. Jumps to the full trace. */
function QueryIdSearch() {
  const [v, setV] = useState("");
  const navigate = useNavigate();
  return (
    <form
      className="search"
      onSubmit={(e) => {
        e.preventDefault();
        const id = v.trim();
        if (id) navigate(`/tech/traces/${encodeURIComponent(id)}`);
      }}
    >
      <input
        placeholder="Jump to query_id…"
        value={v}
        onChange={(e) => setV(e.target.value)}
      />
    </form>
  );
}

export default function AppShell({
  persona,
  children,
}: {
  persona: "business" | "technical";
  children: ReactNode;
}) {
  const nav = persona === "business" ? businessNav : technicalNav;
  const [theme, setTheme] = useState(getTheme());
  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          A<span className="dot">Rag</span>
        </div>
        <nav className="persona">
          <NavLink to="/business/ask" className={() => (persona === "business" ? "active" : "")}>
            Business
          </NavLink>
          <NavLink to="/tech/overview" className={() => (persona === "technical" ? "active" : "")}>
            Technical
          </NavLink>
        </nav>
        <div className="spacer" />
        <QueryIdSearch />
        <button className="btn ghost" style={{ padding: "6px 10px" }} title="Toggle theme"
          onClick={() => setTheme(toggleTheme())}>
          {theme === "dark" ? "☀" : "☾"}
        </button>
      </header>
      <div className="body">
        <aside className="sidebar">
          <div className="group">{persona === "business" ? "Analyst Workspace" : "System & Observability"}</div>
          {nav.map((n) => (
            <NavLink key={n.to} to={n.to} className={({ isActive }) => (isActive ? "active" : "")}>
              {n.label}
            </NavLink>
          ))}
        </aside>
        <main className="main">{children}</main>
      </div>
    </div>
  );
}
