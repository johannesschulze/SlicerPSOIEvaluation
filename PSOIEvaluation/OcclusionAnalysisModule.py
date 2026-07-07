"""
OcclusionAnalysisModule
=======================
3D Slicer scripted module for computing the occlusal change vector
(OCA, OCN, OAS) from paired MIP intraoral scan meshes.

Supports an arbitrary number of timepoints (T0 = immediately postop,
T1 = 6 months, T2 = 12 months, …). Each timepoint is one upper+lower
pair already in MIP relation — no inter-timepoint registration required.

Primary contact threshold τ = 0.05 mm (Liu et al. 2020).
Sensitivity analysis across τ ∈ {0.03, 0.05, 0.08} mm.

Distance computation uses vtkDistancePolyDataFilter (C++ loop) rather
than calling vtkImplicitPolyDataDistance point-by-point from Python,
giving ~50× speedup on dense IOS meshes.
"""

import numpy as np
import vtk
from vtk.util.numpy_support import vtk_to_numpy, numpy_to_vtk

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
from slicer.parameterNodeWrapper import parameterNodeWrapper
from slicer.util import VTKObservationMixin

RESULT_VECTORS_TABLE = "OcclusionVectors"
RESULT_DELTAS_TABLE  = "OcclusionDeltas"
SENSITIVITY_TAUS     = [0.03, 0.05, 0.08]
DEFAULT_TAU          = 0.05
DEFAULT_N_SECTORS    = 6
DEFAULT_MIN_AREA     = 0.1   # mm²

# Occlusion map display settings (match "Model to Model Distance" defaults used in QC)
OCCMAP_SCALAR_RANGE = (0.0, 0.1)   # mm  – display range mapped to color
OCCMAP_THRESHOLD    = (-0.2, 0.2)  # mm  – hide points outside this range
OCCMAP_Z_OFFSET     = 0.1          # mm  – shift along Z to avoid z-fighting


# ──────────────────────────────────────────────────────────────────────────────
# Timepoint persistence (one vtkMRMLScriptedModuleNode per timepoint)
# ──────────────────────────────────────────────────────────────────────────────

class OcclusionTimepoint:
    """Per-timepoint state stored as a vtkMRMLScriptedModuleNode.
    Persists with the .mrb scene file."""

    _TAG   = "OcclusionAnalysis.isTimepoint"
    _ORDER = "OcclusionAnalysis.order"
    _LABEL = "OcclusionAnalysis.label"
    _UPPER = "OcclusionAnalysis.upperModelID"
    _LOWER = "OcclusionAnalysis.lowerModelID"

    def __init__(self, node):
        self._node = node

    @classmethod
    def create(cls, label, order):
        node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLScriptedModuleNode")
        node.SetName(f"OcclusionTimepoint-{label}")
        node.SetAttribute(cls._TAG,   "true")
        node.SetAttribute(cls._ORDER, str(order))
        node.SetAttribute(cls._LABEL, label)
        return cls(node)

    @classmethod
    def findAll(cls):
        result = []
        for i in range(slicer.mrmlScene.GetNumberOfNodesByClass("vtkMRMLScriptedModuleNode")):
            node = slicer.mrmlScene.GetNthNodeByClass(i, "vtkMRMLScriptedModuleNode")
            if node.GetAttribute(cls._TAG) == "true":
                result.append(cls(node))
        result.sort(key=lambda t: int(t._node.GetAttribute(cls._ORDER) or 0))
        return result

    @property
    def nodeId(self):
        return self._node.GetID()

    @property
    def label(self):
        return self._node.GetAttribute(self._LABEL) or ""

    @label.setter
    def label(self, v):
        self._node.SetAttribute(self._LABEL, v)
        self._node.SetName(f"OcclusionTimepoint-{v}")

    @property
    def order(self):
        return int(self._node.GetAttribute(self._ORDER) or 0)

    @order.setter
    def order(self, v):
        self._node.SetAttribute(self._ORDER, str(v))

    def _getModel(self, attr):
        nid = self._node.GetAttribute(attr)
        return slicer.mrmlScene.GetNodeByID(nid) if nid else None

    def _setModel(self, attr, node):
        self._node.SetAttribute(attr, node.GetID() if node else "")

    @property
    def upperModel(self):
        return self._getModel(self._UPPER)

    @upperModel.setter
    def upperModel(self, node):
        self._setModel(self._UPPER, node)

    @property
    def lowerModel(self):
        return self._getModel(self._LOWER)

    @lowerModel.setter
    def lowerModel(self, node):
        self._setModel(self._LOWER, node)

    def remove(self):
        slicer.mrmlScene.RemoveNode(self._node)


# ──────────────────────────────────────────────────────────────────────────────
# Module class
# ──────────────────────────────────────────────────────────────────────────────

class OcclusionAnalysisModule(ScriptedLoadableModule):

    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title       = _("Occlusion Analysis")
        self.parent.categories  = [translate("qSlicerAbstractCoreModule", "PSOI Evaluation")]
        self.parent.dependencies = []
        self.parent.contributors = ["Johannes Schulze (Bundeswehrkrankenhaus Ulm)"]
        self.parent.helpText    = _(
            "Computes the occlusal change vector (OCA, OCN, OAS) from paired MIP "
            "intraoral scan meshes across an arbitrary number of timepoints. "
            "Primary τ = 0.05 mm (Liu et al. 2020); sensitivity across "
            "τ ∈ {0.03, 0.05, 0.08} mm."
        )
        self.parent.acknowledgementText = ""


# ──────────────────────────────────────────────────────────────────────────────
# Parameter node  (analysis settings only; timepoints live in scene nodes)
# ──────────────────────────────────────────────────────────────────────────────

@parameterNodeWrapper
class OcclusionAnalysisModuleParameterNode:
    primaryTau : float = DEFAULT_TAU
    nSectors   : int   = DEFAULT_N_SECTORS
    minArea    : float = DEFAULT_MIN_AREA


# ──────────────────────────────────────────────────────────────────────────────
# Widget
# ──────────────────────────────────────────────────────────────────────────────

class OcclusionAnalysisModuleWidget(ScriptedLoadableModuleWidget, VTKObservationMixin):

    def __init__(self, parent=None):
        ScriptedLoadableModuleWidget.__init__(self, parent)
        VTKObservationMixin.__init__(self)
        self.logic = None
        self._parameterNode = None
        self._timepointRows = []   # list of row-dicts

    def setup(self):
        ScriptedLoadableModuleWidget.setup(self)
        self.logic = OcclusionAnalysisModuleLogic()

        # ── Timepoints ────────────────────────────────────────────────────
        tpGroup = ctk.ctkCollapsibleButton()
        tpGroup.text = "Timepoints"
        self.layout.addWidget(tpGroup)
        tpGroupLayout = qt.QVBoxLayout(tpGroup)

        self._timepointsContainer = qt.QWidget()
        self._timepointsLayout = qt.QVBoxLayout(self._timepointsContainer)
        self._timepointsLayout.setContentsMargins(0, 0, 0, 0)
        self._timepointsLayout.setSpacing(2)
        tpGroupLayout.addWidget(self._timepointsContainer)

        self._addTimepointButton = qt.QPushButton("+ Add timepoint")
        tpGroupLayout.addWidget(self._addTimepointButton)

        # ── Settings ──────────────────────────────────────────────────────
        settingsGroup = ctk.ctkCollapsibleButton()
        settingsGroup.text = "Settings"
        settingsGroup.collapsed = True
        self.layout.addWidget(settingsGroup)
        settingsLayout = qt.QFormLayout(settingsGroup)

        self._primaryTauSpinBox = qt.QDoubleSpinBox()
        self._primaryTauSpinBox.setRange(0.01, 0.5)
        self._primaryTauSpinBox.setSingleStep(0.01)
        self._primaryTauSpinBox.setDecimals(3)
        self._primaryTauSpinBox.setValue(DEFAULT_TAU)
        self._primaryTauSpinBox.setSuffix(" mm")
        self._primaryTauSpinBox.setToolTip(
            "Primary contact threshold τ (fixed a priori; Liu et al. 2020: ≤ 50 µm). "
            "Sensitivity analysis always runs across τ ∈ {0.03, 0.05, 0.08} mm."
        )
        settingsLayout.addRow("Primary threshold τ:", self._primaryTauSpinBox)

        self._nSectorsSpinBox = qt.QSpinBox()
        self._nSectorsSpinBox.setRange(2, 12)
        self._nSectorsSpinBox.setValue(DEFAULT_N_SECTORS)
        self._nSectorsSpinBox.setToolTip(
            "Number of arch sectors for OCN (method B). 6 = sextants."
        )
        settingsLayout.addRow("Sectors OCN (method B):", self._nSectorsSpinBox)

        self._minAreaSpinBox = qt.QDoubleSpinBox()
        self._minAreaSpinBox.setRange(0.0, 10.0)
        self._minAreaSpinBox.setSingleStep(0.05)
        self._minAreaSpinBox.setDecimals(2)
        self._minAreaSpinBox.setValue(DEFAULT_MIN_AREA)
        self._minAreaSpinBox.setSuffix(" mm²")
        self._minAreaSpinBox.setToolTip(
            "Minimum area per sector / cluster to count as an active contact "
            "region (speckle filter)."
        )
        settingsLayout.addRow("Min. contact area:", self._minAreaSpinBox)

        # ── Buttons ───────────────────────────────────────────────────────
        self._mapButton = qt.QPushButton("Create occlusion maps")
        self._mapButton.setStyleSheet("padding: 6px;")
        self._mapButton.setToolTip(
            "For each timepoint: compute lower→upper signed distance, create a "
            "colorized model node (scalar range 0–0.1 mm, threshold ±0.2 mm)."
        )
        self.layout.addWidget(self._mapButton)

        self._runButton = qt.QPushButton("Run analysis")
        self._runButton.setStyleSheet("font-weight: bold; padding: 6px;")
        self.layout.addWidget(self._runButton)

        self.layout.addStretch(1)

        # ── Connections ───────────────────────────────────────────────────
        self._addTimepointButton.connect("clicked(bool)", self._onAddTimepointClicked)
        self._mapButton.connect("clicked(bool)", self._onMapClicked)
        self._runButton.connect("clicked(bool)", self._onRunClicked)
        self._primaryTauSpinBox.connect("valueChanged(double)", self._onSettingChanged)
        self._nSectorsSpinBox.connect("valueChanged(int)", self._onSettingChanged)
        self._minAreaSpinBox.connect("valueChanged(double)", self._onSettingChanged)

        self.addObserver(slicer.mrmlScene, slicer.mrmlScene.EndImportEvent,  self._onSceneEndImport)
        self.addObserver(slicer.mrmlScene, slicer.mrmlScene.EndRestoreEvent, self._onSceneEndImport)

        self.initializeParameterNode()
        self._refreshTimepointRowsFromScene()

    def enter(self):
        self.initializeParameterNode()
        self._refreshTimepointRowsFromScene()

    def exit(self):
        if self._parameterNode is not None:
            self.removeObserver(
                self._parameterNode, vtk.vtkCommand.ModifiedEvent,
                self._onParameterNodeModified
            )

    def _onSceneEndImport(self, *_):
        if self.parent.isEntered:
            self.initializeParameterNode()
            self._refreshTimepointRowsFromScene()

    # ── Parameter node ────────────────────────────────────────────────────

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
            self._updateSettingsFromParameterNode()

    def _onParameterNodeModified(self, *_):
        self._updateSettingsFromParameterNode()

    def _updateSettingsFromParameterNode(self):
        if self._parameterNode is None:
            return
        pn = self._parameterNode
        for widget, value in (
            (self._primaryTauSpinBox, pn.primaryTau),
            (self._nSectorsSpinBox,   pn.nSectors),
            (self._minAreaSpinBox,    pn.minArea),
        ):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)

    def _onSettingChanged(self, *_):
        if self._parameterNode is None:
            return
        self._parameterNode.primaryTau = self._primaryTauSpinBox.value
        self._parameterNode.nSectors   = int(self._nSectorsSpinBox.value)
        self._parameterNode.minArea    = self._minAreaSpinBox.value

    # ── Timepoint rows ────────────────────────────────────────────────────

    def _refreshTimepointRowsFromScene(self):
        for row in self._timepointRows:
            row["widget"].setParent(None)
        self._timepointRows.clear()
        for tp in OcclusionTimepoint.findAll():
            self._buildTimepointRow(tp)

    def _onAddTimepointClicked(self):
        order = len(self._timepointRows)
        tp = OcclusionTimepoint.create(f"T{order}", order)
        self._buildTimepointRow(tp)

    def _buildTimepointRow(self, tp):
        rowWidget = qt.QWidget()
        rowLayout = qt.QHBoxLayout(rowWidget)
        rowLayout.setContentsMargins(0, 1, 0, 1)

        labelEdit = qt.QLineEdit(tp.label)
        labelEdit.setFixedWidth(80)
        labelEdit.setToolTip("Label for this timepoint")

        upperSelector = self._makeModelSelector("Upper arch mesh (MIP)")
        upperSelector.setCurrentNode(tp.upperModel)

        lowerSelector = self._makeModelSelector("Lower arch mesh (MIP)")
        lowerSelector.setCurrentNode(tp.lowerModel)

        removeBtn = qt.QPushButton("✕")
        removeBtn.setFixedWidth(26)
        removeBtn.setToolTip("Remove timepoint")

        rowLayout.addWidget(qt.QLabel("Label:"))
        rowLayout.addWidget(labelEdit)
        rowLayout.addWidget(qt.QLabel("Upper:"))
        rowLayout.addWidget(upperSelector)
        rowLayout.addWidget(qt.QLabel("Lower:"))
        rowLayout.addWidget(lowerSelector)
        rowLayout.addWidget(removeBtn)

        self._timepointsLayout.addWidget(rowWidget)

        row = {
            "widget":        rowWidget,
            "labelEdit":     labelEdit,
            "upperSelector": upperSelector,
            "lowerSelector": lowerSelector,
            "removeBtn":     removeBtn,
            "tp":            tp,
        }
        self._timepointRows.append(row)

        labelEdit.connect(     "textChanged(QString)",         lambda v, r=row: self._onLabelChanged(v, r))
        upperSelector.connect( "currentNodeChanged(vtkMRMLNode*)", lambda n, r=row: self._onUpperChanged(n, r))
        lowerSelector.connect( "currentNodeChanged(vtkMRMLNode*)", lambda n, r=row: self._onLowerChanged(n, r))
        removeBtn.connect(     "clicked(bool)",                lambda _, r=row: self._onRemoveClicked(r))

    def _makeModelSelector(self, tooltip):
        sel = slicer.qMRMLNodeComboBox()
        sel.nodeTypes    = ["vtkMRMLModelNode"]
        sel.addEnabled   = False
        sel.removeEnabled = False
        sel.noneEnabled  = True
        sel.showHidden   = False
        sel.setMRMLScene(slicer.mrmlScene)
        sel.setToolTip(tooltip)
        return sel

    def _onLabelChanged(self, text, row):
        row["tp"].label = text

    def _onUpperChanged(self, node, row):
        row["tp"].upperModel = node

    def _onLowerChanged(self, node, row):
        row["tp"].lowerModel = node

    def _onRemoveClicked(self, row):
        row["tp"].remove()
        row["widget"].setParent(None)
        self._timepointRows.remove(row)
        for i, r in enumerate(self._timepointRows):
            r["tp"].order = i

    # ── Map / Run ─────────────────────────────────────────────────────────

    def _onMapClicked(self):
        timepoints = [r["tp"] for r in self._timepointRows]
        if not timepoints:
            slicer.util.errorDisplay("Add at least one timepoint first.")
            return
        for r in self._timepointRows:
            tp = r["tp"]
            if tp.upperModel is None or tp.lowerModel is None:
                slicer.util.errorDisplay(
                    f"Timepoint '{tp.label}': upper and lower arch mesh must both be selected."
                )
                return
        with slicer.util.tryWithErrorDisplay(_("Occlusion map creation failed."), waitCursor=True):
            self.logic.createOcclusionMaps(timepoints)

    def _onRunClicked(self):
        timepoints = [r["tp"] for r in self._timepointRows]
        if len(timepoints) < 2:
            slicer.util.errorDisplay("At least two timepoints are required.")
            return
        for r in self._timepointRows:
            tp = r["tp"]
            if tp.upperModel is None or tp.lowerModel is None:
                slicer.util.errorDisplay(
                    f"Timepoint '{tp.label}': upper and lower arch mesh must both be selected."
                )
                return

        with slicer.util.tryWithErrorDisplay(_("Analysis failed."), waitCursor=True):
            pn = self._parameterNode
            self.logic.runAnalysis(
                timepoints    = timepoints,
                primaryTau    = pn.primaryTau,
                sensitivityTaus = SENSITIVITY_TAUS,
                nSectors      = int(pn.nSectors),
                minArea       = pn.minArea,
            )


# ──────────────────────────────────────────────────────────────────────────────
# Logic
# ──────────────────────────────────────────────────────────────────────────────

class OcclusionAnalysisModuleLogic(ScriptedLoadableModuleLogic):

    def getParameterNode(self):
        return OcclusionAnalysisModuleParameterNode(super().getParameterNode())

    # ── Occlusion maps ───────────────────────────────────────────────────────

    def createOcclusionMaps(self, timepoints,
                            scalarRange=OCCMAP_SCALAR_RANGE,
                            thresholdRange=OCCMAP_THRESHOLD,
                            zOffset=OCCMAP_Z_OFFSET):
        """
        For each timepoint: create a signed distance map for both the lower
        mesh (source→upper, Z shift +zOffset) and the upper mesh
        (source→lower, Z shift -zOffset so it moves away from the upper jaw).
        """
        print("\n=== Creating occlusion maps ===")
        for tp in timepoints:
            lower_poly = self._getTriangulated(tp.lowerModel)
            upper_poly = self._getTriangulated(tp.upperModel)
            self._createSingleOcclusionMap(
                source_poly=lower_poly,
                target_poly=self._ensureOutwardNormals(upper_poly),
                source_model=tp.lowerModel,
                zOffset=+zOffset,
                scalarRange=scalarRange,
                thresholdRange=thresholdRange,
            )
            self._createSingleOcclusionMap(
                source_poly=upper_poly,
                target_poly=self._ensureOutwardNormals(lower_poly),
                source_model=tp.upperModel,
                zOffset=-zOffset,
                scalarRange=scalarRange,
                thresholdRange=thresholdRange,
            )
        print(f"Done. {len(timepoints) * 2} occlusion map(s) in scene.")

    def _createSingleOcclusionMap(self, source_poly, target_poly, source_model,
                                   zOffset, scalarRange, thresholdRange):
        dist = vtk.vtkDistancePolyDataFilter()
        dist.SetInputData(0, source_poly)
        dist.SetInputData(1, target_poly)
        dist.SignedDistanceOn()
        dist.ComputeSecondDistanceOff()
        dist.Update()

        # Force "Distance" as the active POINT scalar so the display node
        # uses Gouraud (smooth) shading instead of flat per-cell shading.
        distPoly = dist.GetOutput()
        if distPoly.GetPointData().GetArray("Distance") is not None:
            distPoly.GetPointData().SetActiveScalars("Distance")

        xfm = vtk.vtkTransform()
        xfm.Translate(0.0, 0.0, zOffset)
        shifted = vtk.vtkTransformPolyDataFilter()
        shifted.SetInputData(distPoly)
        shifted.SetTransform(xfm)
        shifted.Update()

        nodeName = f"{source_model.GetName()}_distance"
        mapNode = slicer.mrmlScene.GetFirstNodeByName(nodeName)
        if mapNode is None:
            mapNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLModelNode", nodeName)
        mapNode.SetAndObservePolyData(shifted.GetOutput())
        mapNode.CreateDefaultDisplayNodes()

        # Find HotToColdRainbow color node (falls back to Rainbow if missing)
        colorNode = slicer.mrmlScene.GetFirstNodeByName("HotToColdRainbow")
        colorNodeID = colorNode.GetID() if colorNode else "vtkMRMLColorTableNodeRainbow"

        dn = mapNode.GetDisplayNode()
        dn.SetScalarVisibility(True)
        dn.SetActiveScalarName("Distance")
        dn.SetActiveAttributeLocation(0)   # 0 = POINT_DATA → smooth interpolation
        dn.SetScalarRangeFlag(slicer.vtkMRMLDisplayNode.UseManualScalarRange)
        dn.SetScalarRange(*scalarRange)
        dn.SetThresholdEnabled(True)
        dn.SetThresholdRange(*thresholdRange)
        dn.SetAndObserveColorNodeID(colorNodeID)

        # Place next to the source model in the subject hierarchy
        shNode = slicer.vtkMRMLSubjectHierarchyNode.GetSubjectHierarchyNode(slicer.mrmlScene)
        sourceItem = shNode.GetItemByDataNode(source_model)
        mapItem    = shNode.GetItemByDataNode(mapNode)
        shNode.SetItemParent(mapItem, shNode.GetItemParent(sourceItem))

        # Color legend (looked up by name so we can reuse it on re-runs)
        clNode = slicer.mrmlScene.GetFirstNodeByName(f"{nodeName} color legend")
        if clNode is None:
            clNode = slicer.modules.colors.logic().AddDefaultColorLegendDisplayNode(mapNode)
        
        clNode.SetVisibility(True)
        clNode.SetTitleText("")
        clNode.SetLabelFormat("%.2f")
        clNode.SetSize(.08,.8)
        clNode.SetPosition(.9,.5)

        clNode.GetLabelTextProperty().SetColor(0,0,0)
        clNode.GetLabelTextProperty().SetShadow(False)
        clNode.GetLabelTextProperty().SetFontFamilyToArial()

        print(f"  {nodeName}: distance map created (Z offset {zOffset:+.2f} mm).")

    # ── Main entry point ─────────────────────────────────────────────────────

    def runAnalysis(self, timepoints, primaryTau, sensitivityTaus, nSectors, minArea):
        """
        For each timepoint compute OCA/OCN/OAS at every τ, then compute
        signed deltas for all consecutive pairs (Ti → Ti+1) and all pairs
        vs. T0 (baseline).  Writes two result table nodes.
        """
        taus = sorted(set(sensitivityTaus) | {primaryTau})

        print("\n=== Occlusion Analysis ===")

        # Per-timepoint vectors
        vectors = {}   # (tp_index, tau) -> {OCA, OCN, OCN_cluster, OAS}
        for i, tp in enumerate(timepoints):
            upper_poly = self._getTriangulated(tp.upperModel)
            lower_poly = self._getTriangulated(tp.lowerModel)
            print(f"\n[{tp.label}]  Lower: {tp.lowerModel.GetName()}  Upper: {tp.upperModel.GetName()}")
            gap = self._signedGap(lower_poly, upper_poly)
            for tau in taus:
                v = self._occlusionVector(lower_poly, gap, tau, nSectors, minArea)
                vectors[(i, tau)] = v
                print(
                    f"  τ={tau:.3f} mm:  OCA={v['OCA']:.2f} mm²  "
                    f"OCN={v['OCN']} (cluster {v['OCN_cluster']})  OAS={v['OAS']:.4f}"
                )

        # Pairwise deltas: consecutive + all vs T0
        n = len(timepoints)
        pair_set = set()
        for i in range(n - 1):
            pair_set.add((0, i + 1))   # vs baseline
            pair_set.add((i, i + 1))   # consecutive
        pairs = sorted(pair_set)

        delta_rows = []
        print()
        for (i, j) in pairs:
            tpA, tpB  = timepoints[i], timepoints[j]
            label     = f"{tpA.label} → {tpB.label}"
            delta_signs = {"OCA": set(), "OCN": set(), "OAS": set()}
            row_by_tau  = {}

            for tau in taus:
                vA, vB = vectors[(i, tau)], vectors[(j, tau)]
                dOCA   = vB["OCA"] - vA["OCA"]
                dOCN   = vB["OCN"] - vA["OCN"]
                dOAS   = vB["OAS"] - vA["OAS"]
                for key, d in (("OCA", dOCA), ("OCN", dOCN), ("OAS", dOAS)):
                    delta_signs[key].add(_sign(d))
                row_by_tau[tau] = {
                    "comparison": label, "tau": tau,
                    "OCA_A": vA["OCA"], "OCA_B": vB["OCA"], "dOCA": dOCA,
                    "OCN_A": vA["OCN"], "OCN_B": vB["OCN"], "dOCN": dOCN,
                    "OCNcl_A": vA["OCN_cluster"], "OCNcl_B": vB["OCN_cluster"],
                    "OAS_A": vA["OAS"], "OAS_B": vB["OAS"], "dOAS": dOAS,
                }

            robust = {k: len(s - {0}) <= 1 for k, s in delta_signs.items()}
            prim   = row_by_tau[primaryTau]
            improved = (
                all(robust.values())
                and prim["dOCA"] > 0 and prim["dOCN"] > 0 and prim["dOAS"] < 0
            )
            worsened = (
                all(robust.values())
                and prim["dOCA"] < 0 and prim["dOCN"] <= 0 and prim["dOAS"] > 0
            )
            verdict = (
                "improved"      if improved
                else "worsened" if worsened
                else "inconclusive"
            )

            for tau, row in row_by_tau.items():
                row["dOCA_robust"] = robust["OCA"]
                row["dOCN_robust"] = robust["OCN"]
                row["dOAS_robust"] = robust["OAS"]
                row["verdict"]     = verdict
            delta_rows.extend(row_by_tau.values())

            print(
                f"{label}: {verdict}  "
                f"(τ={primaryTau:.3f}: dOCA={prim['dOCA']:+.2f} mm², "
                f"dOCN={prim['dOCN']:+d}, dOAS={prim['dOAS']:+.4f})"
            )

        # Write tables
        vector_rows = [
            {
                "timepoint": timepoints[i].label, "tau": tau,
                "OCA": vectors[(i, tau)]["OCA"],
                "OCN": vectors[(i, tau)]["OCN"],
                "OCN_cluster": vectors[(i, tau)]["OCN_cluster"],
                "OAS": vectors[(i, tau)]["OAS"],
            }
            for i in range(n) for tau in taus
        ]
        self._writeVectorTable(vector_rows)
        self._writeDeltaTable(delta_rows)
        print(f"\nResults written to '{RESULT_VECTORS_TABLE}' and '{RESULT_DELTAS_TABLE}'.")

    # ── Geometry helpers ─────────────────────────────────────────────────────

    def _getTriangulated(self, modelNode):
        tri = vtk.vtkTriangleFilter()
        tri.SetInputData(modelNode.GetPolyData())
        tri.Update()
        return tri.GetOutput()

    def _ensureOutwardNormals(self, poly):
        nrm = vtk.vtkPolyDataNormals()
        nrm.SetInputData(poly)
        nrm.AutoOrientNormalsOn()
        nrm.ConsistencyOn()
        nrm.SplittingOff()
        nrm.Update()
        return nrm.GetOutput()

    def _signedGap(self, lower_poly, upper_poly):
        """
        Signed distance of every lower vertex to the upper surface.
        Negative = penetration/contact, positive = gap.

        vtkDistancePolyDataFilter runs the distance computation in C++
        (no Python loop), giving ~50x speedup over calling
        vtkImplicitPolyDataDistance per point from Python.
        """
        upper = self._ensureOutwardNormals(upper_poly)
        dist  = vtk.vtkDistancePolyDataFilter()
        dist.SetInputData(0, lower_poly)
        dist.SetInputData(1, upper)
        dist.SignedDistanceOn()
        dist.ComputeSecondDistanceOff()
        dist.Update()
        scalars = dist.GetOutput().GetPointData().GetScalars()
        if scalars is None:
            scalars = dist.GetOutput().GetPointData().GetArray(0)
        return vtk_to_numpy(scalars).copy()

    # ── Occlusal vector ───────────────────────────────────────────────────────

    def _occlusionVector(self, lower_poly, gap, tau, n_sectors, min_area):
        return {
            "OCA":         self._computeOCA(lower_poly, gap, tau),
            "OCN":         self._computeOCNRegional(lower_poly, gap, tau, n_sectors, min_area),
            "OCN_cluster": self._computeOCNCluster(lower_poly, gap, tau, min_area),
            "OAS":         self._computeOAS(lower_poly, gap, tau),
        }

    def _contactTriangles(self, lower_poly, gap, tau):
        pts      = vtk_to_numpy(lower_poly.GetPoints().GetData())
        tris     = vtk_to_numpy(lower_poly.GetPolys().GetData()).reshape(-1, 4)[:, 1:]
        a, b, c  = pts[tris[:, 0]], pts[tris[:, 1]], pts[tris[:, 2]]
        areas    = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
        centroids = (a + b + c) / 3.0
        # Conservative mask: all three vertices of a triangle must be within tau
        tri_gap  = np.abs(gap[tris]).max(axis=1)
        contact  = tri_gap <= tau
        return pts, tris, areas, centroids, contact

    def _archFrame(self, points):
        """PCA frame of the arch point cloud.
        Returns centroid, e_transverse (left-right), e_ap (ant-post), normal."""
        c = points.mean(0)
        _, _, Vt = np.linalg.svd(points - c, full_matrices=False)
        return c, Vt[0], Vt[1], Vt[2]

    def _computeOCA(self, lower_poly, gap, tau):
        _, _, areas, _, contact = self._contactTriangles(lower_poly, gap, tau)
        return float(areas[contact].sum())

    def _computeOAS(self, lower_poly, gap, tau):
        pts, _, areas, centroids, contact = self._contactTriangles(lower_poly, gap, tau)
        c, e_trans, _, _ = self._archFrame(pts)
        s     = (centroids - c) @ e_trans
        right = areas[contact & (s >= 0)].sum()
        left  = areas[contact & (s <  0)].sum()
        tot   = right + left
        return float(abs(right - left) / tot) if tot > 0 else float("nan")

    def _computeOCNRegional(self, lower_poly, gap, tau, n_sectors, min_area):
        """OCN Weg B (leading): count arch sectors with contact area >= min_area."""
        pts, _, areas, centroids, contact = self._contactTriangles(lower_poly, gap, tau)
        if not np.any(contact):
            return 0
        c, e_trans, e_ap, _ = self._archFrame(pts)
        x     = (centroids[contact] - c) @ e_trans
        y     = (centroids[contact] - c) @ e_ap
        theta = np.arctan2(y, x)
        edges = np.linspace(-np.pi, np.pi, n_sectors + 1)
        idx   = np.clip(np.digitize(theta, edges) - 1, 0, n_sectors - 1)
        sec_area = np.zeros(n_sectors)
        np.add.at(sec_area, idx, areas[contact])
        return int(np.count_nonzero(sec_area >= min_area))

    def _computeOCNCluster(self, lower_poly, gap, tau, min_area):
        """OCN Weg A (cross-check): scalar-connected contact regions >= min_area."""
        gap_vtk = numpy_to_vtk(gap.astype(np.float32), deep=True)
        gap_vtk.SetName("gap")

        poly = vtk.vtkPolyData()
        poly.DeepCopy(lower_poly)
        poly.GetPointData().SetScalars(gap_vtk)

        conn = vtk.vtkPolyDataConnectivityFilter()
        conn.SetInputData(poly)
        conn.SetExtractionModeToAllRegions()
        conn.ScalarConnectivityOn()
        conn.FullScalarConnectivityOn()
        conn.SetScalarRange(-tau, tau)
        conn.ColorRegionsOn()
        conn.Update()

        nreg = conn.GetNumberOfExtractedRegions()
        if nreg == 0:
            return 0

        out  = conn.GetOutput()
        pts  = vtk_to_numpy(out.GetPoints().GetData())
        tris = vtk_to_numpy(out.GetPolys().GetData()).reshape(-1, 4)[:, 1:]
        a, b, c  = pts[tris[:, 0]], pts[tris[:, 1]], pts[tris[:, 2]]
        areas    = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)

        # Region IDs may be stored in cell data or point data depending on VTK version.
        # For connected regions all points of a cell share the same region, so
        # indexing point-based IDs via the first vertex of each triangle is correct.
        cell_reg_arr = out.GetCellData().GetArray("RegionId")
        if cell_reg_arr is not None:
            reg = vtk_to_numpy(cell_reg_arr)
        else:
            pt_reg = vtk_to_numpy(out.GetPointData().GetArray("RegionId"))
            reg = pt_reg[tris[:, 0]]

        reg_area = np.zeros(nreg)
        np.add.at(reg_area, reg, areas)
        return int(np.count_nonzero(reg_area >= min_area))

    # ── Result tables ─────────────────────────────────────────────────────────

    def _makeOrResetTable(self, name, col_defs):
        node = slicer.mrmlScene.GetFirstNodeByName(name)
        if node is None:
            node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLTableNode", name)
        node.RemoveAllColumns()
        cols = {}
        for colName, isStr in col_defs:
            col = vtk.vtkStringArray() if isStr else vtk.vtkDoubleArray()
            col.SetName(colName)
            node.AddColumn(col)
            cols[colName] = col
        return node, cols

    def _writeVectorTable(self, rows):
        col_defs = [
            ("timepoint", True), ("tau_mm", False),
            ("OCA_mm2", False), ("OCN", False), ("OCN_cluster", False), ("OAS", False),
        ]
        node, cols = self._makeOrResetTable(RESULT_VECTORS_TABLE, col_defs)
        node.GetTable().SetNumberOfRows(len(rows))
        for i, row in enumerate(rows):
            cols["timepoint"].SetValue(i, row["timepoint"])
            cols["tau_mm"].SetValue(i, row["tau"])
            cols["OCA_mm2"].SetValue(i, row["OCA"])
            cols["OCN"].SetValue(i, float(row["OCN"]))
            cols["OCN_cluster"].SetValue(i, float(row["OCN_cluster"]))
            cols["OAS"].SetValue(i, row["OAS"])
        node.Modified()

    def _writeDeltaTable(self, rows):
        col_defs = [
            ("comparison", True), ("tau_mm", False),
            ("OCA_A_mm2", False), ("OCA_B_mm2", False), ("dOCA_mm2", False),
            ("OCN_A", False),    ("OCN_B", False),      ("dOCN", False),
            ("OCNcl_A", False),  ("OCNcl_B", False),
            ("OAS_A", False),    ("OAS_B", False),       ("dOAS", False),
            ("dOCA_robust", True), ("dOCN_robust", True), ("dOAS_robust", True),
            ("verdict", True),
        ]
        node, cols = self._makeOrResetTable(RESULT_DELTAS_TABLE, col_defs)
        node.GetTable().SetNumberOfRows(len(rows))
        for i, row in enumerate(rows):
            cols["comparison"].SetValue(i, row["comparison"])
            cols["tau_mm"].SetValue(i, row["tau"])
            cols["OCA_A_mm2"].SetValue(i, row["OCA_A"])
            cols["OCA_B_mm2"].SetValue(i, row["OCA_B"])
            cols["dOCA_mm2"].SetValue(i, row["dOCA"])
            cols["OCN_A"].SetValue(i, float(row["OCN_A"]))
            cols["OCN_B"].SetValue(i, float(row["OCN_B"]))
            cols["dOCN"].SetValue(i, float(row["dOCN"]))
            cols["OCNcl_A"].SetValue(i, float(row["OCNcl_A"]))
            cols["OCNcl_B"].SetValue(i, float(row["OCNcl_B"]))
            cols["OAS_A"].SetValue(i, row["OAS_A"])
            cols["OAS_B"].SetValue(i, row["OAS_B"])
            cols["dOAS"].SetValue(i, row["dOAS"])
            cols["dOCA_robust"].SetValue(i, str(row["dOCA_robust"]))
            cols["dOCN_robust"].SetValue(i, str(row["dOCN_robust"]))
            cols["dOAS_robust"].SetValue(i, str(row["dOAS_robust"]))
            cols["verdict"].SetValue(i, row["verdict"])
        node.Modified()


# ──────────────────────────────────────────────────────────────────────────────

def _sign(x):
    return (x > 0) - (x < 0)
