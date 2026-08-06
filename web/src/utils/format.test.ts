import { describe, expect, it } from "vitest";
import { formatDuration, formatTokens, truncateId } from "./format";

describe("formatTokens", () => {
  it("小于 1K 原样输出", () => {
    expect(formatTokens(999)).toBe("999");
  });
  it("K 级保留一位小数", () => {
    expect(formatTokens(1500)).toBe("1.5K");
  });
  it("M 级保留一位小数", () => {
    expect(formatTokens(2_500_000)).toBe("2.5M");
  });
});

describe("truncateId", () => {
  it("超长截断加省略号", () => {
    expect(truncateId("abcdefghij")).toBe("abcdefgh…");
  });
  it("不超长原样返回", () => {
    expect(truncateId("abc")).toBe("abc");
  });
});

describe("formatDuration", () => {
  it("分秒组合", () => {
    expect(formatDuration("2026-01-01T00:00:00Z", "2026-01-01T00:01:30Z")).toBe(
      "1m 30s",
    );
  });
  it("一分钟内只显示秒", () => {
    expect(formatDuration("2026-01-01T00:00:00Z", "2026-01-01T00:00:45Z")).toBe(
      "45s",
    );
  });
  it("缺起止返回占位符", () => {
    expect(formatDuration(null, "2026-01-01T00:00:45Z")).toBe("—");
  });
  it("负时长返回占位符", () => {
    expect(formatDuration("2026-01-01T00:01:00Z", "2026-01-01T00:00:00Z")).toBe(
      "—",
    );
  });
});
