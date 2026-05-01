import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/** Tailwind class combiner. shadcn/ui standard. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
