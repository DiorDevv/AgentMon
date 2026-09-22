import { Activity, Radar, Shield, ShieldCheck } from "lucide-react";
import { useState, type FormEvent } from "react";
import { api } from "../api";

export function Login({ onLogin }: { onLogin: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api<{ user: string }>("/api/login", { method: "POST", json: { username, password } });
      onLogin();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <section className="login-art">
        <div className="brand" style={{ padding: 0 }}>
          <span className="brand-mark"><Shield size={19} strokeWidth={2.4} /></span>
          <div className="brand-name">AgentMon</div>
        </div>
        <div>
          <h2>Har bir kompyuterda himoya ishlayotganini bilib turing</h2>
          <p>
            Tarmoq trafigi va xavfsizlik konsollarini solishtirib, agenti o'rnatilmagan, to'xtatilgan yoki
            domendan tashqaridagi kompyuterlarni real vaqtda aniqlaydi.
          </p>
          <div className="login-feats">
            <div><ShieldCheck size={18} />Cortex XDR, Kaspersky, SearchInform va Active Directory</div>
            <div><Activity size={18} />Cisco FTD NetFlow asosida — endpoint'da aldab bo'lmaydi</div>
            <div><Radar size={18} />Tarmoqdagi noma'lum qurilmalarni ko'rsatadi</div>
          </div>
        </div>
        <div style={{ fontSize: 12.5, color: "#6b7a94" }}>Ichki foydalanish uchun</div>
      </section>
      <section className="login-form">
        <form onSubmit={submit}>
          <div style={{ marginBottom: 6 }}>
            <h1>Tizimga kirish</h1>
            <p className="ink-2" style={{ margin: "6px 0 0" }}>Domen hisobingiz bilan kiring</p>
          </div>
          <label>
            Login
            <input className="input" placeholder="i.familiyev" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus required />
          </label>
          <label>
            Parol
            <input className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          </label>
          {error && <div className="error">{error}</div>}
          <button className="btn primary" disabled={busy}>{busy ? "Tekshirilmoqda…" : "Kirish"}</button>
        </form>
      </section>
    </div>
  );
}
