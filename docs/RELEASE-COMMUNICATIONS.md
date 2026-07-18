# Release Communications

## Conditional Visual-Release Contract

Release-facing visuals are allowed only when they make the existing evidence
clearer without expanding the claim.

- Commit the referenced asset before the release story depends on it.
- Use a stable public-repo path, descriptive alt text, and a short caption.
- Keep the caption tied to checked evidence: commands, reports, tests, or
  release-contract facts.
- Raster-render each release visual at native size and at README-width; both
  renders must show no clipping, overlap, or crossed labels.
- Do not use visuals to imply authenticated identity, vendor certification,
  marketplace distribution, or external adoption.
- If a planned visual is missing at release cutoff, remove the embed or block
  the release story; do not ship broken images or describe planned artwork as
  shipped evidence.

## Machine Visual Inventory

Release visuals are tracked in `examples/diagrams/visual-claims.json`.

Each inventory item should name:

- the stable `asset_path`
- `asset_type`, currently `png` or `svg`
- the narrow claim the asset illustrates
- checked `evidence_paths`
- `release_applicability.status` and release number

Conceptual PNG hero images must also be labeled as conceptual, with
`semantic_validation = "not_performed"`, `authenticated_claim = false`, and
`product_evidence = false`.

The release contract treats the inventory as hygiene, not semantic proof. It can
check that inventoried assets exist, SVG files parse and include nonempty
`title` and `desc` metadata, PNG files have a valid signature and dimensions,
evidence paths exist, release applicability is explicit, and obvious private
machine paths are absent. It does not prove that a visual claim is true.

The native-size and README-width raster render gate is layout evidence only. It
does not authenticate identity, certify distribution, or prove that a conceptual
visual claim is true.
