import type { ApiProblem } from "../../contracts/api";

export function problemPageStatus(
  problem: ApiProblem,
): "permission-denied" | "service-unavailable" | "error" {
  if (
    problem.status === 401 ||
    problem.status === 403 ||
    problem.code === "auth-required" ||
    problem.code === "permission-denied"
  ) {
    return "permission-denied";
  }
  if (problem.status === 503 || problem.code === "service-unavailable") {
    return "service-unavailable";
  }
  return "error";
}
