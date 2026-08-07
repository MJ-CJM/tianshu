import { isApiProblem, toApiProblem } from "../../api/client";
import { problemPageStatus } from "./problemPageStatus";
import PageDataState from "./PageDataState";

interface PageQueryErrorProps {
  error: unknown;
  onRetry?: () => void;
}

/** Render a query failure without converting it into a successful empty state. */
export default function PageQueryError({ error, onRetry }: PageQueryErrorProps) {
  const problem = isApiProblem(error) ? error : toApiProblem(error);
  return (
    <PageDataState
      status={problemPageStatus(problem)}
      data={null}
      problem={problem}
      isEmpty={() => false}
      onRetry={onRetry}
    >
      {() => null}
    </PageDataState>
  );
}
