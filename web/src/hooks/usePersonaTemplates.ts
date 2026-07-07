import { useQuery } from "@tanstack/react-query";
import {
  listPersonaTemplates,
  getPersonaTemplate,
  type TemplateLang,
} from "../api/personaTemplates";

export function usePersonaTemplates(lang: TemplateLang) {
  return useQuery({
    queryKey: ["persona-templates", lang],
    queryFn: () => listPersonaTemplates(lang),
    select: (data) => data.data ?? [],
  });
}

export function usePersonaTemplate(lang: TemplateLang, id: string | null) {
  return useQuery({
    queryKey: ["persona-template", lang, id],
    queryFn: () => getPersonaTemplate(lang, id!),
    enabled: !!id,
    select: (data) => data.data,
  });
}
