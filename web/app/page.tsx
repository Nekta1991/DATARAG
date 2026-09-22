import { redirect } from "next/navigation";
import { auth } from "@/lib/auth/server";
import Dashboard from "./dashboard";

export const dynamic = "force-dynamic";

export default async function Home() {
  // proxy.ts redirects too; this is the authoritative page-level check.
  const { data: session } = await auth.getSession();
  if (!session?.user) redirect("/auth/sign-in");
  return <Dashboard email={session.user.email} apiUrl={process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000"} />;
}
