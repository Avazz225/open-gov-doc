import path from "node:path";

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Konzept 8: bewusst reines Client-Side-Rendering, kein Node-Laufzeitserver
  // in Produktion - der Build erzeugt statische Dateien, ausgeliefert über
  // nginx (siehe Dockerfile). Kein SSR/SSG-Datenzugriff auf die Python-BFFs.
  output: "export",
  trailingSlash: true,
  images: {
    unoptimized: true,
  },
  // libs/dms-showcase (ADR 0237) lives outside this app and has no
  // node_modules of its own: resolve its bare imports (lucide-react) against
  // this app's node_modules. React itself is already aliased by Next.
  webpack: (config) => {
    config.resolve.modules = [...(config.resolve.modules ?? ["node_modules"]), path.resolve("node_modules")];
    return config;
  },
};

export default nextConfig;
