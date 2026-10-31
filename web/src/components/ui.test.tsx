import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { ComparisonTable, FilePicker } from "./ui";

describe("ComparisonTable", () => {
  it("shows the engine's change figure rather than computing one", () => {
    // A deliberately inconsistent row: if the table computed after - before it would
    // show +0.2; it must show the engine's -9.9.
    render(<ComparisonTable rows={[{ key: "effective_bets", label: "Effective independent bets",
                                      kind: "number", before: 1.3, after: 1.5, change: -9.9 }]} />);
    expect(screen.getByText("−9.9")).toBeTruthy();
    expect(screen.queryByText("+0.2")).toBeNull();
  });
});

describe("FilePicker", () => {
  function Harness() {
    const [file, setFile] = useState<File | null>(null);
    return <FilePicker label="Holdings CSV" accept=".csv" file={file} onChange={setFile} />;
  }

  it("goes from 'No file chosen' to the chosen file's name, and back on remove", () => {
    render(<Harness />);
    expect(screen.getByText("No file chosen")).toBeTruthy();
    const input = screen.getByLabelText("Holdings CSV") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["ticker"], "smith_household.csv")] } });
    expect(screen.queryByText("No file chosen")).toBeNull();
    expect(screen.getByTestId("chosen-file").textContent).toBe("smith_household.csv");
    fireEvent.click(screen.getByRole("button", { name: "Remove smith_household.csv" }));
    expect(screen.getByText("No file chosen")).toBeTruthy();
  });

  it("shows an error message when one is given", () => {
    render(<FilePicker label="Holdings CSV" file={null} onChange={() => {}} error="Choose a holdings CSV." />);
    expect(screen.getByRole("alert").textContent).toBe("Choose a holdings CSV.");
  });
});
