import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merge component classes with Tailwind utility precedence. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
