/** @type {import('next').NextConfig} */
const nextConfig = {
  // Konzept 8/3.3a: statischer Export wie jede andere Frontend-App dieses
  // Projekts (ADR 0006) - der Taskpane ist eine gewöhnliche Webseite, die
  // Office per iframe innerhalb des Add-in-Hosts lädt, kein eigener Server.
  output: "export",
  trailingSlash: true,
  images: {
    unoptimized: true,
  },
};

export default nextConfig;
