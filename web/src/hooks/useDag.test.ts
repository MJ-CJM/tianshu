import { describe, expect, it } from "vitest";
import { dagByEdictPollInterval } from "./useDag";

describe("DAG polling", () => {
  it("stops polling after an Edict is authoritatively known to have no DAG", () => {
    expect(dagByEdictPollInterval(null, null)).toBe(false);
    expect(
      dagByEdictPollInterval(undefined, {
        status: 404,
        code: "not-found",
        message: "No DAG",
        correlationId: null,
        retryable: false,
      }),
    ).toBe(false);
  });

  it("keeps polling while the optional DAG read is still unresolved", () => {
    expect(dagByEdictPollInterval(undefined, null)).toBe(3000);
  });
});
