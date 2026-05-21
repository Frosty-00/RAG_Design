import { Navigate, Route, Routes } from "react-router-dom";

import { AuthGate } from "@/components/auth-gate";
import { Nav } from "@/components/nav";
import { ToastViewport } from "@/components/ui/toast";
import { useHealth } from "@/hooks/use-health";
import AdminPage from "@/pages/admin";
import ChatPage from "@/pages/chat";
import DebugPage from "@/pages/debug";
import DocumentsPage from "@/pages/documents";
import EvalPage from "@/pages/eval";

export default function App() {
  const health = useHealth();
  const ready = health.data?.status === "ok";

  return (
    <AuthGate>
      <div className="flex h-full flex-col">
        <Nav ready={ready} />
        <main className="container flex-1 py-6">
          <Routes>
            <Route path="/" element={<Navigate to="/chat" replace />} />
            <Route path="/chat" element={<ChatPage />} />
            <Route path="/documents" element={<DocumentsPage />} />
            <Route path="/eval" element={<EvalPage />} />
            {/* /admin: page itself guards on role=admin via /admin/me;
                Nav also hides the link unless admin, so non-admin users
                practically never land here. Backend enforces the truth. */}
            <Route path="/admin" element={<AdminPage />} />
            {import.meta.env.DEV && (
              <Route path="/debug" element={<DebugPage />} />
            )}
            <Route path="*" element={<Navigate to="/chat" replace />} />
          </Routes>
        </main>
      </div>
      <ToastViewport />
    </AuthGate>
  );
}
