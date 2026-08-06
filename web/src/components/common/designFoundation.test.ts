import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { palettes } from "../../theme/palette";

describe("restrained Tianshu design foundation", () => {
  it("exposes one restrained semantic token vocabulary in both themes", () => {
    for (const palette of [palettes.dark, palettes.light]) {
      expect(palette).toMatchObject({
        surface: expect.any(String),
        surfaceRaised: expect.any(String),
        focusRing: expect.any(String),
        decision: expect.any(String),
        blocked: expect.any(String),
      });
    }
  });

  it("keeps desktop density, focus and reduced-motion rules explicit", () => {
    const css = readFileSync(
      resolve(process.cwd(), "src/styles/global.css"),
      "utf8",
    );

    expect(css).toMatch(/font-size:\s*13px/);
    expect(css).toContain("--ts-color-focus-ring");
    expect(css).toContain("prefers-reduced-motion: reduce");
    expect(css).not.toMatch(
      /gold-border|dragon-pattern|paper-texture|neon-glow/i,
    );
  });

  it("keeps ordinary primary interactions neutral and derives the light shadow from ink", () => {
    const themeSource = readFileSync(
      resolve(process.cwd(), "src/theme/index.ts"),
      "utf8",
    );

    expect(themeSource).not.toContain("colorPrimaryHover: p.accent");
    expect(themeSource).toMatch(/boxShadowSecondary:.*p\.text/s);
  });
});
