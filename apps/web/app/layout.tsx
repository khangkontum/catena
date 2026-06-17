import type { Metadata } from "next";
import * as React from "react";

import { AppNav } from "@/components/app-nav";
import { Providers } from "@/app/providers";

import "./globals.css";

export const metadata: Metadata = {
  title: "catena",
  description: "Local evidence-backed paper extraction tables",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <Providers>
          <div className="flex min-h-screen bg-slate-50 text-slate-950">
            <AppNav />
            <main className="min-w-0 flex-1 p-4 md:p-8">{children}</main>
          </div>
        </Providers>
      </body>
    </html>
  );
}
