import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function compactNumber(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "";
  }
  return new Intl.NumberFormat(undefined, { notation: "compact" }).format(value);
}

export function parseNumberList(value: string) {
  return value
    .split(",")
    .map((item) => Number.parseInt(item.trim(), 10))
    .filter((item) => Number.isFinite(item));
}
