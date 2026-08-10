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
};

export default nextConfig;
