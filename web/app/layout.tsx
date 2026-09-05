import type { Metadata } from "next";
import type { ReactNode } from "react";

import { CockpitFrame } from "../components/CockpitFrame";

import "./styles.css";

export const metadata: Metadata = {
  title: "VaultOS Web",
  description: "VaultOS web surface",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <CockpitFrame>{children}</CockpitFrame>
      </body>
    </html>
  );
}
