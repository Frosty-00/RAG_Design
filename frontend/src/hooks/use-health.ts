/** Polls /readyz every 10s — used by the nav-bar status dot. */
import { useQuery } from "@tanstack/react-query";

interface ReadyResp {
  status: string;
  components: Record<string, boolean>;
}

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: async (): Promise<ReadyResp> => {
      const r = await fetch("/readyz");
      if (!r.ok) throw new Error("not ready");
      return r.json();
    },
    refetchInterval: 10_000,
    retry: false,
    staleTime: 5_000,
  });
}
