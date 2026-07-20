// @ts-check
import { useState } from "react";
import { DashboardPage } from "./pages/DashboardPage";
import { DiscoveryPage } from "./pages/DiscoveryPage";
import { Navigation, TabName } from "./components/Navigation";
import { GroupDetailsPage } from "./pages/GroupDetailsPage";
import { StockDetailsPage } from "./pages/StockDetailsPage";

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
  const [activeTab, setActiveTab] = useState<TabName>("DASHBOARD");
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [activeGroup, setActiveGroup] = useState<GroupViewParams | null>(null);
  const [activeStock, setActiveStock] = useState<StockViewParams | null>(null);

  const handleRunSelect = (runId: string) => {
    setActiveRunId(runId);
    setActiveTab("SECTORS");
    setActiveGroup(null);
    setActiveStock(null);
  };

  const handleTabChange = (tab: TabName) => {
    setActiveTab(tab);
    setActiveGroup(null);
    setActiveStock(null);
  };

  const handleBackToGroup = () => {
    setActiveStock(null);
  };

  const handleBackToDiscovery = () => {
    setActiveGroup(null);
    setActiveStock(null);
  };

  return (
    <div className="app-container" style={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>
      <Navigation 
        activeTab={activeTab} 
        onTabChange={handleTabChange} 
        runSelected={activeRunId !== null} 
      />
      
      <div className="page-content" style={{ flex: 1, overflowY: 'auto' }}>
        {activeTab === "DASHBOARD" && !activeGroup && !activeStock && (
          <DashboardPage 
            onRunSelect={handleRunSelect} 
            onNewRun={() => setActiveTab("PIPELINE")}
          />
        )}
        
        {activeTab === "PIPELINE" && !activeGroup && !activeStock && (
          <DiscoveryPage 
            onRunCreated={(newRunId: string) => {
              setActiveRunId(newRunId);
            }}
          />
        )}

        {activeTab !== "DASHBOARD" && activeTab !== "PIPELINE" && activeRunId && !activeGroup && !activeStock && (
          <DiscoveryPage 
            runId={activeRunId} 
            activeTab={activeTab} 
            onGroupSelect={(group: GroupViewParams) => setActiveGroup(group)}
            onStockSelect={(stock: StockViewParams) => setActiveStock(stock)}
          />
        )}

        {activeGroup && !activeStock && activeRunId && (
          <GroupDetailsPage 
            runId={activeRunId}
            group={activeGroup}
            onBack={handleBackToDiscovery}
            onStockSelect={(stock: StockViewParams) => setActiveStock(stock)}
          />
        )}

        {activeStock && activeRunId && (
          <StockDetailsPage 
            runId={activeRunId}
            stock={activeStock}
            onBack={activeGroup ? handleBackToGroup : handleBackToDiscovery}
          />
        )}
      </div>
    </div>
  );
}
