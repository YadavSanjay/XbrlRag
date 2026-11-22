import type { ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import AppShell from "./components/AppShell";

import AskPage from "./pages/business/AskPage";
import AnswerPage from "./pages/business/AnswerPage";
import HistoryPage from "./pages/business/HistoryPage";
import ComparePage from "./pages/business/ComparePage";
import MetricsPage from "./pages/business/MetricsPage";
import CompanyPage from "./pages/business/CompanyPage";
import BridgePage from "./pages/business/BridgePage";
import ScreenPage from "./pages/business/ScreenPage";

import OverviewPage from "./pages/technical/OverviewPage";
import TracesPage from "./pages/technical/TracesPage";
import TraceDetailPage from "./pages/technical/TraceDetailPage";
import LineagePage from "./pages/technical/LineagePage";
import IngestionPage from "./pages/technical/IngestionPage";
import LlmPage from "./pages/technical/LlmPage";
import LogsPage from "./pages/technical/LogsPage";
import ArchitecturePage from "./pages/technical/ArchitecturePage";
import EvalPage from "./pages/technical/EvalPage";
import EvalRunPage from "./pages/technical/EvalRunPage";
import EvalComparePage from "./pages/technical/EvalComparePage";
import EvalReliabilityPage from "./pages/technical/EvalReliabilityPage";

function Business({ children }: { children: ReactNode }) {
  return <AppShell persona="business">{children}</AppShell>;
}
function Technical({ children }: { children: ReactNode }) {
  return <AppShell persona="technical">{children}</AppShell>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/business/ask" replace />} />

      {/* Business console */}
      <Route path="/business/ask" element={<Business><AskPage /></Business>} />
      <Route path="/business/answer/:queryId" element={<Business><AnswerPage /></Business>} />
      <Route path="/business/company" element={<Business><CompanyPage /></Business>} />
      <Route path="/business/company/:ticker" element={<Business><CompanyPage /></Business>} />
      <Route path="/business/screen" element={<Business><ScreenPage /></Business>} />
      <Route path="/business/metrics" element={<Business><MetricsPage /></Business>} />
      <Route path="/business/bridge" element={<Business><BridgePage /></Business>} />
      <Route path="/business/history" element={<Business><HistoryPage /></Business>} />
      <Route path="/business/compare" element={<Business><ComparePage /></Business>} />

      {/* Technical console */}
      <Route path="/tech/overview" element={<Technical><OverviewPage /></Technical>} />
      <Route path="/tech/architecture" element={<Technical><ArchitecturePage /></Technical>} />
      <Route path="/tech/traces" element={<Technical><TracesPage /></Technical>} />
      <Route path="/tech/traces/:queryId" element={<Technical><TraceDetailPage /></Technical>} />
      <Route path="/tech/llm" element={<Technical><LlmPage /></Technical>} />
      <Route path="/tech/lineage" element={<Technical><LineagePage /></Technical>} />
      <Route path="/tech/ingestion" element={<Technical><IngestionPage /></Technical>} />
      <Route path="/tech/logs" element={<Technical><LogsPage /></Technical>} />
      <Route path="/tech/eval" element={<Technical><EvalPage /></Technical>} />
      <Route path="/tech/eval/compare/:a/:b" element={<Technical><EvalComparePage /></Technical>} />
      <Route path="/tech/eval/reliability" element={<Technical><EvalReliabilityPage /></Technical>} />
      <Route path="/tech/eval/:runId" element={<Technical><EvalRunPage /></Technical>} />

      <Route path="*" element={<Navigate to="/business/ask" replace />} />
    </Routes>
  );
}
