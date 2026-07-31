import { describe, expect, it } from "vitest";
import {
  buildSidebarItems,
  sidebarSectionForPath,
  sidebarSelectedKey,
} from "./departments";

type TestMenuItem = {
  key?: string | number;
  type?: string;
  children?: TestMenuItem[];
};

const t = (key: string) => key;

function sidebarItems() {
  return buildSidebarItems(t, 0) as TestMenuItem[];
}

function itemByKey(items: TestMenuItem[], key: string) {
  const item = items.find((candidate) => candidate.key === key);
  if (!item) throw new Error(`Missing sidebar item: ${key}`);
  return item;
}

describe("sidebar information architecture", () => {
  it("keeps six primary destinations with exactly two levels", () => {
    const items = sidebarItems();
    expect(items.map((item) => item.key)).toEqual([
      "/control",
      "nav-tasks",
      "nav-court",
      "nav-offices",
      "nav-lab",
      "nav-settings",
    ]);

    expect(itemByKey(items, "nav-tasks").children?.map((item) => item.key)).toEqual([
      "/approvals",
      "/edicts/create",
      "/scheduler",
      "/audit",
    ]);
    expect(itemByKey(items, "nav-court").children?.map((item) => item.key)).toEqual([
      "/personas",
      "/consultation",
      "/cabinet",
    ]);
    expect(itemByKey(items, "nav-offices").children?.map((item) => item.key)).toEqual([
      "/memory",
      "/hongluisi",
      "/tongzheng",
    ]);
    expect(itemByKey(items, "nav-lab").children?.map((item) => item.key)).toEqual([
      "/evolution",
      "/universes",
      "/evals",
      "/keqing",
    ]);
    expect(itemByKey(items, "nav-settings").children?.map((item) => item.key)).toEqual([
      "/system",
      "/session-rules",
      "/cost",
    ]);

    const children = items.flatMap((item) => item.children ?? []);
    expect(children.every((item) => item.type !== "group" && !item.children)).toBe(true);
  });

  it.each([
    ["/approvals", "nav-tasks"],
    ["/edicts", "nav-tasks"],
    ["/edicts/create", "nav-tasks"],
    ["/edicts/01ABC", "nav-tasks"],
    ["/scheduler", "nav-tasks"],
    ["/audit", "nav-tasks"],
    ["/dag/01ABC", "nav-tasks"],
    ["/personas", "nav-court"],
    ["/personas/official-1", "nav-court"],
    ["/consultation", "nav-court"],
    ["/cabinet", "nav-court"],
    ["/memory", "nav-offices"],
    ["/hongluisi", "nav-offices"],
    ["/tongzheng", "nav-offices"],
    ["/evolution", "nav-lab"],
    ["/universes", "nav-lab"],
    ["/evals", "nav-lab"],
    ["/keqing", "nav-lab"],
    ["/system", "nav-settings"],
    ["/session-rules", "nav-settings"],
    ["/cost/", "nav-settings"],
    ["/control", null],
    ["/unknown", null],
  ])("maps %s to its parent section", (pathname, expected) => {
    expect(sidebarSectionForPath(pathname)).toBe(expected);
  });

  it("keeps detail routes attached to their visible destination", () => {
    expect(sidebarSelectedKey("/edicts/create")).toBe("/edicts/create");
    expect(sidebarSelectedKey("/edicts/01ABC")).toBe("/approvals");
    expect(sidebarSelectedKey("/dag/01ABC")).toBe("/approvals");
    expect(sidebarSelectedKey("/personas/official-1")).toBe("/personas");
    expect(sidebarSelectedKey("/memory/")).toBe("/memory");
  });
});
