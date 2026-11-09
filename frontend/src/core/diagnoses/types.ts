export type DiagnosisEvidence = {
  id: string;
  kind: "run" | "log" | "source";
  title: string;
  location: string;
  url: string;
  excerpts?: readonly { line: number; text: string }[];
};

export type DiagnosisEvent = {
  seq: number;
  phase: "resolve" | "collect" | "investigate" | "publish";
  title: string;
  summary: string;
  status: "completed" | "finding";
  evidenceIds: readonly string[];
};

export type DiagnosisReplay = {
  id: string;
  repository: string;
  workflow: string;
  attempt: number;
  runUrl: string;
  headSha: string;
  checkoutSha: string;
  events: readonly DiagnosisEvent[];
  evidence: readonly DiagnosisEvidence[];
  report: {
    summary: string;
    limits: string;
    validation: string;
    evidenceIds: readonly string[];
  };
};
