import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Empty, ErrorNote, Field, FilePicker, Loading, Panel } from "../components/ui";
import { useApi } from "../lib/api";
import { date } from "../lib/format";
import type { Client, Portfolio } from "../lib/types";

export default function Clients() {
  const api = useApi();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const clients = useQuery({ queryKey: ["clients"], queryFn: () => api<Client[]>("/clients") });

  const [name, setName] = useState("");
  const [objective, setObjective] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [fileMissing, setFileMissing] = useState(false);

  const create = useMutation({
    mutationFn: async () => {
      const c = await api<Client>("/clients", { json: { name, objective } });
      if (file) {
        const form = new FormData();
        form.append("file", file);
        await api<Portfolio>(`/clients/${c.id}/portfolios`, { form });
      }
      return c;
    },
    onSuccess: (c) => {
      qc.invalidateQueries({ queryKey: ["clients"] });
      navigate(`/clients/${c.id}`);
    },
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!file) { setFileMissing(true); return; }
    create.mutate();
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_360px]">
      <div>
        <div className="eyebrow">Your firm</div>
        <h1 className="mb-5 text-4xl">Clients</h1>
        {clients.isLoading && <Loading />}
        <ErrorNote error={clients.error} />
        {clients.data && clients.data.length === 0 && (
          <Empty>No clients yet. Add your first client and their holdings on the right.</Empty>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          {clients.data?.map((c) => (
            <Link key={c.id} to={`/clients/${c.id}`}
                  className="block border border-rule bg-card p-4 hover:border-gold">
              <div className="font-serif text-xl">{c.name}</div>
              <div className="mt-1 text-sm text-muted">{c.objective || "No objective recorded"}</div>
              <div className="mt-3 text-xs text-faint">
                Added {date(c.created_at)}
                {c.questionnaire ? " · Risk profile on file" : " · Risk profile not yet taken"}
              </div>
            </Link>
          ))}
        </div>
      </div>
      <Panel title="New client analysis" className="self-start">
        <form onSubmit={submit} className="space-y-4">
          <Field label="Household or client name">
            <input className="input" required maxLength={200} value={name} onChange={(e) => setName(e.target.value)}
                   placeholder="e.g. Sample Household A" />
          </Field>
          <Field label="Objective (optional)">
            <input className="input" maxLength={500} value={objective} onChange={(e) => setObjective(e.target.value)}
                   placeholder="e.g. retirement income, 15+ year horizon" />
          </Field>
          <FilePicker label="Holdings CSV" required accept=".csv,text/csv" file={file}
                      onChange={(f) => { setFile(f); if (f) setFileMissing(false); }}
                      error={fileMissing ? "Choose a holdings CSV to create the client." : null}
                      hint={<>Columns: ticker, shares, price, sector. Up to 2 MB.{" "}
                        <a className="underline" href="/holdings-template.csv" download>Download a template</a></>} />
          <ErrorNote error={create.error} />
          <button className="btn btn-primary w-full justify-center" disabled={create.isPending}>
            {create.isPending ? "Creating…" : "+ Create client"}
          </button>
        </form>
      </Panel>
    </div>
  );
}
