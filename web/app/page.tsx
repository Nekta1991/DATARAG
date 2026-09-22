import { redirect } from "next/navigation";
import { auth } from "@/lib/auth/server";
import Dashboard from "./dashboard";

export const dynamic = "force-dynamic";

export default async function Home() {
  // proxy.ts redirects too; this is the authoritative page-level check.
  const { data: session } = await auth.getSession();
  if (!session?.user) redirect("/auth/sign-in");
  // Same-origin: app/rag/api proxies to RAG_API_URL (see that route).
  return <Dashboard email={session.user.email} apiUrl="/rag" />;
}
