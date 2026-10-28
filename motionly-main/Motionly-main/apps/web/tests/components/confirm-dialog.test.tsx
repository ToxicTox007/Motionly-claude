import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { SubmitButton } from "@/components/submit-button";

function Harness({
  onConfirm,
  typedConfirmation,
}: {
  onConfirm: () => void | Promise<void>;
  typedConfirmation?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Delete
      </button>
      <ConfirmDialog
        open={open}
        title="Delete project?"
        body="This cannot be undone."
        confirmLabel="Delete project"
        typedConfirmation={typedConfirmation}
        onCancel={() => setOpen(false)}
        onConfirm={async () => {
          await onConfirm();
          setOpen(false);
        }}
      />
    </>
  );
}

describe("ConfirmDialog", () => {
  it("does nothing on Cancel or Escape", async () => {
    const onConfirm = vi.fn();
    render(<Harness onConfirm={onConfirm} />);
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("runs the action exactly once, however fast Confirm is clicked", async () => {
    let finish: () => void = () => {};
    const onConfirm = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
    );
    render(<Harness onConfirm={onConfirm} />);
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    const confirm = screen.getByRole("button", { name: "Delete project" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);

    // Pending: the dialog cannot be dismissed and Confirm is disabled.
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Working/ })).toBeDisabled();

    await act(async () => finish());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("requires the typed word before confirming a high-impact action", async () => {
    const onConfirm = vi.fn();
    render(<Harness onConfirm={onConfirm} typedConfirmation="DEAD-LETTER" />);
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    const confirm = screen.getByRole("button", { name: "Delete project" });
    expect(confirm).toBeDisabled();
    await userEvent.type(
      screen.getByLabelText(/Type DEAD-LETTER to confirm/),
      "DEAD-LETTER",
    );
    expect(confirm).toBeEnabled();
    await userEvent.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});

describe("SubmitButton", () => {
  it("submits once for a double click and shows the pending label", async () => {
    let finish: () => void = () => {};
    const action = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
    );
    render(
      <form action={action}>
        <SubmitButton pendingLabel="Saving…">Save</SubmitButton>
      </form>,
    );
    const button = screen.getByRole("button", { name: "Save" });
    await act(async () => {
      fireEvent.click(button);
      fireEvent.click(button);
    });
    expect(action).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: /Saving…/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Saving…/ })).toHaveAttribute(
      "aria-busy",
      "true",
    );
    await act(async () => finish());
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });
});
