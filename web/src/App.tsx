import { useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { api, setUnauthorizedHandler } from "./api";
import { setProducts } from "./labels";
import { setRole } from "./session";
import { Layout } from "./components/Layout";
import { HostDetail } from "./pages/HostDetail";
import { Hosts } from "./pages/Hosts";
import { Login } from "./pages/Login";
import { Overview } from "./pages/Overview";
import { System } from "./pages/System";
import { Unknown } from "./pages/Unknown";

export default function App() {
  const [user, setUser] = useState<string | null | undefined>(undefined);

  // Foydalanuvchi va yoqilgan mahsulotlar ro'yxati — sahifalar chizilishidan oldin.
  function loadMe() {
    api<{ user: string; role?: string; products?: string[] }>("/api/me")
      .then((r) => {
        setProducts(r.products);
        setRole(r.role);
        setUser(r.user);
      })
      .catch(() => setUser(null));
  }

  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
    loadMe();
  }, []);

  if (user === undefined) return <div className="skeleton" style={{ padding: 24 }}>Yuklanmoqda…</div>;
  if (user === null) return <Login onLogin={loadMe} />;

  return (
    <Layout user={user} onLogout={() => setUser(null)}>
      <Routes>
        <Route path="/" element={<Overview />} />
        <Route path="/hosts" element={<Hosts />} />
        <Route path="/hosts/:id" element={<HostDetail />} />
        <Route path="/unknown" element={<Unknown />} />
        <Route path="/system" element={<System />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  );
}
