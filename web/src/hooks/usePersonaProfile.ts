import { useQuery } from "@tanstack/react-query";
import { getPersonaProfile } from "../api/profile";

export function usePersonaProfile(id: string | null) {
  return useQuery({
    queryKey: ["persona", "profile", id],
    queryFn: () => getPersonaProfile(id!),
    enabled: !!id,
    select: (resp) => resp.data,
  });
}
