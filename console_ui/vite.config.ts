import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath, URL } from "node:url";

export default defineConfig({
  plugins: [tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  build: {
    rollupOptions: {
      input: {
        console: fileURLToPath(new URL("./index.html", import.meta.url)),
        playground: fileURLToPath(
          new URL("./playground.html", import.meta.url),
        ),
      },
    },
  },
});
