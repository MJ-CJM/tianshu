export function evolutionCandidateAnchor(candidateId: string): string {
  return `candidate-${encodeURIComponent(candidateId)}`;
}

export function evolutionCandidateHref(candidateId: string): string {
  return `/evolution#${evolutionCandidateAnchor(candidateId)}`;
}
