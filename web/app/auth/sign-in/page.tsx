"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { authClient } from "@/lib/auth/client";

type Mode = "sign-in" | "sign-up";

const labelStyle = { display: "flex", flexDirection: "column", gap: 6, fontSize: 12.5, color: "var(--color-neutral-800)" } as const;

// Registration creates an ordinary account. Admin rights are granted separately
// (role=admin in neon_auth."user"); the Python API answers 403 until then.
export default function SignInPage() {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("sign-in");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const signUp = mode === "sign-up";

  function switchMode(next: Mode) {
    setMode(next);
    setError(null);
    setNotice(null);
    setPassword("");
    setConfirm("");
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    if (signUp) {
      if (password.length < 8) return setError("パスワードは8文字以上にしてください。");
      if (password !== confirm) return setError("パスワードが一致しません。");
    }
    setPending(true);
    try {
      if (signUp) {
        const { data, error } = await authClient.signUp.email({
          email, password, name: name.trim() || email.split("@")[0],
        });
        if (error) {
          setError(error.status === 422 || /exist/i.test(error.message ?? "")
            ? "このメールアドレスは既に登録されています。"
            : `登録できませんでした（${error.message ?? error.status}）。`);
          return;
        }
        if (!data?.token) {
          // Email verification is on: no session until the address is confirmed.
          setNotice("登録しました。確認メールの手順を完了してからサインインしてください。");
          switchMode("sign-in");
          return;
        }
      } else {
        const { error } = await authClient.signIn.email({ email, password });
        if (error) {
          setError("メールアドレスまたはパスワードが正しくありません。");
          return;
        }
      }
      router.replace("/");
      router.refresh();
    } catch {
      setError("認証サーバーに接続できませんでした。");
    } finally {
      setPending(false);
    }
  }

  return (
    <main style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 16 }}>
      <form onSubmit={onSubmit} className="card" style={{ width: "100%", maxWidth: 420, boxShadow: "var(--shadow-md)" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
          <span style={{ fontFamily: "var(--font-heading)", fontSize: 24, lineHeight: 1, color: "var(--color-accent-700)" }}>DATARAG</span>
          <span className="tag tag-accent">非公式デモ</span>
        </div>
        <h1 className="ct">{signUp ? "アカウント登録" : "管理者サインイン"}</h1>
        <div className="ct-rule" />
        <p style={{ margin: 0, fontSize: 13.5, lineHeight: 1.7, color: "var(--color-neutral-700)" }}>
          {signUp
            ? "登録直後は一般アカウントです。コンソールの利用には管理者権限の付与が必要です。"
            : "このコンソールは管理者アカウントのみ利用できます。"}
        </p>
        {signUp && (
          <label style={labelStyle}>
            表示名（任意）
            <input className="field" type="text" autoComplete="name" maxLength={100}
                   value={name} onChange={(e) => setName(e.target.value)} />
          </label>
        )}
        <label style={labelStyle}>
          メールアドレス
          <input className="field" type="email" autoComplete={signUp ? "email" : "username"} required
                 value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label style={labelStyle}>
          パスワード{signUp && "（8文字以上）"}
          <input className="field" type="password" autoComplete={signUp ? "new-password" : "current-password"}
                 required minLength={signUp ? 8 : undefined}
                 value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {signUp && (
          <label style={labelStyle}>
            パスワード（確認）
            <input className="field" type="password" autoComplete="new-password" required
                   value={confirm} onChange={(e) => setConfirm(e.target.value)} />
          </label>
        )}
        {error && <p role="alert" className="blocked">{error}</p>}
        {notice && <p role="status" style={{ margin: 0, fontSize: 13, color: "var(--color-neutral-800)" }}>{notice}</p>}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
          <button type="button" onClick={() => switchMode(signUp ? "sign-in" : "sign-up")}
                  style={{ background: "none", border: 0, padding: 0, cursor: "pointer", fontSize: 13,
                           color: "var(--color-accent-700)", textDecoration: "underline" }}>
            {signUp ? "サインインに戻る" : "アカウントを新規登録"}
          </button>
          <button className="btn btn-primary" type="submit" disabled={pending}>
            {pending ? "確認中…" : signUp ? "登録する" : "サインイン"}
          </button>
        </div>
      </form>
    </main>
  );
}
