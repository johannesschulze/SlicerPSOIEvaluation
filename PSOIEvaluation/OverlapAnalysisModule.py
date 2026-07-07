"""
OverlapAnalysisModule
=====================
3D Slicer scripted module for fragment/implant overlap QC.

Step 1 – Prepare:
  Select a reference CT volume and two model nodes, then click "Prepare" to
  import both models as segments, clean each with "Keep largest island", and
  build an "Overlap" segment as their intersection.  The segmentation is
  displayed in the slice views and the 3D viewer; everything else is hidden.

Step 2 – Review (optional):
  Click "Open Segment Editor" to inspect or correct the three segments.

Step 3 – Analyze:
  Click "Run analysis" to compute gap_mm, overlap volume, per-axis extents, and
  maximum overlap depth.  Results are written to the table node
  "FragmentMandibleOverlapQC_Result".
"""

import numpy as np
import vtk
from vtk.util import numpy_support as vtk_np

import qt
import ctk
import slicer
from slicer.i18n import tr as _
from slicer.i18n import translate
from slicer.ScriptedLoadableModule import (
    ScriptedLoadableModule,
    ScriptedLoadableModuleLogic,
    ScriptedLoadableModuleWidget,
    ScriptedLoadableModuleTest,
)
from slicer.util import VTKObservationMixin
from slicer.parameterNodeWrapper import parameterNodeWrapper
from slicer import vtkMRMLScalarVolumeNode, vtkMRMLModelNode

SEGMENTATION_NODE_NAME = "FragmentMandibleOverlapQC"
OVERLAP_SEGMENT_NAME   = "Overlap"
RESULT_TABLE_NAME      = "FragmentMandibleOverlapQC_Result"


# ──────────────────────────────────────────────────────────────────────────────
# Parameter node
# ──────────────────────────────────────────────────────────────────────────────

@parameterNodeWrapper
class OverlapAnalysisModuleParameterNode:
    referenceVolume : vtkMRMLScalarVolumeNode
    modelA          : vtkMRMLModelNode
    modelB          : vtkMRMLModelNode


# ──────────────────────────────────────────────────────────────────────────────
# Module class
# ──────────────────────────────────────────────────────────────────────────────

class OverlapAnalysisModule(ScriptedLoadableModule):

    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title       = _("Overlap Analysis")
        self.parent.categories  = [translate("qSlicerAbstractCoreModule", "PSOI Evaluation")]
        self.parent.dependencies = ["SegmentEditor"]
        self.parent.contributors = ["Johannes Schulze (Bundeswehrkrankenhaus Ulm)"]
        self.parent.helpText    = _(
            "Fragment / implant overlap QC: select a reference volume and two models, "
            "prepare the QC segmentation, review it in the Segment Editor, then run the "
            "analysis to compute gap and overlap metrics."
        )
        self.parent.acknowledgementText = ""


# ──────────────────────────────────────────────────────────────────────────────
# Widget
# ──────────────────────────────────────────────────────────────────────────────

class OverlapAnalysisModuleWidget(ScriptedLoadableModuleWidget, VTKObservationMixin):

    def __init__(self, parent=None):
        ScriptedLoadableModuleWidget.__init__(self, parent)
        VTKObservationMixin.__init__(self)
        self.logic = None
        self._parameterNode = None

    def setup(self):
        ScriptedLoadableModuleWidget.setup(self)
        self.logic = OverlapAnalysisModuleLogic()

        # ── Inputs ────────────────────────────────────────────────────────
        inputsGroup = ctk.ctkCollapsibleButton()
        inputsGroup.text = "Inputs"
        self.layout.addWidget(inputsGroup)
        inputsLayout = qt.QFormLayout(inputsGroup)

        self.referenceVolumeSelector = slicer.qMRMLNodeComboBox()
        self.referenceVolumeSelector.nodeTypes = ["vtkMRMLScalarVolumeNode"]
        self.referenceVolumeSelector.addEnabled    = False
        self.referenceVolumeSelector.removeEnabled = False
        self.referenceVolumeSelector.noneEnabled   = True
        self.referenceVolumeSelector.showHidden    = False
        self.referenceVolumeSelector.setMRMLScene(slicer.mrmlScene)
        self.referenceVolumeSelector.setToolTip(
            "CT or other scalar volume used as the voxel grid for the QC segmentation."
        )
        inputsLayout.addRow("Reference volume:", self.referenceVolumeSelector)

        self.modelASelector = slicer.qMRMLNodeComboBox()
        self.modelASelector.nodeTypes = ["vtkMRMLModelNode"]
        self.modelASelector.addEnabled    = False
        self.modelASelector.removeEnabled = False
        self.modelASelector.noneEnabled   = True
        self.modelASelector.showHidden    = False
        self.modelASelector.setMRMLScene(slicer.mrmlScene)
        inputsLayout.addRow("Model A:", self.modelASelector)

        self.modelBSelector = slicer.qMRMLNodeComboBox()
        self.modelBSelector.nodeTypes = ["vtkMRMLModelNode"]
        self.modelBSelector.addEnabled    = False
        self.modelBSelector.removeEnabled = False
        self.modelBSelector.noneEnabled   = True
        self.modelBSelector.showHidden    = False
        self.modelBSelector.setMRMLScene(slicer.mrmlScene)
        inputsLayout.addRow("Model B:", self.modelBSelector)

        # ── Actions ───────────────────────────────────────────────────────
        actionsGroup = ctk.ctkCollapsibleButton()
        actionsGroup.text = "Actions"
        self.layout.addWidget(actionsGroup)
        actionsLayout = qt.QVBoxLayout(actionsGroup)

        self.prepareButton = qt.QPushButton("1.  Prepare segmentation")
        self.prepareButton.toolTip = (
            "Import both models as segments, keep the largest island in each, "
            "and create the Overlap segment as their intersection."
        )
        actionsLayout.addWidget(self.prepareButton)

        self.openSegmentEditorButton = qt.QPushButton("Open Segment Editor …")
        self.openSegmentEditorButton.toolTip = (
            "Switch to the Segment Editor to review or correct the three segments."
        )
        actionsLayout.addWidget(self.openSegmentEditorButton)

        self.analyzeButton = qt.QPushButton("2.  Run analysis")
        self.analyzeButton.toolTip = (
            "Compute gap_mm, overlap volume, per-axis extents, and maximum overlap depth."
        )
        actionsLayout.addWidget(self.analyzeButton)

        self.layout.addStretch(1)

        # ── Connections ───────────────────────────────────────────────────
        self.referenceVolumeSelector.connect("currentNodeChanged(vtkMRMLNode*)", self._onReferenceVolumeChanged)
        self.modelASelector.connect("currentNodeChanged(vtkMRMLNode*)", self._onModelAChanged)
        self.modelBSelector.connect("currentNodeChanged(vtkMRMLNode*)", self._onModelBChanged)
        self.prepareButton.connect("clicked(bool)", self.onPrepareClicked)
        self.openSegmentEditorButton.connect("clicked(bool)", self.onOpenSegmentEditor)
        self.analyzeButton.connect("clicked(bool)", self.onAnalyzeClicked)

        self.initializeParameterNode()

    def enter(self):
        self.initializeParameterNode()

    def exit(self):
        if self._parameterNode is not None:
            self.removeObserver(
                self._parameterNode, vtk.vtkCommand.ModifiedEvent,
                self._onParameterNodeModified
            )

    def initializeParameterNode(self):
        self._setParameterNode(self.logic.getParameterNode())

    def _setParameterNode(self, pn):
        if self._parameterNode is not None:
            self.removeObserver(
                self._parameterNode, vtk.vtkCommand.ModifiedEvent,
                self._onParameterNodeModified
            )
        self._parameterNode = pn
        if self._parameterNode is not None:
            self.addObserver(
                self._parameterNode, vtk.vtkCommand.ModifiedEvent,
                self._onParameterNodeModified
            )
            self._updateGUIFromParameterNode()

    def _onParameterNodeModified(self, *_):
        self._updateGUIFromParameterNode()

    def _updateGUIFromParameterNode(self):
        if self._parameterNode is None:
            return
        pn = self._parameterNode
        for selector, node in (
            (self.referenceVolumeSelector, pn.referenceVolume),
            (self.modelASelector,          pn.modelA),
            (self.modelBSelector,          pn.modelB),
        ):
            selector.blockSignals(True)
            selector.setCurrentNode(node)
            selector.blockSignals(False)

    def _onReferenceVolumeChanged(self, node):
        if self._parameterNode is not None:
            self._parameterNode.referenceVolume = node

    def _onModelAChanged(self, node):
        if self._parameterNode is not None:
            self._parameterNode.modelA = node

    def _onModelBChanged(self, node):
        if self._parameterNode is not None:
            self._parameterNode.modelB = node

    def onPrepareClicked(self):
        if self._parameterNode is None:
            return
        referenceVolume = self._parameterNode.referenceVolume
        modelA          = self._parameterNode.modelA
        modelB          = self._parameterNode.modelB

        if referenceVolume is None or modelA is None or modelB is None:
            slicer.util.errorDisplay("Please select a reference volume and both models.")
            return
        if modelA is modelB:
            slicer.util.errorDisplay("Model A and Model B must be different nodes.")
            return

        with slicer.util.tryWithErrorDisplay(_("Preparation failed."), waitCursor=True):
            segNode = self.logic.prepareSegmentation(referenceVolume, modelA, modelB)
            self._setupViews(referenceVolume, segNode)

    def _setupViews(self, referenceVolume, segNode):
        # Hide all regular model and segmentation nodes except our new one
        for cls in ("vtkMRMLModelNode", "vtkMRMLSegmentationNode"):
            for i in range(slicer.mrmlScene.GetNumberOfNodesByClass(cls)):
                node = slicer.mrmlScene.GetNthNodeByClass(i, cls)
                if node is segNode:
                    continue
                dn = node.GetDisplayNode()
                if dn:
                    dn.SetVisibility(False)

        # Ensure our segmentation is fully visible
        dispNode = segNode.GetDisplayNode()
        dispNode.SetVisibility(True)
        dispNode.SetVisibility3D(True)
        dispNode.SetVisibility2DFill(True)
        dispNode.SetVisibility2DOutline(True)

        # Reference volume as background, no foreground
        slicer.util.setSliceViewerLayers(background=referenceVolume, foreground=None)
        slicer.util.resetSliceViews()

        # Fit 3D view to the segmentation
        threeDView = slicer.app.layoutManager().threeDWidget(0).threeDView()
        threeDView.resetFocalPoint()

    def onOpenSegmentEditor(self):
        slicer.util.selectModule("SegmentEditor")
        segNode = slicer.mrmlScene.GetFirstNodeByName(SEGMENTATION_NODE_NAME)
        if segNode is not None:
            try:
                slicer.modules.SegmentEditorWidget.editor.setSegmentationNode(segNode)
            except Exception:
                pass

    def onAnalyzeClicked(self):
        with slicer.util.tryWithErrorDisplay(_("Analysis failed."), waitCursor=True):
            self.logic.runAnalysis()


# ──────────────────────────────────────────────────────────────────────────────
# Logic
# ──────────────────────────────────────────────────────────────────────────────

class OverlapAnalysisModuleLogic(ScriptedLoadableModuleLogic):

    def getParameterNode(self):
        return OverlapAnalysisModuleParameterNode(super().getParameterNode())

    # ── Preparation ───────────────────────────────────────────────────────────

    def prepareSegmentation(self, referenceVolumeNode, modelNodeA, modelNodeB):
        """
        Creates (or resets) the QC segmentation, imports both models, cleans
        each with "Keep largest island", and builds the Overlap segment.
        Returns the segmentation node.
        """
        segNode = slicer.mrmlScene.GetFirstNodeByName(SEGMENTATION_NODE_NAME)
        if segNode is None:
            segNode = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLSegmentationNode", SEGMENTATION_NODE_NAME
            )
            segNode.CreateDefaultDisplayNodes()
        else:
            segNode.GetSegmentation().RemoveAllSegments()

        segNode.SetReferenceImageGeometryParameterFromVolumeNode(referenceVolumeNode)

        logic = slicer.modules.segmentations.logic()
        logic.ImportModelToSegmentationNode(modelNodeA, segNode)
        logic.ImportModelToSegmentationNode(modelNodeB, segNode)

        segmentation = segNode.GetSegmentation()
        ids = vtk.vtkStringArray()
        segmentation.GetSegmentIDs(ids)
        allIds = [ids.GetValue(i) for i in range(ids.GetNumberOfValues())]
        if len(allIds) != 2:
            raise RuntimeError(
                f"Expected exactly 2 segments after import, got {len(allIds)}."
            )
        segIdA, segIdB = allIds

        segEditorWidget = slicer.modules.segmenteditor.widgetRepresentation().self().editor
        segEditorWidget.setSegmentationNode(segNode)
        segEditorWidget.setSourceVolumeNode(referenceVolumeNode)
        segEditorNode = segEditorWidget.mrmlSegmentEditorNode()

        for segId in (segIdA, segIdB):
            self._keepLargestIsland(segEditorWidget, segEditorNode, segId)

        # Build Overlap = intersection(A, B)
        overlapId = segmentation.AddEmptySegment(OVERLAP_SEGMENT_NAME)
        segmentation.GetSegment(overlapId).SetName(OVERLAP_SEGMENT_NAME)

        segEditorNode.SetSelectedSegmentID(overlapId)
        segEditorWidget.setActiveEffectByName("Logical operators")
        effect = segEditorWidget.activeEffect()
        effect.parameterSetNode().SetMaskMode(
            slicer.vtkMRMLSegmentationNode.EditAllowedEverywhere
        )
        effect.parameterSetNode().SetOverwriteMode(
            slicer.vtkMRMLSegmentEditorNode.OverwriteNone
        )
        effect.setParameter("Operation", "COPY")
        effect.setParameter("ModifierSegmentID", segIdA)
        effect.self().onApply()
        effect.setParameter("Operation", "INTERSECT")
        effect.setParameter("ModifierSegmentID", segIdB)
        effect.self().onApply()
        segEditorWidget.setActiveEffectByName("Null")

        print(
            f"Prepared '{SEGMENTATION_NODE_NAME}': "
            f"'{segmentation.GetSegment(segIdA).GetName()}', "
            f"'{segmentation.GetSegment(segIdB).GetName()}', "
            f"'{OVERLAP_SEGMENT_NAME}'."
        )
        return segNode

    def _keepLargestIsland(self, segEditorWidget, segEditorNode, segId):
        segEditorNode.SetSelectedSegmentID(segId)
        segEditorWidget.setActiveEffectByName("Islands")
        effect = segEditorWidget.activeEffect()
        effect.setParameter("Operation", "KEEP_LARGEST_ISLAND")
        effect.parameterSetNode().SetMaskMode(
            slicer.vtkMRMLSegmentationNode.EditAllowedEverywhere
        )
        # OverwriteNone prevents the effect from erasing overlapping content
        # in the other segment (which is the default Segment Editor behaviour
        # and would silently destroy any genuine overlap before we measure it).
        effect.parameterSetNode().SetOverwriteMode(
            slicer.vtkMRMLSegmentEditorNode.OverwriteNone
        )
        effect.self().onApply()

    # ── Analysis ──────────────────────────────────────────────────────────────

    def runAnalysis(self):
        segNode = slicer.mrmlScene.GetFirstNodeByName(SEGMENTATION_NODE_NAME)
        if segNode is None:
            raise RuntimeError(
                f"Segmentation '{SEGMENTATION_NODE_NAME}' not found. Run Prepare first."
            )

        segmentation = segNode.GetSegmentation()
        ids = vtk.vtkStringArray()
        segmentation.GetSegmentIDs(ids)
        allIds = [ids.GetValue(i) for i in range(ids.GetNumberOfValues())]

        overlapSegId = None
        otherIds = []
        for segId in allIds:
            if segmentation.GetSegment(segId).GetName() == OVERLAP_SEGMENT_NAME:
                overlapSegId = segId
            else:
                otherIds.append(segId)

        if overlapSegId is None:
            raise RuntimeError(f"No segment named '{OVERLAP_SEGMENT_NAME}' found.")
        if len(otherIds) != 2:
            names = [segmentation.GetSegment(s).GetName() for s in otherIds]
            raise RuntimeError(
                f"Expected exactly 2 non-overlap segments, found {len(otherIds)}: {names}"
            )

        segIdA, segIdB = otherIds
        nameA = segmentation.GetSegment(segIdA).GetName()
        nameB = segmentation.GetSegment(segIdB).GetName()

        polyA = self._getClosedSurface(segNode, segIdA)
        polyB = self._getClosedSurface(segNode, segIdB)
        points, signed = self._computeSignedDistances(polyA, polyB)

        spacing_mm = self._getReferenceSpacing()
        tolerance_mm = spacing_mm / 2.0 if spacing_mm else 0.0
        gap_mm = self._computeGap(signed, tolerance_mm)

        overlapLabelmap = self._getBinaryLabelmap(segNode, overlapSegId)
        labeledImage, n_regions = self._labelConnectedRegions(overlapLabelmap)
        depth_all_mm, depth_per_region = self._computeOverlapDepths(
            points, signed, labeledImage, overlapLabelmap, n_regions
        )

        print(f"\nSegments: '{nameA}'  vs  '{nameB}'")
        print(f"  gap_mm = {gap_mm:.4f}  (discretisation tolerance {tolerance_mm:.4f} mm)")
        print(f"  Overlap segment: {n_regions} connected region(s)")

        region_rows = []
        total_volume = 0.0
        for lv in range(1, n_regions + 1):
            _, volume_mm3, ex, ey, ez = self._regionStats(labeledImage, lv, overlapLabelmap)
            total_volume += volume_mm3
            depth_mm = depth_per_region[lv]
            region_rows.append({
                "row": f"region_{lv}",
                "volume_mm3": volume_mm3,
                "extent_x_mm": ex, "extent_y_mm": ey, "extent_z_mm": ez,
                "overlap_depth_max_mm": depth_mm,
            })
            print(
                f"    region_{lv}:  vol={volume_mm3:.2f} mm³  "
                f"ext=({ex:.2f}, {ey:.2f}, {ez:.2f}) mm  "
                f"depth={depth_mm:.4f} mm"
            )

        if n_regions == 0:
            summary = {
                "row": "ALL", "volume_mm3": 0.0,
                "extent_x_mm": 0.0, "extent_y_mm": 0.0, "extent_z_mm": 0.0,
                "overlap_depth_max_mm": 0.0,
            }
        else:
            arr = vtk_np.vtk_to_numpy(labeledImage.GetPointData().GetScalars())
            dims = labeledImage.GetDimensions()
            arr3d = arr.reshape(dims[2], dims[1], dims[0])
            zs, ys, xs = np.nonzero(arr3d > 0)
            extent = overlapLabelmap.GetExtent()
            extent_start = np.array([extent[0], extent[2], extent[4]])
            ijk_pts = np.stack([xs, ys, zs], axis=1).astype(float) + extent_start
            rs, tr = self._ijkToWorldTransform(overlapLabelmap)
            wpts = ijk_pts @ rs.T + tr
            summary = {
                "row": "ALL (union bbox)",
                "volume_mm3": total_volume,
                "extent_x_mm": float(wpts[:, 0].max() - wpts[:, 0].min()),
                "extent_y_mm": float(wpts[:, 1].max() - wpts[:, 1].min()),
                "extent_z_mm": float(wpts[:, 2].max() - wpts[:, 2].min()),
                "overlap_depth_max_mm": depth_all_mm,
            }

        print(
            f"    ALL (union bbox):  vol={summary['volume_mm3']:.2f} mm³  "
            f"ext=({summary['extent_x_mm']:.2f}, {summary['extent_y_mm']:.2f}, "
            f"{summary['extent_z_mm']:.2f}) mm  "
            f"depth={summary['overlap_depth_max_mm']:.4f} mm"
        )

        self._writeResultTable(gap_mm, region_rows, summary)
        print(f"Results written to table node '{RESULT_TABLE_NAME}'.\n")

    # ── Analysis helpers ──────────────────────────────────────────────────────

    def _getClosedSurface(self, segNode, segId):
        segNode.CreateClosedSurfaceRepresentation()
        poly = vtk.vtkPolyData()
        slicer.vtkSlicerSegmentationsModuleLogic.GetSegmentClosedSurfaceRepresentation(
            segNode, segId, poly
        )
        return poly

    def _getBinaryLabelmap(self, segNode, segId):
        segNode.CreateBinaryLabelmapRepresentation()
        img = slicer.vtkOrientedImageData()
        slicer.vtkSlicerSegmentationsModuleLogic.GetSegmentBinaryLabelmapRepresentation(
            segNode, segId, img
        )
        return img

    def _vtkMatToNumpy(self, mat):
        return np.array([[mat.GetElement(r, c) for c in range(4)] for r in range(4)])

    def _ijkToWorldTransform(self, orientedImg):
        mat = vtk.vtkMatrix4x4()
        orientedImg.GetImageToWorldMatrix(mat)
        m = self._vtkMatToNumpy(mat)
        return m[:3, :3], m[:3, 3]

    def _worldToIjkTransform(self, orientedImg):
        mat = vtk.vtkMatrix4x4()
        orientedImg.GetWorldToImageMatrix(mat)
        m = self._vtkMatToNumpy(mat)
        return m[:3, :3], m[:3, 3]

    def _computeSignedDistances(self, polyA, polyB):
        impl = vtk.vtkImplicitPolyDataDistance()
        impl.SetInput(polyB)
        pts = vtk_np.vtk_to_numpy(polyA.GetPoints().GetData())
        signed = np.array([impl.EvaluateFunction(p) for p in pts])
        return pts, signed

    def _getReferenceSpacing(self):
        nodes = slicer.util.getNodesByClass("vtkMRMLScalarVolumeNode")
        return max(nodes[0].GetSpacing()) if nodes else None

    def _computeGap(self, signed, tolerance_mm):
        min_s = float(np.min(signed))
        if abs(min_s) < tolerance_mm or min_s < 0:
            return 0.0
        return min_s

    def _labelConnectedRegions(self, labelmap):
        arr = vtk_np.vtk_to_numpy(labelmap.GetPointData().GetScalars())
        binary = vtk.vtkImageData()
        binary.CopyStructure(labelmap)
        binary.AllocateScalars(vtk.VTK_UNSIGNED_CHAR, 1)
        vtk_np.vtk_to_numpy(binary.GetPointData().GetScalars())[:] = (arr != 0).astype(np.uint8)
        conn = vtk.vtkImageConnectivityFilter()
        conn.SetInputData(binary)
        conn.SetExtractionModeToAllRegions()
        conn.SetLabelModeToSizeRank()
        conn.SetScalarRange(1, 1)
        conn.Update()
        return conn.GetOutput(), conn.GetNumberOfExtractedRegions()

    def _computeOverlapDepths(self, points, signed, labeledImage, referenceOrientedImage, n_regions):
        """
        Returns (depth_all_mm, {label_value: depth_mm}).
        depth_all_mm is the deepest embedded point across all of segment A.
        depth_per_region is broken down by connected component of the Overlap segment.
        """
        depth_all_mm = max(0.0, float(-np.min(signed)))

        dims = labeledImage.GetDimensions()
        labelArr = vtk_np.vtk_to_numpy(labeledImage.GetPointData().GetScalars())
        labelArr3d = labelArr.reshape(dims[2], dims[1], dims[0])

        rs, tr = self._worldToIjkTransform(referenceOrientedImage)
        extent = referenceOrientedImage.GetExtent()
        extent_start = np.array([extent[0], extent[2], extent[4]])

        embedded_indices = np.nonzero(signed < 0)[0]
        point_labels = np.zeros(len(points), dtype=int)

        if len(embedded_indices) > 0:
            ijk = np.round(
                points[embedded_indices] @ rs.T + tr - extent_start
            ).astype(int)
            ijk[:, 0] = np.clip(ijk[:, 0], 0, dims[0] - 1)
            ijk[:, 1] = np.clip(ijk[:, 1], 0, dims[1] - 1)
            ijk[:, 2] = np.clip(ijk[:, 2], 0, dims[2] - 1)
            for local_idx, global_idx in enumerate(embedded_indices):
                point_labels[global_idx] = self._nearestNonzeroLabel(
                    labelArr3d, dims, ijk[local_idx]
                )

        depth_per_region = {}
        for lv in range(1, n_regions + 1):
            mask = point_labels == lv
            depth_per_region[lv] = (
                max(0.0, float(-np.min(signed[mask]))) if np.any(mask) else 0.0
            )

        return depth_all_mm, depth_per_region

    def _nearestNonzeroLabel(self, arr3d, dims, ijk_point, max_radius=3):
        i, j, k = ijk_point
        if arr3d[k, j, i] != 0:
            return int(arr3d[k, j, i])
        for r in range(1, max_radius + 1):
            neighborhood = arr3d[
                max(0, k-r):min(dims[2], k+r+1),
                max(0, j-r):min(dims[1], j+r+1),
                max(0, i-r):min(dims[0], i+r+1),
            ]
            nonzero = neighborhood[neighborhood != 0]
            if nonzero.size > 0:
                values, counts = np.unique(nonzero, return_counts=True)
                return int(values[np.argmax(counts)])
        return 0

    def _regionStats(self, labeledImage, label_value, referenceOrientedImage):
        dims = labeledImage.GetDimensions()
        spacing = referenceOrientedImage.GetSpacing()
        arr = vtk_np.vtk_to_numpy(labeledImage.GetPointData().GetScalars())
        arr3d = arr.reshape(dims[2], dims[1], dims[0])
        mask = arr3d == label_value
        voxel_count = int(np.count_nonzero(mask))
        volume_mm3  = voxel_count * float(np.prod(spacing))
        zs, ys, xs  = np.nonzero(mask)
        extent = referenceOrientedImage.GetExtent()
        extent_start = np.array([extent[0], extent[2], extent[4]])
        ijk_pts = np.stack([xs, ys, zs], axis=1).astype(float) + extent_start
        rs, tr  = self._ijkToWorldTransform(referenceOrientedImage)
        wpts    = ijk_pts @ rs.T + tr
        ex = float(wpts[:, 0].max() - wpts[:, 0].min())
        ey = float(wpts[:, 1].max() - wpts[:, 1].min())
        ez = float(wpts[:, 2].max() - wpts[:, 2].min())
        return voxel_count, volume_mm3, ex, ey, ez

    def _writeResultTable(self, gap_mm, region_rows, summary_row):
        tableNode = slicer.mrmlScene.GetFirstNodeByName(RESULT_TABLE_NAME)
        if tableNode is None:
            tableNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLTableNode", RESULT_TABLE_NAME)
        tableNode.RemoveAllColumns()

        col_names = [
            "row", "volume_mm3",
            "extent_x_mm", "extent_y_mm", "extent_z_mm",
            "overlap_depth_max_mm", "gap_mm",
        ]
        cols = {}
        for name in col_names:
            col = vtk.vtkStringArray() if name == "row" else vtk.vtkDoubleArray()
            col.SetName(name)
            tableNode.AddColumn(col)
            cols[name] = col

        all_rows = region_rows + [summary_row]
        tableNode.GetTable().SetNumberOfRows(len(all_rows) + 1)  # +1 for GAP row

        for i, row in enumerate(all_rows):
            cols["row"].SetValue(i, row["row"])
            cols["volume_mm3"].SetValue(i, row["volume_mm3"])
            cols["extent_x_mm"].SetValue(i, row["extent_x_mm"])
            cols["extent_y_mm"].SetValue(i, row["extent_y_mm"])
            cols["extent_z_mm"].SetValue(i, row["extent_z_mm"])
            cols["overlap_depth_max_mm"].SetValue(i, row["overlap_depth_max_mm"])
            cols["gap_mm"].SetValue(i, float("nan"))

        gap_idx = len(all_rows)
        cols["row"].SetValue(gap_idx, "GAP")
        for name in ("volume_mm3", "extent_x_mm", "extent_y_mm", "extent_z_mm",
                     "overlap_depth_max_mm"):
            cols[name].SetValue(gap_idx, float("nan"))
        cols["gap_mm"].SetValue(gap_idx, gap_mm)

        tableNode.Modified()

        try:
            slicer.app.applicationLogic().GetSelectionNode().SetActiveTableID(tableNode.GetID())
            slicer.app.applicationLogic().PropagateTableSelection()
            slicer.app.layoutManager().addMaximizedViewNode(slicer.app.layoutManager().activeMRMLTableViewNode())
        except AttributeError:
            print(
                f"(Could not switch to a table view automatically — "
                f"open the Tables module to inspect '{RESULT_TABLE_NAME}'.)"
            )

        return tableNode
