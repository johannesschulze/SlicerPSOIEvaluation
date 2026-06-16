---
title: PSI Analysis Workflow
author: Johannes Schulze
date: 2026-06-17
lang: en
---

**Module:** GeneralPSOIWorkflowModule  
**Purpose:** Evaluation of the three-dimensional position of patient-specific implants (PSI) compared to the preoperative plan  
**Target audience:** Clinical staff and researchers in PSI surgery

---

# Prerequisites

- 3D Slicer (version 5.0 or later)
- The extension **SlicerPSOIEvaluation** must be installed via the Extension Manager or manually
- Additional required extensions:
  - **ModelToModelDistance**
  - **SlicerIGT**
  - **SlicerRT**
  - **ModelRegistration** (listed as "SlicerModelRegistration" in the Extension Manager)
- CT datasets in HU-calibrated units (DICOM or NIfTI): preoperative CT + postoperative CT or intraoperative C-arm scan
- STL file of the planned PSI (mandatory)
- STL file of the planned skull / midface (optional; required when the PSI STL is delivered in a manufacturer-specific coordinate system that does not match the planning CT)

---

# Overview

The module guides the user through a complete PSI evaluation in six consecutive steps (tabs):

| Step | Task |
|------|------|
| **1. Prepare the Scene** | Volume and model selection, scene preparation, rough initial alignment |
| **2. Register preop and postop CTs** | Manual fine-tuning and automatic intensity-based CT registration (BRAINS) |
| **2.a (Optional) Register plan to preop CT** | Registration of the planning STLs into the preoperative CT coordinate system |
| **3. Segment intraop PSI** | Segmentation of the PSI from the postoperative CT |
| **4. Align PSIs** | Registration of the planning STL to the postoperative PSI position |
| **5. Calculate Comparison** | Point-to-point distance analysis |
| **6. Output Results** | Calculation and export of all metrics as a CSV file |

Steps 2.a and parts of step 3 are situationally optional.

---

# Step 0: Loading Data and Naming Convention

## Importing data into 3D Slicer

**STL files:** Drag and drop into the Slicer window.

**DICOM datasets:** Drag and drop into Slicer and confirm *Load directory into DICOM database* in the dialog. Then open the DICOM browser and double-click the desired case to load it. Warnings can be dismissed.

## Naming convention

For clarity, imported datasets should be named consistently (double-click the name in the data list or press F2):

| Dataset | Recommended name |
|---------|-----------------|
| STL file of the planned PSI | `PSI planned` |
| STL file of the skull / midface (if available) | `Skull planned` |
| Preoperative CT dataset | `preop volume` |
| Postoperative CT / C-arm dataset | `postop volume` |

> **Note:** The module assigns names to the pre- and postoperative volumes automatically when *Prepare Scene* is clicked. Renaming is still recommended so that the correct datasets can be easily identified during selection.

Open the module: Either press CTRL+F (*Module Finder*) and type "General PSOI", or navigate in the module menu to *PSOI Evaluation → General PSOI Analysis Workflow*.

![](Screenshots/00_startup_and_volume_selection.png)

---

# Step 1: Prepare the Scene

## Select inputs

| Field | Content |
|-------|---------|
| **Preop volume** | Preoperative CT dataset |
| **Postop volume** | Postoperative CT / C-arm dataset |
| **Skull Planned** | STL of the planned skull (only needed when planning STLs need to be registered into the preoperative CT coordinate system) |

![](Screenshots/01_prepare_scene.png)

## Optional: Crop volumes

If the CT datasets cover a larger body region than necessary, they can be cropped beforehand to save computation time:

1. Click **Crop volume** → an adjustable ROI box appears in the scene.
2. Adjust the box by dragging its handles to the desired boundaries.
3. Click **Apply crop** → the dataset is permanently cropped to the selected region.

Separate crop buttons are available for the preoperative and postoperative volumes.

## Optional: Midsagittal alignment

The button **1a. Align to Midline (PCA)** automatically computes a yaw angle from the cortical bone distribution in the preoperative CT to align the midsagittal plane with x = 0. The resulting transform is displayed as interactively adjustable handles, allowing manual fine-correction.

> **Note:** This step is optional and most useful when the patient was positioned with a pronounced tilt.

## Prepare Scene

Clicking **1. Prepare Scene** performs the following:

- Renames the volumes to `preop volume` / `postop volume`
- Hardens the midsagittal alignment transform into the preoperative CT (if present)
- Roughly aligns the postoperative volume to the preoperative CT (center-of-mass alignment)
- Activates interactive transform handles for manual fine-tuning

---

# Step 2: Register CT Scans

## Manual pre-alignment

During scene preparation (step 1) the two volumes were already roughly overlaid. This step refines the alignment manually to improve the subsequent automatic registration.

In the axial, coronal, and sagittal slice views the postoperative volume is blended over the preoperative one at 50 % opacity. The transform handles allow the postoperative volume to be translated and rotated:

- **Grey circle:** Rotation
- **Crosshair in the centre:** Translation
- **ALT + drag crosshair:** Move the centre of rotation

**Recommended alignment workflow:**

1. **Sagittal view (yellow):** Set the vertical and sagittal position. Useful landmarks: frontal sinus, sphenoid sinus, sella turcica, hard palate.
2. **Coronal view (green):** Correct the transverse alignment. A useful landmark is the infra-orbital canal on the healthy side (set the centre of rotation there, then correct rotation around the sagittal axis).
3. **Axial view:** Overlay the zygomatic prominence on the healthy side, set the centre of rotation there, and fine-tune rotation. Do not orient to the fractured contralateral side.

> Afterwards, scroll through all three views to verify the alignment. The midface and skull base should overlap as precisely as possible; deviations in the cervical spine due to different patient positioning are acceptable.

## Optional: Registration mask

A segmentation mask can be used to restrict the registration to a specific anatomical region (e.g. only the orbit or only the mandible). Clicking **2a. Draw registration mask (optional)** opens the Segment Editor with the Scissors tool active. The drawn segment is used as an ROI for BRAINS registration.

## Automatic registration

Clicking **2b. Register CT-Scans (BRAINS)** starts the rigid intensity-based registration. Depending on hardware performance this takes 1–5 minutes. After completion, the Normalized Cross-Correlation (NCC) of the registration is printed in the console.

> **Check the result:** After registration, scroll through all three views again. The overlay in the midface and skull base should be nearly perfect.

---

# Step 2.a (Optional): Register Planning STL to Preoperative CT

This step is **only required** when the STL files were not delivered in the coordinate system of the preoperative CT (recognisable because the model appears far from the CT volumes after import).

The registration is performed in three sub-steps:

## 1. Pre-align planning STLs

Clicking **1. Recenter the planning STLs** centres the skull STL roughly on the preoperative volume and activates transform handles for manual fine-tuning. The goal is a reasonably correct starting position for the subsequent automatic registration.

> It is worth spending a few minutes here, as the quality of the manual pre-alignment directly affects the registration accuracy.

## 2. Segment the preoperative CT

Clicking **2. Segment the preop CT** creates a bone segmentation of the preoperative CT (threshold 200 HU, restricted to the bounding box of the skull STL). The result is displayed as a 3D surface model.

> **Alternative:** If a surface model of the skull is already available from another source (e.g. from a previous session), it can be selected via the *or select existing model* dropdown and the segmentation step skipped. Then click **Register to selected model**.

## 3. Register planning STL

Clicking **3. Register plan STLs to preop CT** performs an ICP (Iterative Closest Point) registration of the skull STL to the segmented preoperative model. After completion, the RMS (Root Mean Square) registration error is shown in a dialog.

> **Quality criterion:** The RMS should be < 0.5 mm. In the 3D view the STL (green) and the segmentation (grey) should alternate evenly — a one-sided colour dominance indicates a systematic offset.

> **Important:** Any registration errors here accumulate with errors in the subsequent analysis steps. Check this result carefully.

---

# Step 3: Segment the Intraoperative PSI

## Select the PSI model

In the **PSI Planned** field, select the STL model of the planned PSI. In cases with multiple PSIs, this tab can be revisited at any time by selecting a different model and repeating the workflow from here.

## Optional: Transfer the planning STL into the preoperative coordinate system

If step 2.a was performed, the PSI STL must also be transformed into the preoperative coordinate system: clicking **3.a Align PSI to preop CT (optional)** applies the transform computed in step 2.a to the currently selected PSI model.

After this step the PSI STL should be visible at the correct anatomical position in the 3D view.

## Segment the PSI

Clicking **3.b Segment intraop PSI** performs the following:

1. The postoperative volume is cropped to the region of the planned PSI (bounding box of the PSI model with a 2 cm margin).
2. The program switches to the *Segment Editor*.
3. The Threshold tool is preset to 1750 HU and applied automatically.

Two editing steps are typically required in the Segment Editor:

1. **Adjust the threshold:** If too much (e.g. bone included) or too little (PSI not fully captured) was segmented, adjust the lower threshold. The outlines of the PSI should be clearly delineated; the internal structure is less critical.
2. **Remove excess structures:** Use the **Scissors** tool to remove all segmented regions that do not belong to the PSI (screws, other plates, bone fragments).

![](Screenshots/03_segmentation.png)

When finished, switch back to the **General PSOI Analysis Workflow** module.

## Use an existing segmentation

If the PSI was already segmented in a previous session, select the existing segmentation from the *or use existing* dropdown and confirm with **Use selected**.

---

# Step 4: Align PSIs

## Automatic registration

Clicking **4. Align PSIs** performs the following:

1. The selected segmentation is converted to a surface model (only the selected segment is exported).
2. A copy of the planning STL is created and registered to the postoperative model using the ICP algorithm.
3. The transform is displayed as interactively adjustable handles.
4. After completion, the RMS value is shown in a dialog.

> **Quality criterion:** The RMS should be < 0.5 mm. If the alignment is not satisfactory, it can be corrected manually using the displayed transform handles. The PSI has a minimal degree of flexibility — aim for a globally good overlay rather than perfection in every region.

![](Screenshots/04_align_psis.png)

## Manual alignment (without segmentation)

As an alternative to segmentation, the button **4b. Manual alignment (no segmentation)** is available. It:

- Creates a copy of the planning STL in the scene
- Creates a centred linear transform with interactive handles
- Does **not** perform automatic registration

This option is suitable for cases where the PSI is poorly visible in the postoperative CT and a manual assignment is required.

---

# Step 5: Calculate Comparison

Clicking **5. Calculate Comparison** performs a point-to-point distance analysis between the planning STL and the registered postoperative position.

The result is displayed as a colour-coded distance model. Distances are stored as signed values (from planned to postoperative). The colour scale ranges from cool (small distance) to warm (larger distance).

![](Screenshots/05_distance_model.png)

---

# Step 6: Output Results

Clicking **Output Results** computes all remaining metrics and writes the results to a CSV file in the directory of the PSI STL file. The filename is `output_<name of PSI model>.csv`.

## Output values

| Column name | Meaning |
|-------------|---------|
| `*_rms_plan_to_preop` | RMS distance (mm) of the planning STL → preop CT registration |
| `*_rms_plan_to_postop` | RMS distance (mm) of the planning STL → postop PSI registration |
| `*_dice_plan_intraop` | Dice coefficient (overlap measure, 0–1) |
| `*_hausdorff_avg_planned_postop` | Average Hausdorff distance (mm) |
| `*_hausdorff_max_planned_postop` | Maximum Hausdorff distance (mm) |
| `*_rotation_x/y/z` | Rotational error around x, y, z axis (degrees, Euler XYZ) |
| `*_distance` | Euclidean distance between bounding-box centres (mm) |
| `*_vector_x/y/z` | Displacement vector between bounding-box centres (mm) |
| `*_m2m_rms` | RMS of signed point-to-point distances (mm) |
| `registration_ncc` | Normalized Cross-Correlation of the CT registration (−1 to 1) |

> The CSV file can be imported directly into MS Excel or LibreOffice Calc. Copy the result row into the master table collecting results for all cases.

![](Screenshots/06_output_results.png)

---

# Tips and Notes

- **Multiple PSIs per case:** For each PSI, return to step 3, select a different planning model, and repeat the workflow from there. Results are saved with the respective model name as a prefix.
- **Check the coordinate system:** If the imported STL appears far from the CT volumes, step 2.a is almost certainly required.
- **Manual fine-tuning after step 4:** The transform handles shown after *Align PSIs* can be used for manual corrections. Important: all corrections must be complete before step 5 (*Calculate Comparison*) is executed, since that step hardens the transform.
- **Segmentation quality:** The accuracy of the segmentation in step 3 has a significant impact on the registration result in step 4. It is worth investing time in a clean segmentation.
- **Registration mask (step 2):** Particularly useful when the preoperative and postoperative CTs were acquired with the patient in different positions (e.g. supine vs. sitting). The mask can restrict the registration to the relevant anatomical region.
- **Save the scene:** After completing the evaluation, save the Slicer scene (.mrb) in the case directory.

---

# Troubleshooting

| Problem | Possible cause | Solution |
|---------|---------------|---------|
| PSI STL appears far from the CT | STL in manufacturer-specific coordinate system | Perform step 2.a |
| RMS after step 2.a > 0.5 mm | Manual pre-alignment insufficient | Repeat *Recenter* and improve pre-alignment, then register again |
| Threshold in step 3 segments too much bone | Lower HU limit too low | Raise threshold lower bound to ≥ 1750 HU or clean up manually with Scissors |
| Threshold does not fully capture PSI | PSI made of low-attenuation material (titanium: ~2000 HU, PEEK: ~400 HU) | Adjust threshold according to material |
| Align PSIs converges to wrong position | Planning STL not yet transferred into preop coordinate system | Run step 3.a (*Align PSI to preop CT*) before segmentation |
| RMS after step 4 > 0.5 mm | PSI deformed or segmentation inaccurate | Visually check the result; if needed, improve segmentation in step 3 and repeat |
| No CSV output in step 6 | PSI STL file has no file path (e.g. imported without a save location) | Save the PSI model as an STL file on disk before running the evaluation |
| Rotation values unexpectedly large | Transform from step 4 contains a residual pre-transform | Ensure the transform was not manually overwritten before step 5 |
