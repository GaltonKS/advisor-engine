import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ErrorNote, Loading, Panel, StatCard } from "../../components/ui";
import { useApi } from "../../lib/api";
import { pct } from "../../lib/format";
import type { Question, QuestionnaireResult } from "../../lib/types";
import type { Workspace } from "../ClientWorkspace";

interface QuestionnaireDef { questions: Question[]; method_version: string; method_status: string }

export default function ProfileTab({ ws }: { ws: Workspace }) {
  const api = useApi();
  const qc = useQueryClient();
  const def = useQuery({ queryKey: ["questionnaire"], queryFn: () => api<QuestionnaireDef>("/questionnaire") });
  const [answers, setAnswers] = useState<Record<string, number>>(ws.client.questionnaire ?? {});
  const save = useMutation({
    mutationFn: () => api<QuestionnaireResult>(`/clients/${ws.client.id}/questionnaire`, { method: "PUT", json: { answers } }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["client", ws.client.id] });
      qc.invalidateQueries({ queryKey: ["suitability"] });
    },
  });

  if (def.isLoading || !def.data) return <><Loading /><ErrorNote error={def.error} /></>;
  const result = save.data ?? ws.client.questionnaire_result ?? null;
  const complete = def.data.questions.every((q) => answers[q.id] !== undefined);

  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_340px]">
      <Panel title="Client risk questionnaire" tag={def.data.method_status}>
        <ol className="space-y-5">
          {def.data.questions.map((q, i) => (
            <li key={q.id}>
              <div className="mb-2 text-sm font-semibold"><span className="figure mr-2 text-gold-dark">{String(i + 1).padStart(2, "0")}</span>{q.text}</div>
              <div className="flex flex-wrap gap-2">
                {q.options.map((o, j) => (
                  <label key={o} className={`cursor-pointer border px-3 py-1.5 text-sm ${answers[q.id] === j ? "border-gold bg-gold-soft" : "border-rule bg-white hover:border-gold"}`}>
                    <input type="radio" className="sr-only" name={q.id} checked={answers[q.id] === j}
                           onChange={() => { setAnswers({ ...answers, [q.id]: j }); save.reset(); }} />
                    {o}
                  </label>
                ))}
              </div>
            </li>
          ))}
        </ol>
        <ErrorNote error={save.error} />
        <button className="btn btn-primary mt-6" disabled={!complete || save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? "Scoring…" : "Save and score"}
        </button>
        {!complete && <span className="ml-3 text-xs text-faint">Answer all six questions to score.</span>}
      </Panel>

      <div className="space-y-4 self-start">
        {result ? (
          <>
            <StatCard eyebrow="Client risk level" value={result.risk_level} unit={`/ ${result.risk_level_max}`}
                      note={`Score ${result.score} of ${result.max_score}. Largest acceptable fall: ${pct(result.tolerable_loss, 0)}.`} />
            <Panel>
              <p className="text-xs text-muted">
                Scored by the engine (method {result.method_version}). {result.method_status}
                {ws.client.questionnaire_result && !save.data && " Answers on file are shown on the left."}
              </p>
              <button className="btn mt-3 w-full justify-center" onClick={() => ws.go("overview")}>See how the portfolio compares →</button>
            </Panel>
          </>
        ) : (
          <Panel title="Not scored yet">
            <p className="text-sm text-muted">The engine scores the answers to a risk level from 1 to 10 and checks the portfolio's largest fall from a peak against the largest fall the client says they could accept.</p>
          </Panel>
        )}
        {save.isSuccess && <p className="text-xs text-good">Saved and recorded in the audit trail.</p>}
      </div>
    </div>
  );
}
