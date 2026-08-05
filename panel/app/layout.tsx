import type { Metadata } from "next";
import "./globals.css";
import { Nav } from "@/components/nav";

export const metadata: Metadata = {
  title: "Olimpo",
  description: "Core de agentes sociais autônomos",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-BR" className="dark">
      <body>
        <div className="flex min-h-screen">
          <Nav />
          <main className="flex-1 overflow-x-hidden p-6">{children}</main>
        </div>
      </body>
    </html>
  );
}
