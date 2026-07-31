import { describe, expect, it } from "vitest";
import { EDICT_PRESETS, getPreset } from "./edictPresets";

const VALID_REVIEW = new Set(["always", "on_flag", "on_failure", "never"]);
const VALID_PROFILE = new Set(["checkpointed"]);
const VALID_EXECUTOR = new Set(["native"]);

describe("EDICT_PRESETS 定义完整性", () => {
  it("key 唯一且非空", () => {
    const keys = EDICT_PRESETS.map((p) => p.key);
    expect(new Set(keys).size).toBe(keys.length);
    expect(keys.every((k) => k.length > 0)).toBe(true);
  });

  it("每个预设有 icon 与 fields", () => {
    for (const p of EDICT_PRESETS) {
      expect(p.icon.length).toBeGreaterThan(0);
      expect(typeof p.fields).toBe("object");
    }
  });

  it("fields 里的枚举值合法", () => {
    for (const p of EDICT_PRESETS) {
      const f = p.fields as Record<string, string>;
      if (f.review_policy) expect(VALID_REVIEW.has(f.review_policy)).toBe(true);
      if (f.executor) expect(VALID_EXECUTOR.has(f.executor)).toBe(true);
      if (f.execution_profile) expect(VALID_PROFILE.has(f.execution_profile)).toBe(true);
    }
  });

  it("longTask 预设统一使用可恢复的 checkpointed profile", () => {
    for (const p of EDICT_PRESETS) {
      if (p.longTask) {
        expect(p.fields.execution_profile).toBe("checkpointed");
      }
    }
  });

  it("实验性客卿不作为普通用户任务类型", () => {
    expect(getPreset("keqing")).toBeUndefined();
  });

  it("quick 预设是默认态(不长程/自研引擎)", () => {
    const quick = getPreset("quick");
    expect(quick?.longTask).toBe(false);
    expect(quick?.fields.executor).toBe("native");
    expect(quick?.fields.review_policy).toBe("on_failure");
  });
});

describe("getPreset", () => {
  it("命中返回预设", () => {
    expect(getPreset("analysis")?.key).toBe("analysis");
  });
  it("未命中/空返回 undefined", () => {
    expect(getPreset("ghost")).toBeUndefined();
    expect(getPreset(null)).toBeUndefined();
  });
});
