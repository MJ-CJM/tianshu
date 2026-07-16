export type PageDataStatus =
  | "loading"
  | "success-empty"
  | "success-data"
  | "stale"
  | "error"
  | "permission-denied"
  | "service-unavailable";

export interface ApiProblem {
  status: number;
  code: string;
  message: string;
  correlationId: string | null;
  retryable: boolean;
}
