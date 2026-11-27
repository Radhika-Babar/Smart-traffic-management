import { StrictMode, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { HintProvider } from "@/components/ui/primitives";
import "@/index.css";
import { AppShell } from "@/layouts/AppShell";
import { Alerts } from "@/pages/Alerts";
import { Analytics } from "@/pages/Analytics";
import { Emergency } from "@/pages/Emergency";
import { Incidents } from "@/pages/Incidents";
import { Intersections } from "@/pages/Intersections";
import { LiveMap } from "@/pages/LiveMap";
import { Overview } from "@/pages/Overview";
import { Prediction } from "@/pages/Prediction";
import { Sensors } from "@/pages/Sensors";
import { Settings } from "@/pages/Settings";
import { Signals } from "@/pages/Signals";
import { SimulationLab } from "@/pages/SimulationLab";
import { connectLive } from "@/services/socket";

function App() {
  useEffect(() => connectLive(), []);
  return (
    <HintProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<AppShell />}>
            <Route index element={<Overview />} />
            <Route path="map" element={<LiveMap />} />
            <Route path="intersections" element={<Intersections />} />
            <Route path="sensors" element={<Sensors />} />
            <Route path="signals" element={<Signals />} />
            <Route path="emergency" element={<Emergency />} />
            <Route path="incidents" element={<Incidents />} />
            <Route path="prediction" element={<Prediction />} />
            <Route path="analytics" element={<Analytics />} />
            <Route path="alerts" element={<Alerts />} />
            <Route path="simulation" element={<SimulationLab />} />
            <Route path="settings" element={<Settings />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </HintProvider>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
