import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  listSkills,
  getSkill,
  updateSkill,
  createSkill,
  deleteSkill,
  listTools,
  listPromptFiles,
  getPromptFile,
  updatePromptFile,
  previewSystemPrompt,
} from "../api/system";

// --- Skills ---

export function useSkills() {
  return useQuery({
    queryKey: ["skills"],
    queryFn: listSkills,
    select: (data) => data.data ?? [],
  });
}

export function useSkillDetail(name: string | null) {
  return useQuery({
    queryKey: ["skills", name],
    queryFn: () => getSkill(name!),
    enabled: !!name,
    select: (data) => data.data,
  });
}

export function useUpdateSkill() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, content }: { name: string; content: string }) =>
      updateSkill(name, content),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["skills"] });
    },
  });
}

export function useCreateSkill() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, content }: { name: string; content: string }) =>
      createSkill(name, content),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["skills"] });
    },
  });
}

export function useDeleteSkill() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => deleteSkill(name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["skills"] });
    },
  });
}

// --- Tools ---

export function useTools() {
  return useQuery({
    queryKey: ["tools"],
    queryFn: listTools,
    select: (data) => data.data ?? [],
  });
}

// --- System Prompt ---

export function usePromptFiles() {
  return useQuery({
    queryKey: ["system-prompt", "files"],
    queryFn: listPromptFiles,
    select: (data) => data.data ?? { files: [], departments: {} },
  });
}

export function usePromptFileContent(
  personaId: string | null,
  filename: string | null,
) {
  return useQuery({
    queryKey: ["system-prompt", "file", personaId, filename],
    queryFn: () => getPromptFile(personaId!, filename!),
    enabled: !!personaId && !!filename,
    select: (data) => data.data,
  });
}

export function useUpdatePromptFile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      personaId,
      filename,
      content,
    }: {
      personaId: string;
      filename: string;
      content: string;
    }) => updatePromptFile(personaId, filename, content),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["system-prompt"] });
    },
  });
}

export function usePromptPreview(personaId: string | null) {
  return useQuery({
    queryKey: ["system-prompt", "preview", personaId],
    queryFn: () => previewSystemPrompt(personaId!),
    enabled: !!personaId,
    select: (data) => data.data,
  });
}
