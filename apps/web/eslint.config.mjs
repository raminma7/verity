import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { FlatCompat } from "@eslint/eslintrc";

const compat = new FlatCompat({ baseDirectory: dirname(fileURLToPath(import.meta.url)) });

const config = [
  { ignores: [".next/**", "node_modules/**", "playwright-report/**", "test-results/**", "next-env.d.ts"] },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  // Chatbot logos are small user uploads served by the API (and shown inside the embedded widget);
  // next/image would need the API as a remote image host and adds nothing for 40px images.
  { rules: { "@next/next/no-img-element": "off" } },
];

export default config;
