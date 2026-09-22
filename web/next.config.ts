import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The repo root has its own package-lock.json (for neon.ts), which made
  // Next warn about multiple lockfiles and guess the wrong root.
  turbopack: { root: path.resolve(__dirname) },
};

export default nextConfig;
