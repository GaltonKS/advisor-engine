import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import Privacy, { PRIVACY_EFFECTIVE_DATE, PRIVACY_PDF } from "./Privacy";

describe("Privacy", () => {
  it("shows the policy with its effective date, sections and contact", () => {
    render(<MemoryRouter><Privacy /></MemoryRouter>);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("GaltonKS Privacy Policy");
    expect(screen.getByText(new RegExp(`Effective date: ${PRIVACY_EFFECTIVE_DATE}`))).toBeTruthy();
    for (const title of ["Galton AI", "Data Retention", "Your Choices and US State Privacy Rights", "Contact Us"]) {
      expect(screen.getByRole("heading", { level: 2, name: title })).toBeTruthy();
    }
    expect(screen.getByRole("link", { name: "Download PDF" }).getAttribute("href")).toBe(PRIVACY_PDF);
    expect(screen.getAllByRole("link", { name: "privacy@galtonks.com" })[0].getAttribute("href"))
      .toBe("mailto:privacy@galtonks.com");
  });
});
