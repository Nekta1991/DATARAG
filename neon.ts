import { defineConfig } from "@neon/config/v1";

export default defineConfig({
  // Neon Auth (Managed Better Auth). One admin user, created directly; the
  // backend accepts only role=admin, so open sign-up grants nothing.
  auth: true,
});
