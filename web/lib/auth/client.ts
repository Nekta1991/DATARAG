"use client";

import { createAuthClient } from "@neondatabase/auth/next";

// Talks to the same-origin proxy at /api/auth. Takes no arguments by design.
export const authClient = createAuthClient();
