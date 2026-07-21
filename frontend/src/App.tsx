// @ts-check
import { DashboardPage } from "./pages/DashboardPage";
import { DiscoveryPage } from "./pages/DiscoveryPage";
import { Navigation } from "./components/Navigation";
import { GroupDetailsPage } from "./pages/GroupDetailsPage";
import { StockDetailsPage } from "./pages/StockDetailsPage";
import { Routes, Route } from "react-router-dom";

export type GroupViewParams = {
  type: string;
  name: string;
  parentSector: string;
  parentIndustry: string;
  horizon: string;
};

export type StockViewParams = {
  symbol: string;
  horizon: string;
};

export function App() {
  return (
    <div className="app-container" style={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>
      <Navigation />
      
      <div className="page-content" style={{ flex: 1, overflowY: 'auto' }}>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/discovery/new" element={<DiscoveryPage />} />
          <Route path="/discovery/:runId" element={<DiscoveryPage />} />
          <Route path="/discovery/:runId/:tab" element={<DiscoveryPage />} />
          <Route path="/discovery/:runId/group/:type/:name" element={<GroupDetailsPage />} />
          <Route path="/discovery/:runId/stock/:symbol" element={<StockDetailsPage />} />
        </Routes>
      </div>
    </div>
  );
}
