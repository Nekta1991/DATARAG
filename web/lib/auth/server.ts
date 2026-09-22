import { createNeonAuth } from "@neondatabase/auth/next/server";

// Neon Auth (Managed Better Auth) on the production branch. Only one account
// matters: the admin created by scripts/create_admin.py. The Python API
// checks role=admin itself, so this layer only needs to establish a session.
export const auth = createNeonAuth({
  baseUrl: process.env.NEON_AUTH_BASE_URL!,
  cookies: { secret: process.env.NEON_AUTH_COOKIE_SECRET! },
});
