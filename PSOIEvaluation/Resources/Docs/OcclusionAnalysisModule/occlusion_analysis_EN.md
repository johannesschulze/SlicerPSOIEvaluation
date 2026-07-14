---
title: Occlusion Analysis Workflow
author: Johannes Schulze
date: 2026-07-14
lang: en
---

**Module:** OcclusionAnalysisModule  
**Purpose:** Longitudinal quantitative analysis of occlusal contact change from intraoral surface scans (IOS) in maximum intercuspation position (MIP)  
**Target audience:** Clinical staff and researchers in orthognathic and reconstructive surgery

---

# Prerequisites

- 3D Slicer (version 5.0 or later)
- The extension **SlicerPSOIEvaluation** must be installed
- IOS scans in MIP as STL or OBJ surface models, one upper and one lower arch per timepoint
- All scans must already be in a common coordinate system **or** aligned within this module via the built-in ICP registration
- PDF report generation requires **WeasyPrint** (installed automatically via pip on first use)

Open the module: Press **CTRL+F** (*Module Finder*) and type "Occlusion", or navigate to *PSOI Evaluation → Occlusion Analysis*.

---

# Overview

The module computes the *occlusal change vector* — three complementary metrics (OCA, OCN, OAS) — by comparing MIP intraoral scans across an arbitrary number of timepoints.

| Step | Task |
|------|------|
| **1. Add timepoints** | Assign upper and lower arch models to each timepoint |
| **2. Trim models** *(optional)* | Remove excess scan geometry using a curve outline and/or bounding box |
| **3. Orient T0** | Set the reference orientation interactively |
| **4. Register T1, T2, …** | Rigid ICP alignment of all follow-up scans to T0 |
| **5. Settings** | Configure the contact threshold τ, sector count, and minimum area |
| **6. Run analysis** | Compute OCA, OCN, and OAS; populate result tables |
| **7. Occlusion maps** *(optional)* | Colour-coded distance maps for visual inspection |
| **8. Cast models** *(optional)* | Orthodontic art-base casts for standardised screenshots |
| **9. Screenshots** | Render PNG images in standardised views |
| **10. Report** | Generate a self-contained HTML/PDF report |

---

# Step 1: Add Timepoints

Each timepoint represents one clinical time point (T0 = reference / immediate postoperative, T1, T2 = follow-up visits).

1. Click **+ Add timepoint** to add a new row.
2. Enter a short **label** (e.g. `T0`, `T1`, `6m`, `12m`). The label is used in table column headers, file names, and on the cast model.
3. In the **Upper** field, select the upper arch IOS mesh node for this timepoint.
4. In the **Lower** field, select the lower arch IOS mesh node.
5. Repeat for all timepoints.

> **Note:** The order of timepoints matters. T0 is always treated as the reference for ICP registration and delta calculations. The first row is T0.

---

# Step 2: Trim Models *(optional)*

Clicking the **✂** button next to an arch model opens the trim dialog. This allows excess geometry — roots, gingiva, scanner artefacts — to be removed before analysis.

While the dialog is open:
- All visible jaw models become **semi-transparent** (35 % opacity).
- The 3D view automatically switches to the **inferior** view (upper arch) or **superior** view (lower arch) for a clear occlusal perspective.

## XY Outline Trim

Restricts the model to the region enclosed by a user-drawn closed curve, projected vertically (Z-axis). This accounts for the arch-shaped anatomy of the jaw and is preferable to a simple rectangular bounding box.

**To draw a new outline:**

1. Click **Draw new outline** — markup placement mode is activated.
2. Click around the desired arch boundary in the 3D view. Control points snap to the XY plane.
3. Close the curve by clicking on the first control point.

**To reuse an existing outline:**

1. Select the desired closed-curve node from the **Curve** dropdown.
2. The curve is made visible for review.

The clip is a strict vertical extrusion of the curve's XY footprint — the Z coordinates of the control points are ignored. Everything outside the enclosed area is removed.

## Bounds Box Trim

A bounding box clips the model in all three axes, useful for setting upper and lower vertical limits (e.g. to remove root tips or a scanner platform) or anterior/posterior limits.

**To create a new bounds box:**

1. Click **Create bounds box** — an interactive ROI box is placed around the current model.
2. The 3D view switches to the **anterior** view.
3. Drag the box handles to the desired boundaries.

**To reuse an existing bounds box:**

1. Select the desired ROI node from the **Bounds box** dropdown.
2. The view switches to the anterior view for review.

Both constraints (curve outline and bounds box) can be active simultaneously.

## Applying the Trim

| Button | Effect |
|--------|--------|
| **Apply trim** | Applies the active curve/box to the currently open model only |
| **Apply to all models** | Applies the same curve/box to every arch model of all timepoints |

After trimming, the curve and ROI nodes are kept in the scene (hidden). They can be selected again in the trim dialog for further adjustments or applied to additional models.

> **Note:** Trimming modifies the mesh in place and cannot be undone. Save the Slicer scene as `.mrb` before trimming if you may need to revert.

![](Screenshots/02_trim_dialog.png)

---

# Step 3: Orient T0

The T0 orientation defines the global reference frame for all analyses and visualisations.

1. Click **Orient T0 interactively** — transform handles appear on the T0 models.
2. Rotate and translate using the transform handles until:
   - The **occlusal plane** is horizontal (roughly parallel to the XY plane).
   - The **dental midline** is centred on x = 0.
   - The **posterior** direction points in the −Y direction.
3. Click **Confirm T0 orientation** — the transform is hardened into the mesh vertices and the transform node is removed.

> **Note:** The widget pivot is fixed at world origin (0, 0, 0). Use *ALT + drag* on the centre crosshair to reposition the pivot if needed.

> **Important:** All subsequent ICP registrations and distance computations use the T0 coordinate system as ground truth. Confirm the orientation before proceeding.

![](Screenshots/03_orient_t0.png)

---

# Step 4: Register T1, T2, … to T0

Click **Register T1, T2, … to T0 (ICP)**.

For each follow-up timepoint the module runs rigid iterative closest-point (ICP) registration:

- **Source:** Ti upper arch mesh
- **Target:** T0 upper arch mesh
- **Result:** The same rigid transform is applied to both the upper and lower arch of Ti

T0 is never moved. The transforms are hardened into the mesh vertices after registration.

> **Note:** ICP convergence depends on a reasonable initial alignment. If T0 and a follow-up scan start in very different orientations, ICP may converge to a local minimum. Pre-align the scans before importing, or improve the T0 orientation in step 3 first.

> **Note:** Intra-timepoint (upper ↔ lower) MIP registration is not performed by this module. The scans must already be in MIP relation when imported.

---

# Step 5: Settings

| Parameter | Default | Description |
|-----------|---------|-------------|
| **Contact threshold τ** | `0.05 mm` | Distance from the lower arch surface to the upper arch surface at which a point is considered to be in contact (Liu et al. 2020). Adjust between 0.01 and 0.5 mm. |
| **Sectors (OCN)** | `6` | Number of arch sextants for the regional OCN count (method B). Range: 2 – 12. |
| **Minimum contact area** | `0.1 mm²` | Minimum area per sector or cluster to count as an active contact region. Filters out isolated contact speckles. Range: 0 – 10 mm². |

A **sensitivity analysis** is always run at τ ∈ {0.03, 0.05, 0.08} mm in addition to the primary threshold, regardless of the τ setting.

---

# Step 6: Run Analysis

Click **Run analysis!**.

The module iterates over all timepoints and all threshold values, then writes three table nodes to the Slicer scene.

## Output Tables

| Table node | Contents |
|------------|----------|
| `OcclusionVectors` | Per-timepoint values of OCA, OCN (regional), OCN (cluster), and OAS at each threshold τ |
| `OcclusionDeltas` | Pairwise deltas (T0→Ti and consecutive) at each τ, with robustness flags and verdict |
| `OcclusionSummary` | One row per comparison at the primary τ — compact summary suitable for export |

The tables are visible in the Data module and can be exported as CSV via right-click → *Export to file*.

## Comparison Pairs

For *n* timepoints, the module computes:
- **Baseline comparisons:** T0 → T1, T0 → T2, …, T0 → T(n−1)
- **Consecutive comparisons:** T0 → T1, T1 → T2, …, T(n−2) → T(n−1)

---

# Metrics Reference

## OCA — Occlusal Contact Area (mm²)

Total surface area of lower arch triangles within τ distance of the upper arch surface. A triangle counts as *in contact* if the gap at any of its three vertices is ≤ τ.

The signed distance (negative for penetration) is computed via `vtkDistancePolyDataFilter`.

## OCN — Occlusal Contact Number

Computed by two independent methods reported separately:

**Method B — Regional (sextants):** The arch is divided into equal angular sectors. A sector counts as active if its total contact area is ≥ *minimum contact area*. This is the primary OCN output and appears in the verdict.

**Method A — Cluster count:** Geometrically connected contact patches are extracted. A patch counts if its total area is ≥ *minimum contact area*. Reported as a cross-check.

## OAS — Occlusal Asymmetry Score (0–1)

Normalised left–right imbalance of contact area:

```
OAS = |A_right − A_left| / (A_right + A_left)
```

OAS = 0 means perfect symmetry; OAS = 1 means all contact is entirely unilateral. Returns *n/a* when OCA = 0 (no contact detected).

## Sensitivity Analysis and Robustness

Each metric delta (e.g. ΔOCA = OCA_Ti − OCA_T0) is flagged as *robust* if its sign is consistent across all three sensitivity thresholds {0.03, 0.05, 0.08} mm. A delta that changes sign between thresholds is flagged as *not robust*.

## Verdict

A verdict is assigned to each comparison:

| Verdict | Condition (all metrics robust) |
|---------|-------------------------------|
| **Improved** | ΔOCA > 0 **and** ΔOCN > 0 **and** ΔOAS < 0 |
| **Worsened** | ΔOCA < 0 **and** ΔOCN ≤ 0 **and** ΔOAS > 0 |
| **Inconclusive** | Any robustness flag is false, or conditions for improved/worsened are not met |

---

# Step 7: Occlusion Maps *(optional)*

Click **Create occlusion maps**.

For each timepoint a colour-mapped model is created, showing the signed distance from the lower arch surface to the upper arch surface. The map is useful for identifying specific contact regions and comparing their distribution across timepoints.

| Setting | Value |
|---------|-------|
| Colour range | 0.0 – 0.1 mm |
| Points beyond | ± 0.2 mm hidden |
| Z offset | +0.1 mm (prevents Z-fighting with the source mesh) |

Map nodes are named `{label}_distmap_upper` and appear as children of the source model in the subject hierarchy.

---

# Step 8: Cast Models *(optional)*

Click **Create cast models** to generate an orthodontic art-base cast for each arch of each timepoint. Casts are used to produce standardised screenshots and for visual inspection.

## Geometry

Each cast consists of two parts:
- **Arch walls:** Extruded from the largest boundary loop of the IOS mesh.
- **Hexagonal prism base:** 5 mm height, 2.5 mm lateral margin beyond the mesh bounding box, symmetric around x = 0.

The timepoint label (e.g. `T0`) is printed in raised letters on the posterior face of the base.

## Smooth Cast Walls

Click **Smooth cast walls** to rebuild the cast with the arch boundary loop resampled to uniform arc-length spacing. This eliminates vertical stripe rendering artefacts that can appear when the original IOS mesh has highly irregular vertex spacing. This step is optional and takes additional processing time.

![](Screenshots/08_cast_models.png)

---

# Step 9: Screenshots

Set the **Output folder** path, then click **Take screenshots**.

## Views

| View | Camera direction | Captured by default |
|------|-----------------|---------------------|
| **Occlusal** | Upper: inferior; Lower: superior | Yes |
| **Butterfly** | Upper (inferior) + Lower (superior) side by side | Yes |
| **Buccal / lateral** | Left oblique, anterior, right oblique, left, posterior, right | No |

## Per-View Options

Each view has three independent checkboxes:

| Checkbox | Effect |
|----------|--------|
| **Capture** | Whether this view is rendered at all |
| **Show cast** | Whether cast models replace the IOS meshes in this view |
| **Show legend** | Whether the occlusion-map colour bar is included |

## Other Settings

| Parameter | Default | Description |
|-----------|---------|-------------|
| **Resolution** | `500 px` | Width and height of each output PNG |
| **Normalize zoom** | On | All timepoints in the same view share the same camera zoom, computed from the combined bounding box |

## File Names

Files are saved as `{label}_{view}.png`, e.g. `T0_upper.png`, `T1_butterfly.png`, `T0_left.png`.

> **Note:** Markup nodes (trim curves, bounds boxes) are automatically hidden during screenshot capture and their previous visibility is restored afterwards.

![](Screenshots/09_screenshots.png)

---

# Step 10: Generate Report

Click **Generate report** after taking screenshots.

The module renders a Jinja2 HTML template and saves two files to the screenshot folder:

| File | Generated when |
|------|---------------|
| `occlusion_analysis_report.html` | Always; self-contained with base-64 embedded images |
| `occlusion_analysis_report.pdf` | When WeasyPrint is available (installed automatically on first run) |

## Report Contents

- Title block with generation date
- Per-timepoint rendering grids (occlusal, buccal/lateral views)
- Cross-timepoint time-series image grid
- **Vectors table:** OCA, OCN, OCN cluster, OAS per timepoint at the primary τ
- **Summary table:** Pairwise deltas with robustness flags and colour-coded verdicts

---

# Tips and Notes

- **Save the scene regularly:** Timepoint assignments, trim curves, ROI boxes, result tables, and cast models all persist in the `.mrb` scene file. Save frequently to avoid re-running computation-intensive steps.
- **Midline at x = 0:** The cast base and the left/right OAS split are computed relative to x = 0. Ensure the dental midline is centred on x = 0 when orienting T0.
- **Apply trim to all models:** When all timepoints are co-registered and the same arch outline applies to all, use *Apply to all models* to save time.
- **Reuse trim nodes:** After the first trim, the curve and bounds box nodes are kept in the scene. Open the trim dialog for any other model and select the existing node from the dropdown instead of redrawing.
- **Sensitivity analysis:** If the verdict is *Inconclusive* due to sign inconsistency across thresholds, inspect the raw delta values at each τ in the `OcclusionDeltas` table to understand which threshold drives the inconsistency.
- **Multiple cases:** Run the full workflow once per patient case. Save each case as a separate `.mrb` file and transfer the relevant rows from the `OcclusionSummary` CSV to a master spreadsheet.

---

# Troubleshooting

| Problem | Possible cause | Solution |
|---------|---------------|---------|
| Trim removes the entire model | Curve control points placed outside the model's XY bounds; or bounds box too small | Check the curve/box in the anterior or top view before applying. Adjust handles. |
| ICP converges to wrong position | Follow-up scan starts in a very different orientation | Manually pre-align the model to T0 in the Transforms module before running ICP |
| OCA = 0 at all timepoints | Models not in MIP (not touching); or τ too small | Verify scans are in MIP; increase τ; inspect gap in the 3D view |
| OAS returns *n/a* | OCA = 0 — no contact detected | See *OCA = 0* row above |
| Cast wall artefacts (vertical stripes) | Irregular vertex spacing on the IOS mesh boundary | Click **Smooth cast walls** to resample the boundary loop |
| PDF not generated | WeasyPrint not installed or install failed | Check the Slicer Python console for error output; install WeasyPrint manually via *pip install weasyprint* in the Slicer Python interactor |
| Verdict always *Inconclusive* | Sign of one or more deltas inconsistent across sensitivity thresholds | Inspect individual τ columns in `OcclusionDeltas`; consider whether the clinical change is within measurement uncertainty |
| Report images missing | Screenshots not yet taken, or output folder changed | Run *Take screenshots* with the same output folder before generating the report |
| Cast label engraving not working | Known limitation: boolean subtraction fails for the non-convex hex prism geometry | Label is shown as 0.2 mm raised text — this is expected and does not affect analysis results |
