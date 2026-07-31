// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { useLocaleProvider } from "./useLocale";

function LocaleProbe() {
  const locale = useLocaleProvider();
  return (
    <>
      <button type="button" onClick={() => locale.setLocale("en")}>English</button>
      <button type="button" onClick={() => locale.setLocale("zh-modern")}>中文</button>
    </>
  );
}

afterEach(cleanup);

describe("document locale", () => {
  it("keeps the html language synchronized with the selected UI locale", async () => {
    render(<LocaleProbe />);

    await userEvent.click(screen.getByRole("button", { name: "English" }));
    expect(document.documentElement).toHaveAttribute("lang", "en");

    await userEvent.click(screen.getByRole("button", { name: "中文" }));
    expect(document.documentElement).toHaveAttribute("lang", "zh-CN");
  });
});
