/** Tiny token store backed by localStorage.
 *  Layer 13 chat/upload pages call `getToken()`; if absent the app shows a
 *  one-time sign-in screen that asks the user to paste their bearer token.
 */
const KEY = "self-rag.token";

export function getToken(): string | null {
  return localStorage.getItem(KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(KEY);
}
