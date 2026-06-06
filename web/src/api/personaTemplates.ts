import apiClient from "./client";
import type {
  ApiResponse,
  PersonaTemplateCategory,
  PersonaTemplateDetail,
} from "./types";

export type TemplateLang = "zh" | "en";

export async function listPersonaTemplates(
  lang: TemplateLang,
): Promise<ApiResponse<PersonaTemplateCategory[]>> {
  const { data } = await apiClient.get<ApiResponse<PersonaTemplateCategory[]>>(
    "/persona-templates",
    { params: { lang } },
  );
  return data;
}

export async function getPersonaTemplate(
  lang: TemplateLang,
  id: string,
): Promise<ApiResponse<PersonaTemplateDetail>> {
  const { data } = await apiClient.get<ApiResponse<PersonaTemplateDetail>>(
    `/persona-templates/${id}`,
    { params: { lang } },
  );
  return data;
}
