import type { Metadata } from "next";
import type { ReactNode } from "react";
import { AppChrome } from "./components/app-chrome";
import "./globals.css";

export const metadata: Metadata = {
  title: "车型口碑工作台",
  description: "车型口碑增量采集、任务管理、结果仪表盘和交付物下载工作台。",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>
        <AppChrome>{children}</AppChrome>
      </body>
    </html>
  );
}
