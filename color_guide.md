# Color guide (fabric pattern, 2026)

LLM rules for choosing palette/colorways. Trend colors refresh each season — edit
this file, not code.

## Color-count (도수) by fabrication
- **yarn_dyed (선염, woven — stripe/check/gingham/chambray):** 2–8 colors. Hard-limited
  by loom color yarns; keep warp/weft yarn colors minimal. distinct colors per colorway
  MUST be ≤ production.max_colors.
- **print (날염):** flatbed screen 4–8, rotary 6–12 recommended; digital print is
  unlimited (gradients/multicolor OK). Treat as soft guidance — fewer colors = cheaper.
- Use the fabric ground as one of the colors to save a color-count.

## Colorway structure
- Colorway = same design, swapped colors; usually keep the SAME color count as the original.
- Pull all colors from ONE palette so designs read as a set; vary by adjusting area
  proportion, not by adding unrelated hues.
- A "default" colorway is required and must map every slot.

## Color harmony models
- Monochromatic (1 hue, vary value/chroma) — fewest colors, safest.
- Analogous (adjacent hues) — natural.
- Complementary (opposite hues) — strong contrast, motif pops.
- Triad / Tetrad — vivid but raises color count.
- Ensure enough contrast; dark low-contrast colors muddy together.
- Prefer standard codes in slot `spot` when known (PANTONE TCX / SCOTDIC / Coloro).

## Recommended colors (2026)
Use ONLY when the prompt does not specify colors. If the prompt names colors, use those.

- **Base / ground (large areas):** Cloud Dancer (soft off-white, PANTONE 11-4201, ideal
  ground/base), Sage Green, Angora, Cocoa Powder, clear greens.
- **Accent (small areas):** Transformative Teal, Electric Fuchsia, Blue Aura, Amber Haze
  (golden yellow), Acacia (yellow-green), Muskmelon (orange), Dusty Rose, Tea Rose,
  Mandarin Orange, Burnt Sienna, Amethyst Orchid, Burnished Lilac.
- Core 2026 direction: bright accents paired with natural neutral grounds.

## Apply formulas (examples)
- 3-color: Cloud Dancer (ground) + Sage Green (main motif) + Mandarin Orange OR Electric
  Fuchsia (accent).
- 4–5 color: Transformative Teal (main) + Amber Haze + Dusty Rose + Cloud Dancer + 1 neutral.
- Yarn-dyed stripe/check: Sage Green · Angora · Cocoa Powder neutral base (3 yarns) +
  1 Teal or Fuchsia yarn as a thin accent line.

## Checklist
- All colors from one palette? Colorway keeps same count?
- Fabric ground used as a color? Standard code in `spot` where known?
- Enough contrast (no muddy dark-on-dark)?
- Color count fits the fabrication (yarn_dyed 2–8 hard limit; print recommended 4–12, digital unlimited)?
