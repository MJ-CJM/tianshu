import type { ApiProblem } from "../contracts/api";
import { isApiProblem, toApiProblem } from "./client";
import type { PersonaInfo, SkillInfo } from "./types";
import { listEdicts } from "./edicts";
import { getReadiness, type ReadinessState } from "./health";
import { listPersonas } from "./personas";
import { listSkills } from "./system";

export const ONBOARDING_QUERY_KEY = ["onboarding", "state"] as const;

const PACKAGED_PERSONAS = [
  { id: "bingbu", name: "兵部", department: "bingbu" },
  { id: "ducha", name: "都察院", department: "ducha" },
  { id: "hubu", name: "户部", department: "hubu" },
  { id: "neige", name: "内阁", department: "neige" },
  { id: "tongzheng", name: "通政司", department: "tongzheng" },
  { id: "wenyuan", name: "文渊阁", department: "wenyuan" },
] as const;

const BUILTIN_SKILL_NAMES = ["file-ops", "shell"] as const;

export interface OnboardingState {
  required: boolean;
  readiness: ReadinessState;
  profile: "demo" | "live";
  packagedPersonas: PersonaInfo[];
  builtinSkills: SkillInfo[];
}

function unavailable(code: string): ApiProblem {
  return {
    status: 503,
    code,
    message: "",
    correlationId: null,
    retryable: true,
  };
}

function requiredPackagedPersonas(personas: PersonaInfo[]): PersonaInfo[] {
  const ids = personas.map((persona) => persona.id);
  if (new Set(ids).size !== ids.length) {
    throw unavailable("onboarding-resources-unavailable");
  }
  const byId = new Map(personas.map((persona) => [persona.id, persona]));
  const packaged = PACKAGED_PERSONAS.map((expected) => byId.get(expected.id));
  const valid = packaged.every((persona, index) => {
    const expected = PACKAGED_PERSONAS[index]!;
    return (
      persona?.id === expected.id &&
      persona.name === expected.name &&
      persona.department === expected.department
    );
  });
  if (!valid) throw unavailable("onboarding-resources-unavailable");
  return packaged as PersonaInfo[];
}

function requiredBuiltinSkills(skills: SkillInfo[]): SkillInfo[] {
  const builtin = skills.filter((skill) => skill.source === "builtin");
  const names = new Set(builtin.map((skill) => skill.name));
  if (BUILTIN_SKILL_NAMES.some((name) => !names.has(name))) {
    throw unavailable("onboarding-resources-unavailable");
  }
  const byName = new Map(builtin.map((skill) => [skill.name, skill]));
  return BUILTIN_SKILL_NAMES.map((name) => byName.get(name)!);
}

export async function getOnboardingState(): Promise<OnboardingState> {
  let readiness;
  try {
    readiness = await getReadiness();
  } catch (error) {
    const problem = isApiProblem(error) ? error : toApiProblem(error);
    if (problem.status === 401 || problem.status === 403) throw problem;
    throw unavailable("onboarding-readiness-unavailable");
  }
  if (readiness.status === "not_ready") {
    throw unavailable("onboarding-readiness-unavailable");
  }
  if (readiness.profile !== "demo" && readiness.profile !== "live") {
    throw unavailable("onboarding-readiness-detail-unavailable");
  }

  // Persisted work is the authoritative returning-user signal. Do not block an
  // established instance because its user has added personas/skills or because
  // a bootstrap catalog endpoint is temporarily unavailable.
  const edictsResponse = await listEdicts({ limit: 1 });
  if (edictsResponse.metadata?.total === undefined) {
    throw unavailable("onboarding-state-unavailable");
  }
  if (edictsResponse.metadata.total > 0) {
    return {
      required: false,
      readiness: readiness.status,
      profile: readiness.profile,
      packagedPersonas: [],
      builtinSkills: [],
    };
  }

  const [personasResponse, skillsResponse] = await Promise.all([
    listPersonas(),
    listSkills(),
  ]);
  if (!personasResponse.data || !skillsResponse.data) {
    throw unavailable("onboarding-resources-unavailable");
  }

  return {
    required: true,
    readiness: readiness.status,
    profile: readiness.profile,
    packagedPersonas: requiredPackagedPersonas(personasResponse.data),
    builtinSkills: requiredBuiltinSkills(skillsResponse.data),
  };
}
