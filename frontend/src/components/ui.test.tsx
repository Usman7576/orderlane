import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Button, StatusBadge } from "./ui";

describe("ui", () => {
  it("shows a readable status", () => {
    render(<StatusBadge status="pending_review" />);
    expect(screen.getByText("Needs review")).toBeInTheDocument();
  });

  it("disables a loading button", () => {
    render(<Button loading>Approve</Button>);
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
  });
});
