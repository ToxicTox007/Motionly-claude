import { afterEach, describe, expect, it } from "vitest";
import {
  isEditableTarget,
  isRichTextTarget,
  shouldIgnoreShortcut,
} from "@/lib/keyboard-shortcuts";

function keydown(target: EventTarget, init: KeyboardEventInit) {
  const event = new KeyboardEvent("keydown", {
    bubbles: true,
    cancelable: true,
    ...init,
  });
  Object.defineProperty(event, "target", { value: target });
  return event;
}

function mount<T extends HTMLElement>(element: T) {
  document.body.append(element);
  return element;
}

afterEach(() => {
  document.body.replaceChildren();
});

describe("isEditableTarget", () => {
  it("treats form fields and contenteditable regions as editable", () => {
    expect(isEditableTarget(mount(document.createElement("input")))).toBe(true);
    expect(isEditableTarget(mount(document.createElement("textarea")))).toBe(
      true,
    );
    expect(isEditableTarget(mount(document.createElement("select")))).toBe(
      true,
    );

    const editor = mount(document.createElement("div"));
    editor.setAttribute("contenteditable", "true");
    const child = document.createElement("span");
    editor.append(child);
    expect(isEditableTarget(editor)).toBe(true);
    expect(isEditableTarget(child)).toBe(true);
  });

  it("does not treat buttons, plain elements, checkboxes or the window as editable", () => {
    expect(isEditableTarget(mount(document.createElement("button")))).toBe(
      false,
    );
    expect(isEditableTarget(mount(document.createElement("div")))).toBe(false);
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    expect(isEditableTarget(mount(checkbox))).toBe(false);
    expect(isEditableTarget(window)).toBe(false);
    expect(isEditableTarget(null)).toBe(false);
  });

  it('ignores contenteditable="false" islands', () => {
    const island = mount(document.createElement("div"));
    island.setAttribute("contenteditable", "false");
    expect(isEditableTarget(island)).toBe(false);
  });
});

describe("isRichTextTarget", () => {
  it("is true for textareas and contenteditable, false for single-line inputs", () => {
    expect(isRichTextTarget(mount(document.createElement("textarea")))).toBe(
      true,
    );
    const editor = mount(document.createElement("div"));
    editor.setAttribute("contenteditable", "");
    expect(isRichTextTarget(editor)).toBe(true);
    expect(isRichTextTarget(mount(document.createElement("input")))).toBe(
      false,
    );
  });
});

describe("shouldIgnoreShortcut", () => {
  it("lets a plain key on the page through", () => {
    const body = document.body;
    expect(shouldIgnoreShortcut(keydown(body, { key: "k" }))).toBe(false);
  });

  it("ignores events already handled or mid-IME-composition", () => {
    const handled = keydown(document.body, { key: "k" });
    handled.preventDefault();
    expect(shouldIgnoreShortcut(handled)).toBe(true);
    expect(
      shouldIgnoreShortcut(
        keydown(document.body, { key: "k", isComposing: true }),
      ),
    ).toBe(true);
  });

  it("ignores typing in editable targets, including contenteditable", () => {
    const editor = mount(document.createElement("div"));
    editor.setAttribute("contenteditable", "true");
    expect(shouldIgnoreShortcut(keydown(editor, { key: "k" }))).toBe(true);
    const input = mount(document.createElement("input"));
    expect(shouldIgnoreShortcut(keydown(input, { key: "k" }))).toBe(true);
  });

  it('with editable: "rich-text" still fires in single-line inputs', () => {
    const input = mount(document.createElement("input"));
    const textarea = mount(document.createElement("textarea"));
    expect(
      shouldIgnoreShortcut(keydown(input, { key: "k", metaKey: true }), {
        editable: "rich-text",
      }),
    ).toBe(false);
    expect(
      shouldIgnoreShortcut(keydown(textarea, { key: "k", metaKey: true }), {
        editable: "rich-text",
      }),
    ).toBe(true);
  });

  it("with requireNoModifiers ignores Ctrl, Meta and Alt chords but not Shift", () => {
    const body = document.body;
    const options = { requireNoModifiers: true };
    expect(
      shouldIgnoreShortcut(keydown(body, { key: "l", ctrlKey: true }), options),
    ).toBe(true);
    expect(
      shouldIgnoreShortcut(keydown(body, { key: "l", metaKey: true }), options),
    ).toBe(true);
    expect(
      shouldIgnoreShortcut(keydown(body, { key: "j", altKey: true }), options),
    ).toBe(true);
    expect(
      shouldIgnoreShortcut(
        keydown(body, { key: "L", shiftKey: true }),
        options,
      ),
    ).toBe(false);
  });

  it("ignores shortcuts while focus is inside a dialog unless allowed", () => {
    const dialog = mount(document.createElement("div"));
    dialog.setAttribute("role", "dialog");
    const button = document.createElement("button");
    dialog.append(button);
    expect(shouldIgnoreShortcut(keydown(button, { key: "k" }))).toBe(true);
    expect(
      shouldIgnoreShortcut(keydown(button, { key: "k" }), {
        allowInDialog: true,
      }),
    ).toBe(false);
  });
});
