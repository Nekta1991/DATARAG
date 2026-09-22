import { auth } from "@/lib/auth/server";

// Optimistic redirect only. The real authorization is in the Python API,
// which verifies the JWT and requires role=admin on every query.
export default auth.middleware({ loginUrl: "/auth/sign-in" });

// Protect the dashboard only; the sign-in page, /api/auth and static assets
// must stay reachable without a session.
export const config = {
  matcher: ["/"],
};
