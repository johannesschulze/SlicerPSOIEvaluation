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

RESULT_VECTORS_TABLE  = "OcclusionVectors"
RESULT_DELTAS_TABLE   = "OcclusionDeltas"
RESULT_SUMMARY_TABLE  = "OcclusionSummary"
SENSITIVITY_TAUS     = [0.03, 0.05, 0.08]
DEFAULT_TAU          = 0.05
DEFAULT_N_SECTORS    = 6
DEFAULT_MIN_AREA     = 0.1   # mm²

# Occlusion map display settings (match "Model to Model Distance" defaults used in QC)
OCCMAP_SCALAR_RANGE = (0.0, 0.1)   # mm  – display range mapped to color
OCCMAP_THRESHOLD    = (0.0, 0.2)   # mm  – hide points outside this range (unsigned)
OCCMAP_Z_OFFSET     = 0.1          # mm  – shift along Z to avoid z-fighting

# Dental plaster-cast material — warm white, matte
CAST_COLOR     = (0.96, 0.90, 0.80)
CAST_AMBIENT   = 0.30
CAST_DIFFUSE   = 0.75
CAST_SPECULAR  = 0.00
CAST_SPEC_POW  = 5.0


# ──────────────────────────────────────────────────────────────────────────────
# Timepoint persistence (one vtkMRMLScriptedModuleNode per timepoint)
# ──────────────────────────────────────────────────────────────────────────────

class OcclusionTimepoint:
    """Per-timepoint state stored as a vtkMRMLScriptedModuleNode.
    Persists with the .mrb scene file."""

    _TAG        = "OcclusionAnalysis.isTimepoint"
    _ORDER      = "OcclusionAnalysis.order"
    _LABEL      = "OcclusionAnalysis.label"
    _UPPER      = "OcclusionAnalysis.upperModelID"
    _LOWER      = "OcclusionAnalysis.lowerModelID"
    _UPPER_CAST = "OcclusionAnalysis.upperCastID"
    _LOWER_CAST = "OcclusionAnalysis.lowerCastID"

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

    @property
    def upperCast(self):
        return self._getModel(self._UPPER_CAST)

    @upperCast.setter
    def upperCast(self, node):
        self._setModel(self._UPPER_CAST, node)

    @property
    def lowerCast(self):
        return self._getModel(self._LOWER_CAST)

    @lowerCast.setter
    def lowerCast(self, node):
        self._setModel(self._LOWER_CAST, node)

    @property
    def upperDisplay(self):
        """Cast node for display if available, else the original IOS model."""
        c = self.upperCast
        return c if c is not None else self.upperModel

    @property
    def lowerDisplay(self):
        c = self.lowerCast
        return c if c is not None else self.lowerModel

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
    primaryTau        : float = DEFAULT_TAU
    nSectors          : int   = DEFAULT_N_SECTORS
    minArea           : float = DEFAULT_MIN_AREA
    screenshotDir      : str   = ""
    screenshotSize     : int   = 1000
    normalizeZoom      : bool  = True
    occlusalCapture    : bool  = True
    occlusalShowCast   : bool  = True
    occlusalShowLegend : bool  = True
    butterflyCapture   : bool  = True
    butterflyShowCast  : bool  = False
    butterflyShowLegend: bool  = True
    buccalCapture      : bool  = False
    buccalShowCast     : bool  = True
    buccalShowLegend   : bool  = False


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

        # ── Orientation & Registration ────────────────────────────────────
        orientGroup = ctk.ctkCollapsibleButton()
        orientGroup.text = "Orientation & Registration"
        orientGroup.collapsed = True
        self.layout.addWidget(orientGroup)
        orientLayout = qt.QVBoxLayout(orientGroup)

        self._orientT0Button = qt.QPushButton("Orient T0 interactively")
        self._orientT0Button.setToolTip(
            "Attach an interactive transform to the T0 upper and lower jaw models. "
            "Use the 3D-view handles to rotate/translate, then click 'Confirm'."
        )
        orientLayout.addWidget(self._orientT0Button)

        self._confirmOrientButton = qt.QPushButton("Confirm T0 orientation")
        self._confirmOrientButton.setToolTip(
            "Harden the interactive transform into the T0 mesh coordinates and "
            "remove the transform node."
        )
        orientLayout.addWidget(self._confirmOrientButton)

        self._registerButton = qt.QPushButton("Register T1, T2, … to T0 (ICP)")
        self._registerButton.setToolTip(
            "Rigid ICP registration of each Ti upper jaw to T0 upper jaw. "
            "The same transform is applied to the corresponding lower jaw. "
            "T0 must already be in the desired orientation."
        )
        orientLayout.addWidget(self._registerButton)

        # ── Run-Analysis-Button ───────────────────────────────────────────
        self.layout.addSpacing(20)
        self._runButton = qt.QPushButton("Run analysis!")
        self._runButton.setStyleSheet("font-weight: bold; padding: 6px;")
        self.layout.addWidget(self._runButton)
        self.layout.addSpacing(20)

        # ── Create Occlusion Maps Button ──────────────────────────────────
        self._mapButton = qt.QPushButton("Create occlusion maps")
        self._mapButton.setStyleSheet("padding: 6px;")
        self._mapButton.setToolTip(
            "For each timepoint: compute lower→upper signed distance, create a "
            "colorized model node (scalar range 0–0.1 mm, threshold ±0.2 mm)."
        )
        self.layout.addWidget(self._mapButton)
        self.layout.addSpacing(20)
        
        # ── Screenshots ───────────────────────────────────────────────────
        screenshotGroup = ctk.ctkCollapsibleButton()
        screenshotGroup.text = "Screenshots"
        screenshotGroup.collapsed = True
        self.layout.addWidget(screenshotGroup)
        screenshotLayout = qt.QFormLayout(screenshotGroup)

        self._screenshotDirSelector = ctk.ctkPathLineEdit()
        self._screenshotDirSelector.filters = ctk.ctkPathLineEdit.Dirs
        self._screenshotDirSelector.setToolTip("Folder to save screenshots into.")
        screenshotLayout.addRow("Output folder:", self._screenshotDirSelector)

        # ── Per-view options table  (Capture / Show cast / Show legend) ──
        _viewOptWidget = qt.QWidget()
        _viewOptLayout = qt.QGridLayout(_viewOptWidget)
        _viewOptLayout.setContentsMargins(0, 2, 0, 2)
        _viewOptLayout.setSpacing(4)

        for _col, _hdr in enumerate(["", "Capture", "Show cast", "Show legend"]):
            _lbl = qt.QLabel(_hdr)
            _lbl.setAlignment(qt.Qt.AlignCenter)
            _viewOptLayout.addWidget(_lbl, 0, _col)

        self._occlusalCaptureCB   = qt.QCheckBox(); self._occlusalCaptureCB.setChecked(True)
        self._occlusalCastCB      = qt.QCheckBox(); self._occlusalCastCB.setChecked(True)
        self._occlusalLegendCB    = qt.QCheckBox(); self._occlusalLegendCB.setChecked(True)
        self._butterflyCaptureCB  = qt.QCheckBox(); self._butterflyCaptureCB.setChecked(True)
        self._butterflyCastCB     = qt.QCheckBox(); self._butterflyCastCB.setChecked(False)
        self._butterflyLegendCB   = qt.QCheckBox(); self._butterflyLegendCB.setChecked(True)
        self._buccalCaptureCB     = qt.QCheckBox(); self._buccalCaptureCB.setChecked(False)
        self._buccalCastCB        = qt.QCheckBox(); self._buccalCastCB.setChecked(True)
        self._buccalLegendCB      = qt.QCheckBox(); self._buccalLegendCB.setChecked(False)

        _viewRows = [
            ("Occlusal",
             self._occlusalCaptureCB,  self._occlusalCastCB,  self._occlusalLegendCB),
            ("Butterfly",
             self._butterflyCaptureCB, self._butterflyCastCB, self._butterflyLegendCB),
            ("Buccal",
             self._buccalCaptureCB,    self._buccalCastCB,    self._buccalLegendCB),
        ]
        for _row, (_label, *_cbs) in enumerate(_viewRows, start=1):
            _viewOptLayout.addWidget(qt.QLabel(_label), _row, 0)
            for _col, _cb in enumerate(_cbs, start=1):
                _wrap = qt.QWidget()
                _wl   = qt.QHBoxLayout(_wrap)
                _wl.setContentsMargins(0, 0, 0, 0)
                _wl.setAlignment(qt.Qt.AlignCenter)
                _wl.addWidget(_cb)
                _viewOptLayout.addWidget(_wrap, _row, _col)

        screenshotLayout.addRow(_viewOptWidget)

        self._screenshotSizeSpinBox = qt.QSpinBox()
        self._screenshotSizeSpinBox.setMinimum(100)
        self._screenshotSizeSpinBox.setMaximum(4000)
        self._screenshotSizeSpinBox.setSingleStep(100)
        self._screenshotSizeSpinBox.setSuffix(" px")
        self._screenshotSizeSpinBox.setValue(500)
        self._screenshotSizeSpinBox.setToolTip("Width and height of each output PNG.")
        screenshotLayout.addRow("Resolution:", self._screenshotSizeSpinBox)

        self._normalizeZoomCheckBox = qt.QCheckBox("Normalize zoom within groups")
        self._normalizeZoomCheckBox.setChecked(True)
        self._normalizeZoomCheckBox.setToolTip(
            "Use the same zoom level across all timepoints for each view group "
            "(occlusal, butterfly, lateral), so screenshots are directly comparable."
        )
        screenshotLayout.addRow("", self._normalizeZoomCheckBox)

        self._createCastsButton = qt.QPushButton("Create cast models")
        self._createCastsButton.setToolTip(
            "Build a trimmed orthodontic art base for each arch and show it in "
            "the 3-D view. Runs automatically with 'Create occlusion maps'."
        )
        self._smoothCastWallsButton = qt.QPushButton("Smooth cast walls")
        self._smoothCastWallsButton.setToolTip(
            "Rebuild cast models with the boundary loop resampled to uniform "
            "arc-length spacing, removing stripe rendering artifacts on the walls."
        )
        castBtnRow = qt.QHBoxLayout()
        castBtnRow.addWidget(self._createCastsButton)
        castBtnRow.addWidget(self._smoothCastWallsButton)
        screenshotLayout.addRow(castBtnRow)

        self._screenshotButton = qt.QPushButton("Take screenshots")
        self._screenshotButton.setToolTip(
            "For each timepoint: upper jaw (inferior view) + lower jaw (superior "
            "view, rolled 180°), transparent PNG background."
        )
        screenshotLayout.addRow(self._screenshotButton)

        

        self._reportButton = qt.QPushButton("Generate report")
        self._reportButton.setToolTip(
            "Write occlusion_analysis_report.md/.pdf/.odt to the output folder "
            "(requires pandoc; PDF also requires xelatex)."
        )
        screenshotLayout.addRow(self._reportButton)      

        self.layout.addStretch(1)
        
        # ── Connections ───────────────────────────────────────────────────
        self._addTimepointButton.connect("clicked(bool)", self._onAddTimepointClicked)
        self._orientT0Button.connect("clicked(bool)", self._onOrientT0Clicked)
        self._confirmOrientButton.connect("clicked(bool)", self._onConfirmOrientClicked)
        self._registerButton.connect("clicked(bool)", self._onRegisterClicked)
        self._screenshotDirSelector.connect("currentPathChanged(QString)", self._onScreenshotDirChanged)
        self._screenshotSizeSpinBox.connect("valueChanged(int)", self._onScreenshotSettingChanged)
        self._normalizeZoomCheckBox.connect("toggled(bool)", self._onScreenshotSettingChanged)
        for _cb in (
            self._occlusalCaptureCB,  self._occlusalCastCB,  self._occlusalLegendCB,
            self._butterflyCaptureCB, self._butterflyCastCB, self._butterflyLegendCB,
            self._buccalCaptureCB,    self._buccalCastCB,    self._buccalLegendCB,
        ):
            _cb.connect("toggled(bool)", self._onScreenshotSettingChanged)
        self._screenshotButton.connect("clicked(bool)", self._onScreenshotClicked)
        self._createCastsButton.connect("clicked(bool)", self._onCreateCastsClicked)
        self._smoothCastWallsButton.connect("clicked(bool)", self._onSmoothCastWallsClicked)
        self._reportButton.connect("clicked(bool)", self._onReportClicked)
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
        if pn.screenshotDir:
            self._screenshotDirSelector.blockSignals(True)
            self._screenshotDirSelector.setCurrentPath(pn.screenshotDir)
            self._screenshotDirSelector.blockSignals(False)
        self._screenshotSizeSpinBox.blockSignals(True)
        self._screenshotSizeSpinBox.setValue(pn.screenshotSize)
        self._screenshotSizeSpinBox.blockSignals(False)
        for widget, attr in (
            (self._normalizeZoomCheckBox,     "normalizeZoom"),
            (self._occlusalCaptureCB,         "occlusalCapture"),
            (self._occlusalCastCB,            "occlusalShowCast"),
            (self._occlusalLegendCB,          "occlusalShowLegend"),
            (self._butterflyCaptureCB,        "butterflyCapture"),
            (self._butterflyCastCB,           "butterflyShowCast"),
            (self._butterflyLegendCB,         "butterflyShowLegend"),
            (self._buccalCaptureCB,           "buccalCapture"),
            (self._buccalCastCB,              "buccalShowCast"),
            (self._buccalLegendCB,            "buccalShowLegend"),
        ):
            widget.blockSignals(True)
            widget.setChecked(getattr(pn, attr))
            widget.blockSignals(False)

    def _onSettingChanged(self, *_):
        if self._parameterNode is None:
            return
        self._parameterNode.primaryTau = self._primaryTauSpinBox.value
        self._parameterNode.nSectors   = int(self._nSectorsSpinBox.value)
        self._parameterNode.minArea    = self._minAreaSpinBox.value

    def _onScreenshotDirChanged(self, path):
        if self._parameterNode is not None:
            self._parameterNode.screenshotDir = path

    def _onScreenshotSettingChanged(self, *_):
        if self._parameterNode is not None:
            pn = self._parameterNode
            pn.screenshotSize       = self._screenshotSizeSpinBox.value
            pn.normalizeZoom        = self._normalizeZoomCheckBox.isChecked()
            pn.occlusalCapture      = self._occlusalCaptureCB.isChecked()
            pn.occlusalShowCast     = self._occlusalCastCB.isChecked()
            pn.occlusalShowLegend   = self._occlusalLegendCB.isChecked()
            pn.butterflyCapture     = self._butterflyCaptureCB.isChecked()
            pn.butterflyShowCast    = self._butterflyCastCB.isChecked()
            pn.butterflyShowLegend  = self._butterflyLegendCB.isChecked()
            pn.buccalCapture        = self._buccalCaptureCB.isChecked()
            pn.buccalShowCast       = self._buccalCastCB.isChecked()
            pn.buccalShowLegend     = self._buccalLegendCB.isChecked()

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

        upperTrimBtn = qt.QPushButton("✂")
        upperTrimBtn.setFixedWidth(26)
        upperTrimBtn.setToolTip("Trim upper model to arch-shaped cast boundary")

        lowerSelector = self._makeModelSelector("Lower arch mesh (MIP)")
        lowerSelector.setCurrentNode(tp.lowerModel)

        lowerTrimBtn = qt.QPushButton("✂")
        lowerTrimBtn.setFixedWidth(26)
        lowerTrimBtn.setToolTip("Trim lower model to arch-shaped cast boundary")

        removeBtn = qt.QPushButton("✕")
        removeBtn.setFixedWidth(26)
        removeBtn.setToolTip("Remove timepoint")

        rowLayout.addWidget(qt.QLabel("Label:"))
        rowLayout.addWidget(labelEdit)
        rowLayout.addWidget(qt.QLabel("Upper:"))
        rowLayout.addWidget(upperSelector)
        rowLayout.addWidget(upperTrimBtn)
        rowLayout.addWidget(qt.QLabel("Lower:"))
        rowLayout.addWidget(lowerSelector)
        rowLayout.addWidget(lowerTrimBtn)
        rowLayout.addWidget(removeBtn)

        self._timepointsLayout.addWidget(rowWidget)

        row = {
            "widget":        rowWidget,
            "labelEdit":     labelEdit,
            "upperSelector": upperSelector,
            "upperTrimBtn":  upperTrimBtn,
            "lowerSelector": lowerSelector,
            "lowerTrimBtn":  lowerTrimBtn,
            "removeBtn":     removeBtn,
            "tp":            tp,
        }
        self._timepointRows.append(row)

        labelEdit.connect(     "textChanged(QString)",             lambda v, r=row: self._onLabelChanged(v, r))
        upperSelector.connect( "currentNodeChanged(vtkMRMLNode*)", lambda n, r=row: self._onUpperChanged(n, r))
        lowerSelector.connect( "currentNodeChanged(vtkMRMLNode*)", lambda n, r=row: self._onLowerChanged(n, r))
        upperTrimBtn.connect(  "clicked(bool)",                    lambda _, r=row: self._onTrimClicked(r["tp"].upperModel))
        lowerTrimBtn.connect(  "clicked(bool)",                    lambda _, r=row: self._onTrimClicked(r["tp"].lowerModel))
        removeBtn.connect(     "clicked(bool)",                    lambda _, r=row: self._onRemoveClicked(r))

    def _onTrimClicked(self, model_node):
        if model_node is None:
            slicer.util.warningDisplay("No model selected for this jaw.")
            return

        curve_holder = [None]

        dlg = qt.QDialog(slicer.util.mainWindow())
        dlg.setWindowTitle(f"Trim  —  {model_node.GetName()}")
        dlg.setMinimumWidth(340)
        layout = qt.QVBoxLayout(dlg)

        info = qt.QLabel(
            "1.  Click <b>Draw outline</b>, then place points on the model.\n"
            "    Close the loop with a right-click (or double-click).\n\n"
            "2.  Click <b>Apply trim</b> to clip this model, or\n"
            "    <b>Apply to all models</b> to clip every model in every timepoint."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        drawBtn     = qt.QPushButton("Draw outline")
        applyBtn    = qt.QPushButton("Apply trim")
        applyAllBtn = qt.QPushButton("Apply to all models")
        applyBtn.setEnabled(False)
        applyAllBtn.setEnabled(False)
        btnBox = qt.QDialogButtonBox(qt.QDialogButtonBox.Cancel)
        layout.addWidget(drawBtn)
        layout.addWidget(applyBtn)
        layout.addWidget(applyAllBtn)
        layout.addWidget(btnBox)

        def _startDraw():
            if curve_holder[0] is not None:
                slicer.mrmlScene.RemoveNode(curve_holder[0])
            curve = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLMarkupsClosedCurveNode",
                f"_trim_{model_node.GetName()}"
            )
            curve.GetDisplayNode().SetGlyphScale(1.5)
            selNode = slicer.mrmlScene.GetNodeByID("vtkMRMLSelectionNodeSingleton")
            selNode.SetReferenceActivePlaceNodeClassName("vtkMRMLMarkupsClosedCurveNode")
            selNode.SetActivePlaceNodeID(curve.GetID())
            intNode = slicer.app.applicationLogic().GetInteractionNode()
            intNode.SetCurrentInteractionMode(intNode.Place)
            curve_holder[0] = curve
            applyBtn.setEnabled(True)
            applyAllBtn.setEnabled(True)

        def _applyTrim():
            if curve_holder[0] is None:
                return
            self.logic.trimModelWithCurve(model_node, curve_holder[0])
            dlg.accept()

        def _applyToAll():
            if curve_holder[0] is None:
                return
            all_models = []
            for r in self._timepointRows:
                tp = r["tp"]
                if tp.upperModel:
                    all_models.append(tp.upperModel)
                if tp.lowerModel:
                    all_models.append(tp.lowerModel)
            for m in all_models:
                self.logic.trimModelWithCurve(m, curve_holder[0])
            dlg.accept()

        def _cancel():
            if curve_holder[0] is not None:
                slicer.mrmlScene.RemoveNode(curve_holder[0])
                intNode = slicer.app.applicationLogic().GetInteractionNode()
                intNode.SetCurrentInteractionMode(intNode.ViewTransform)
            dlg.reject()

        drawBtn.connect(    "clicked(bool)", lambda _: _startDraw())
        applyBtn.connect(   "clicked(bool)", lambda _: _applyTrim())
        applyAllBtn.connect("clicked(bool)", lambda _: _applyToAll())
        btnBox.rejected.connect(_cancel)
        dlg.show()

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
        self.logic.applyDentalCastMaterial(node)

    def _onLowerChanged(self, node, row):
        row["tp"].lowerModel = node
        self.logic.applyDentalCastMaterial(node)

    def _onRemoveClicked(self, row):
        row["tp"].remove()
        row["widget"].setParent(None)
        self._timepointRows.remove(row)
        for i, r in enumerate(self._timepointRows):
            r["tp"].order = i

    # ── Map / Run ─────────────────────────────────────────────────────────

    def _requireTimepoints(self, minCount=1):
        timepoints = [r["tp"] for r in self._timepointRows]
        if len(timepoints) < minCount:
            slicer.util.errorDisplay(
                f"At least {minCount} timepoint(s) with upper and lower arch mesh required."
            )
            return None
        for r in self._timepointRows:
            tp = r["tp"]
            if tp.upperModel is None or tp.lowerModel is None:
                slicer.util.errorDisplay(
                    f"Timepoint '{tp.label}': upper and lower arch mesh must both be selected."
                )
                return None
        return timepoints

    def _onOrientT0Clicked(self):
        timepoints = self._requireTimepoints(1)
        if timepoints is None:
            return
        with slicer.util.tryWithErrorDisplay(_("Orient T0 failed."), waitCursor=True):
            self.logic.orientT0Interactive(timepoints)

    def _onConfirmOrientClicked(self):
        timepoints = self._requireTimepoints(1)
        if timepoints is None:
            return
        with slicer.util.tryWithErrorDisplay(_("Confirm orientation failed."), waitCursor=True):
            self.logic.confirmT0Orientation(timepoints)

    def _onRegisterClicked(self):
        timepoints = self._requireTimepoints(2)
        if timepoints is None:
            return
        with slicer.util.tryWithErrorDisplay(_("ICP registration failed."), waitCursor=True):
            self.logic.registerTimepointsToT0(timepoints)

    def _onScreenshotClicked(self):
        outputDir = self._screenshotDirSelector.currentPath
        if not outputDir:
            slicer.util.errorDisplay("Please select an output folder first.")
            return
        timepoints = self._requireTimepoints(1)
        if timepoints is None:
            return
        pn   = self._parameterNode
        size = (pn.screenshotSize, pn.screenshotSize)
        with slicer.util.tryWithErrorDisplay(_("Screenshot failed."), waitCursor=True):
            self.logic.takeScreenshots(
                timepoints,
                outputDir,
                occlusalCapture=pn.occlusalCapture,
                occlusalShowCast=pn.occlusalShowCast,
                occlusalShowLegend=pn.occlusalShowLegend,
                butterflyCapture=pn.butterflyCapture,
                butterflyShowCast=pn.butterflyShowCast,
                butterflyShowLegend=pn.butterflyShowLegend,
                buccalCapture=pn.buccalCapture,
                buccalShowCast=pn.buccalShowCast,
                buccalShowLegend=pn.buccalShowLegend,
                screenshotSize=size,
                normalizeZoom=pn.normalizeZoom,
            )
        slicer.util.infoDisplay(f"Screenshots saved to:\n{outputDir}")

    def _onCreateCastsClicked(self):
        timepoints = self._requireTimepoints(1)
        if timepoints is None:
            return
        with slicer.util.tryWithErrorDisplay(_("Cast model creation failed."), waitCursor=True):
            self.logic.createCastModels(timepoints)
        slicer.util.infoDisplay(
            f"Cast models created for {len(timepoints)} timepoint(s).",
            autoCloseMsec=2000,
        )

    def _onSmoothCastWallsClicked(self):
        timepoints = self._requireTimepoints(1)
        if timepoints is None:
            return
        with slicer.util.tryWithErrorDisplay(_("Cast wall smoothing failed."), waitCursor=True):
            self.logic.createCastModels(timepoints, resample_walls=True)
        slicer.util.infoDisplay(
            f"Cast walls smoothed for {len(timepoints)} timepoint(s).",
            autoCloseMsec=2000,
        )

    def _onReportClicked(self):
        outputDir = self._screenshotDirSelector.currentPath
        if not outputDir:
            slicer.util.errorDisplay("Please select an output folder first.")
            return
        timepoints = self._requireTimepoints(1)
        if timepoints is None:
            return
        with slicer.util.tryWithErrorDisplay(_("Report generation failed."), waitCursor=True):
            html_path, generated = self.logic.generateReport(timepoints, outputDir)
        pdfs = [p for p in generated if p.endswith(".pdf")]
        parts = []
        if pdfs:
            parts.append(f"PDF:  {pdfs[0]}")
        parts.append(f"HTML: {html_path}")
        slicer.util.infoDisplay("Report written:\n" + "\n".join(parts))

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

    # ── Orientation & Registration ───────────────────────────────────────────

    _T0_TRANSFORM_NAME = "T0_orientation"
    _AXIS_GRID_NAMES   = ["T0_axis_XY", "T0_axis_XZ", "T0_axis_YZ"]
    _orientSavedVis    = None   # populated in orientT0Interactive, cleared in confirm

    def _createAlignmentGrid(self):
        """Three 100×100 mm axis-plane model nodes at origin as an orientation guide.

        Uses vtkMRMLModelNode (wireframe polygon) rather than markup plane nodes
        so the API is Slicer-version independent.
        """
        for name in self._AXIS_GRID_NAMES:
            old = slicer.mrmlScene.GetFirstNodeByName(name)
            if old:
                slicer.mrmlScene.RemoveNode(old)

        plane_defs = [
            ("T0_axis_XY", np.array([1,0,0], float), np.array([0,1,0], float)),
            ("T0_axis_XZ", np.array([1,0,0], float), np.array([0,0,1], float)),
            ("T0_axis_YZ", np.array([0,1,0], float), np.array([0,0,1], float)),
        ]
        n_seg    = 4        # grid subdivisions per side → 4×4 squares
        half     = 50.0     # half-size in mm  →  100×100 mm total
        coords   = np.linspace(-half, half, n_seg + 1)
        grid_nodes = []
        for name, ax0, ax1 in plane_defs:
            pts   = vtk.vtkPoints()
            cells = vtk.vtkCellArray()
            # (n_seg+1)² vertices
            vid = {}
            for i, s in enumerate(coords):
                for j, t in enumerate(coords):
                    vid[(i, j)] = pts.InsertNextPoint(*(s * ax0 + t * ax1))
            # n_seg² quads
            for i in range(n_seg):
                for j in range(n_seg):
                    quad = vtk.vtkQuad()
                    for k, (di, dj) in enumerate([(0,0),(1,0),(1,1),(0,1)]):
                        quad.GetPointIds().SetId(k, vid[(i+di, j+dj)])
                    cells.InsertNextCell(quad)
            pd = vtk.vtkPolyData()
            pd.SetPoints(pts)
            pd.SetPolys(cells)

            node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLModelNode", name)
            node.SetAndObservePolyData(pd)
            node.CreateDefaultDisplayNodes()
            dn = node.GetDisplayNode()
            dn.SetRepresentation(1)       # 1 = wireframe → clean rectangle outline
            dn.SetColor(0.8, 0.15, 0.15)
            dn.SetLineWidth(2)
            dn.SetOpacity(0.8)
            dn.SetLighting(False)          # consistent colour regardless of scene lights
            dn.SetVisibility(True)
            grid_nodes.append(node)
        return grid_nodes

    def orientT0Interactive(self, timepoints):
        """OBB-align T0 models to world axes, show axis grid, hide everything else."""
        tp0 = timepoints[0]

        # ── OBB via PCA ──────────────────────────────────────────────────────
        pts_list = [vtk_to_numpy(m.GetPolyData().GetPoints().GetData())
                    for m in (tp0.upperModel, tp0.lowerModel) if m]
        if pts_list:
            all_pts  = np.vstack(pts_list)
            centroid = all_pts.mean(axis=0)
            _, _, Vt = np.linalg.svd(all_pts - centroid, full_matrices=False)
            # Vt rows: most→least variance = transverse(LR), AP, occlusal(SI)
            e_lr, e_ap, e_si = Vt[0], Vt[1], Vt[2]
            if np.dot(np.cross(e_lr, e_ap), e_si) < 0:
                e_si = -e_si                          # ensure right-handed
            # OBB alignment → LR=X, AP=Y, SI=Z; then flip 180° around AP (Y)
            R_obb   = np.vstack([e_lr, e_ap, e_si])
            R_ap180 = np.diag([-1.0, 1.0, -1.0])     # 180° around Y
            R = R_ap180 @ R_obb
            # Centre the AABB of the rotated cloud at world origin.
            # R @ mean ≠ bbox_centre(R @ pts), so compute explicitly.
            P_rot = (R @ all_pts.T).T
            t = -(P_rot.min(axis=0) + P_rot.max(axis=0)) / 2
        else:
            R, t = np.eye(3), np.zeros(3)

        # ── Create / update transform node ───────────────────────────────────
        tfmNode = slicer.mrmlScene.GetFirstNodeByName(self._T0_TRANSFORM_NAME)
        if tfmNode is None:
            tfmNode = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLLinearTransformNode", self._T0_TRANSFORM_NAME
            )
        mat = vtk.vtkMatrix4x4()
        for i in range(3):
            for j in range(3):
                mat.SetElement(i, j, R[i, j])
            mat.SetElement(i, 3, t[i])
        tfmNode.SetMatrixTransformToParent(mat)

        for model in (tp0.upperModel, tp0.lowerModel):
            if model:
                model.SetAndObserveTransformNodeID(tfmNode.GetID())

        # ── Re-centre using actual GetRASBounds ──────────────────────────────
        # GetRASBounds maps the LOCAL AABB corners through the rotation, which
        # gives a different AABB centre than the rotated point cloud.  Read the
        # real world bounds and subtract the residual so the centre is at (0,0,0).
        b6  = [0.0] * 6
        wmin, wmax = np.full(3, 1e9), np.full(3, -1e9)
        for model in (tp0.upperModel, tp0.lowerModel):
            if model:
                model.GetRASBounds(b6)
                wmin = np.minimum(wmin, [b6[0], b6[2], b6[4]])
                wmax = np.maximum(wmax, [b6[1], b6[3], b6[5]])
        offset = (wmin + wmax) / 2          # residual: how far the centre is from (0,0,0)
        for i in range(3):
            mat.SetElement(i, 3, mat.GetElement(i, 3) - offset[i])
        tfmNode.SetMatrixTransformToParent(mat)

        # Place gizmo at world (0,0,0): solve R @ c + t = 0  →  c = -Rᵀ @ t
        t_final = np.array([mat.GetElement(i, 3) for i in range(3)])
        c_local = -R.T @ t_final
        tfmNode.SetCenterOfTransformation(*c_local)

        tfmNode.CreateDefaultDisplayNodes()
        dn = tfmNode.GetDisplayNode()
        dn.SetEditorVisibility(True)
        dn.SetEditorVisibility3D(True)
        dn.SetRotationHandleComponentVisibility3D(True, True, True, True)

        # ── Save scene visibility (once) and show only T0 models + grid ──────
        def _display_nodes():
            """Yield (node, displayNode) for every displayable node in scene."""
            for _i in range(slicer.mrmlScene.GetNumberOfNodes()):
                _n = slicer.mrmlScene.GetNthNode(_i)
                if not hasattr(_n, 'GetDisplayNode'):
                    continue
                _dn = _n.GetDisplayNode()
                if _dn is not None:
                    yield _n, _dn

        if self._orientSavedVis is None:
            self._orientSavedVis = {n.GetID(): dn.GetVisibility()
                                    for n, dn in _display_nodes()}

        grid_nodes = self._createAlignmentGrid()

        keep_ids = {m.GetID() for m in (tp0.upperModel, tp0.lowerModel) if m}
        keep_ids |= {gn.GetID() for gn in grid_nodes}

        for n, dn in _display_nodes():
            dn.SetVisibility(1 if n.GetID() in keep_ids else 0)

        print(f"OBB-aligned T0 models. Use 3-D handles, then 'Confirm T0 orientation'.")

    def confirmT0Orientation(self, timepoints):
        """Harden the T0 orientation transform and restore scene visibility."""
        tp0 = timepoints[0]
        tfmNode = slicer.mrmlScene.GetFirstNodeByName(self._T0_TRANSFORM_NAME)
        for model in (tp0.upperModel, tp0.lowerModel):
            if model:
                slicer.vtkSlicerTransformLogic().hardenTransform(model)
        if tfmNode:
            tfmNode.GetDisplayNode().SetEditorVisibility(False)
            slicer.mrmlScene.RemoveNode(tfmNode)

        # Remove axis grid
        for name in self._AXIS_GRID_NAMES:
            n = slicer.mrmlScene.GetFirstNodeByName(name)
            if n:
                slicer.mrmlScene.RemoveNode(n)

        # Restore saved visibility
        if self._orientSavedVis:
            saved = self._orientSavedVis
            for i in range(slicer.mrmlScene.GetNumberOfNodes()):
                n = slicer.mrmlScene.GetNthNode(i)
                if not hasattr(n, 'GetDisplayNode'):
                    continue
                dn2 = n.GetDisplayNode()
                if dn2 and n.GetID() in saved:
                    dn2.SetVisibility(saved[n.GetID()])
            self._orientSavedVis = None

        print("T0 orientation confirmed and hardened.")

    def registerTimepointsToT0(self, timepoints):
        """Rigid ICP: register Ti upper jaw to T0 upper jaw; apply same transform to Ti lower jaw."""
        t0_poly = self._getTriangulated(timepoints[0].upperModel)
        print("\n=== ICP Registration ===")
        for tp in timepoints[1:]:
            upper_poly = self._getTriangulated(tp.upperModel)

            icp = vtk.vtkIterativeClosestPointTransform()
            icp.SetSource(upper_poly)
            icp.SetTarget(t0_poly)
            icp.GetLandmarkTransform().SetModeToRigidBody()
            icp.SetMaximumNumberOfIterations(200)
            icp.SetMaximumMeanDistance(0.01)
            icp.CheckMeanDistanceOn()
            icp.StartByMatchingCentroidsOn()
            icp.Update()

            tfmName = f"{tp.label}_registration"
            tfmNode = slicer.mrmlScene.GetFirstNodeByName(tfmName)
            if tfmNode is None:
                tfmNode = slicer.mrmlScene.AddNewNodeByClass(
                    "vtkMRMLLinearTransformNode", tfmName
                )
            tfmNode.SetMatrixTransformToParent(icp.GetMatrix())

            for model in (tp.upperModel, tp.lowerModel):
                if model:
                    model.SetAndObserveTransformNodeID(tfmNode.GetID())
                    slicer.vtkSlicerTransformLogic().hardenTransform(model)

            slicer.mrmlScene.RemoveNode(tfmNode)
            print(f"  {tp.label}: ICP registration applied and hardened.")
        print("Registration complete.")

    # ── Screenshots ──────────────────────────────────────────────────────────

    def takeScreenshots(self, timepoints, outputDir,
                        occlusalCapture=True,   occlusalShowCast=True,   occlusalShowLegend=True,
                        butterflyCapture=True,  butterflyShowCast=False, butterflyShowLegend=True,
                        buccalCapture=False,    buccalShowCast=True,     buccalShowLegend=False,
                        screenshotSize=(500, 500), normalizeZoom=True):
        import os, math
        threeDWidget = slicer.app.layoutManager().threeDWidget(0)
        threeDView   = threeDWidget.threeDView()
        renderer     = threeDView.renderWindow().GetRenderers().GetFirstRenderer()
        viewAngle    = renderer.GetActiveCamera().GetViewAngle()
        aspect       = screenshotSize[0] / screenshotSize[1]
        D            = 10000

        # Save original model visibility before touching anything
        origVisibility = {}
        for i in range(slicer.mrmlScene.GetNumberOfNodesByClass("vtkMRMLModelNode")):
            n = slicer.mrmlScene.GetNthNodeByClass(i, "vtkMRMLModelNode")
            dn = n.GetDisplayNode()
            if dn:
                origVisibility[n.GetID()] = dn.GetVisibility()

        # Ensure no subject hierarchy folder hides nodes we intend to show.
        # Save every item's visibility and make all items visible so that
        # per-node display-node visibility is the sole gating factor.
        shNode = slicer.vtkMRMLSubjectHierarchyNode.GetSubjectHierarchyNode(slicer.mrmlScene)
        origShVisibility = {}
        shChildIds = vtk.vtkIdList()
        shNode.GetItemChildren(shNode.GetSceneItemID(), shChildIds, True)
        for _k in range(shChildIds.GetNumberOfIds()):
            _item = shChildIds.GetId(_k)
            origShVisibility[_item] = shNode.GetItemDisplayVisibility(_item)
            shNode.SetItemDisplayVisibility(_item, True)

        # ── Pre-compute shared butterfly hinge ────────────────────────────
        # Posterior border of the upper model with the largest AP extent,
        # so all timepoints fold around the same axis for direct comparison.
        sharedButterflyHinge = None   # (cy_hinge, cz_hinge)
        if butterflyCapture and len(timepoints) > 1:
            best_ap, best_b = -1.0, None
            for tp in timepoints:
                if tp.upperModel:
                    b = [0.0] * 6
                    tp.upperModel.GetRASBounds(b)
                    ap = b[3] - b[2]
                    if ap > best_ap:
                        best_ap, best_b = ap, b
            if best_b is not None:
                sharedButterflyHinge = (best_b[2] - 1.0, best_b[4])

        # ── Pre-compute normalized cameras (union of all timepoint meshes) ──
        # Only meaningful with more than one timepoint.
        globalUpperCam     = None   # (focal, camPos, viewUp)
        globalLowerCam     = None
        globalButterflyCam = None
        globalLateralCams  = None   # dict: suffix → (focal, camPos, viewUp)

        if normalizeZoom and len(timepoints) > 1:
            if occlusalCapture:
                upper_pts = []
                lower_pts = []
                for tp in timepoints:
                    for m in ([tp.upperModel] + ([tp.upperCast] if occlusalShowCast else [])):
                        if m: upper_pts.append(vtk_to_numpy(m.GetPolyData().GetPoints().GetData()))
                    for m in ([tp.lowerModel] + ([tp.lowerCast] if occlusalShowCast else [])):
                        if m: lower_pts.append(vtk_to_numpy(m.GetPolyData().GetPoints().GetData()))
                if upper_pts:
                    all_u  = np.vstack(upper_pts)
                    ctr    = (all_u.min(axis=0) + all_u.max(axis=0)) / 2
                    f, c   = self._fitCameraToMeshPoints(
                        all_u, tuple(ctr + [0, 0, -D]), tuple(ctr), (0, 1, 0), viewAngle, aspect)
                    globalUpperCam = (f, c, (0, 1, 0))
                if lower_pts:
                    all_l  = np.vstack(lower_pts)
                    ctr    = (all_l.min(axis=0) + all_l.max(axis=0)) / 2
                    f, c   = self._fitCameraToMeshPoints(
                        all_l, tuple(ctr + [0, 0, D]), tuple(ctr), (0, -1, 0), viewAngle, aspect)
                    globalLowerCam = (f, c, (0, -1, 0))

            if butterflyCapture and sharedButterflyHinge:
                cy_h, cz_h = sharedButterflyHinge
                all_bf = []
                for tp in timepoints:
                    if not (tp.upperModel and tp.lowerModel):
                        continue
                    m_np = np.array([[1,0,0,0],[0,-1,0,2*cy_h],[0,0,-1,2*cz_h],[0,0,0,1]],
                                    dtype=float)
                    pts_u = vtk_to_numpy(tp.upperModel.GetPolyData().GetPoints().GetData())
                    pts_l = vtk_to_numpy(tp.lowerModel.GetPolyData().GetPoints().GetData())
                    pts_l_xfm = (m_np @ np.hstack(
                        [pts_l, np.ones((len(pts_l), 1))]).T).T[:, :3]
                    all_bf.extend([pts_u, pts_l_xfm])
                    if butterflyShowCast:
                        if tp.upperCast:
                            all_bf.append(vtk_to_numpy(tp.upperCast.GetPolyData().GetPoints().GetData()))
                        if tp.lowerCast:
                            pts_lc = vtk_to_numpy(tp.lowerCast.GetPolyData().GetPoints().GetData())
                            all_bf.append((m_np @ np.hstack(
                                [pts_lc, np.ones((len(pts_lc), 1))]).T).T[:, :3])
                if all_bf:
                    all_bf_arr   = np.vstack(all_bf)
                    rough_foc    = (all_bf_arr.min(axis=0) + all_bf_arr.max(axis=0)) / 2
                    rough_cam_bf = rough_foc + np.array([0, 0, -D])
                    f, c = self._fitCameraToMeshPoints(
                        all_bf_arr, rough_cam_bf, rough_foc, (0, 1, 0), viewAngle, aspect)
                    globalButterflyCam = (f, c, (0, 1, 0))

            if buccalCapture:
                s45 = math.sin(math.radians(45)); c45 = math.cos(math.radians(45))
                all_lat_pts = []
                for tp in timepoints:
                    for m in ([tp.upperModel, tp.lowerModel]
                              + ([tp.upperCast, tp.lowerCast] if buccalShowCast else [])):
                        if m: all_lat_pts.append(vtk_to_numpy(m.GetPolyData().GetPoints().GetData()))
                if all_lat_pts:
                    all_lat = np.vstack(all_lat_pts)
                    ctr = (all_lat.min(axis=0) + all_lat.max(axis=0)) / 2
                    cx, cy, cz = ctr
                    foc_rough = tuple(ctr)
                    views_rough = [
                        ("anterior",      (cx,          cy + D,      cz)),
                        ("posterior",     (cx,          cy - D,      cz)),
                        ("left",          (cx - D,      cy,          cz)),
                        ("right",         (cx + D,      cy,          cz)),
                        ("oblique_left",  (cx - D*s45,  cy + D*c45,  cz)),
                        ("oblique_right", (cx + D*s45,  cy + D*c45,  cz)),
                    ]
                    globalLateralCams = {}
                    for suffix, cam_r in views_rough:
                        f, c = self._fitCameraToMeshPoints(
                            all_lat, cam_r, foc_rough, (0, 0, 1), viewAngle, aspect)
                        globalLateralCams[suffix] = (f, c, (0, 0, 1))

        print(f"\n=== Screenshots → {outputDir} ===")
        for tp in timepoints:
            upperMap = slicer.mrmlScene.GetFirstNodeByName(
                f"{tp.upperModel.GetName()}_distance"
            )
            lowerMap = slicer.mrmlScene.GetFirstNodeByName(
                f"{tp.lowerModel.GetName()}_distance"
            )

            if occlusalCapture:
                # Upper jaw: camera from inferior (looking +Z upward)
                focal, camPos, viewUp = self._setupViewForModel(
                    tp.upperModel, upperMap, threeDView, lookFromInferior=True,
                    size=screenshotSize,
                    castModel=tp.upperCast if occlusalShowCast else None
                )
                if globalUpperCam:
                    focal, camPos, viewUp = globalUpperCam
                self._setColorLegendVisibility(upperMap, occlusalShowLegend)
                path = os.path.join(outputDir, f"{tp.label}_upper.png")
                self._captureTransparent(threeDView, path, size=screenshotSize,
                                         focal=focal, camPos=camPos, viewUp=viewUp)
                print(f"  {tp.label}_upper.png")

                # Lower jaw: camera from superior (looking −Z downward)
                focal, camPos, viewUp = self._setupViewForModel(
                    tp.lowerModel, lowerMap, threeDView, lookFromInferior=False,
                    size=screenshotSize,
                    castModel=tp.lowerCast if occlusalShowCast else None
                )
                if globalLowerCam:
                    focal, camPos, viewUp = globalLowerCam
                self._setColorLegendVisibility(lowerMap, occlusalShowLegend)
                path = os.path.join(outputDir, f"{tp.label}_lower.png")
                self._captureTransparent(threeDView, path, size=screenshotSize,
                                         focal=focal, camPos=camPos, viewUp=viewUp)
                print(f"  {tp.label}_lower.png")

            if butterflyCapture and upperMap and lowerMap:
                path = os.path.join(outputDir, f"{tp.label}_butterfly.png")
                self._takeButterflyScreenshot(
                    tp, upperMap, lowerMap, threeDView, path,
                    size=screenshotSize, precomputedCamera=globalButterflyCam,
                    hinge=sharedButterflyHinge,
                    showCast=butterflyShowCast, showLegend=butterflyShowLegend
                )
                print(f"  {tp.label}_butterfly.png")

            if buccalCapture:
                self._takeLateralViews(
                    tp, upperMap, lowerMap, threeDView, outputDir,
                    size=screenshotSize, showCast=buccalShowCast,
                    showColorLegend=buccalShowLegend,
                    precomputedCameras=globalLateralCams
                )

        self._restoreAllVisibility(origVisibility)
        for _item, _vis in origShVisibility.items():
            shNode.SetItemDisplayVisibility(_item, min(1, _vis))
        print("Done.")

    def _setColorLegendVisibility(self, distMapNode, visible):
        """Show or hide the color legend associated with *distMapNode*."""
        if distMapNode is None:
            return
        clNode = slicer.mrmlScene.GetFirstNodeByName(
            f"{distMapNode.GetName()} color legend"
        )
        if clNode is not None:
            clNode.SetVisibility(visible)

    def _takeLateralViews(self, tp, upperMap, lowerMap, threeDView, outputDir,
                          size=(500, 500), showCast=True, showColorLegend=False,
                          precomputedCameras=None):
        """Five lateral views of both jaws in occlusion.

        Camera directions in RAS (R=right, A=anterior, S=superior):
          anterior      – from patient's front, looking +A
          left          – from patient's left,  looking +R
          right         – from patient's right, looking −R
          oblique_left  – 45° between anterior and left
          oblique_right – 45° between anterior and right
        ViewUp is always (0, 0, 1) = Superior for all lateral shots.
        """
        import os, math

        # Show arches + distance maps (+ cast bases if requested); hide everything else
        for i in range(slicer.mrmlScene.GetNumberOfNodesByClass("vtkMRMLModelNode")):
            n  = slicer.mrmlScene.GetNthNodeByClass(i, "vtkMRMLModelNode")
            dn = n.GetDisplayNode()
            if dn:
                dn.SetVisibility(False)
        cast_nodes = (tp.upperCast, tp.lowerCast) if showCast else ()
        for node in (tp.upperModel, tp.lowerModel) + cast_nodes + (upperMap, lowerMap):
            if node and node.GetDisplayNode():
                node.GetDisplayNode().SetVisibility(True)
        self._setColorLegendVisibility(upperMap, showColorLegend)
        self._setColorLegendVisibility(lowerMap, showColorLegend)

        # Combined bounds of arch + cast base (only cast if visible)
        disp = [m for m in (tp.upperModel, tp.lowerModel) + cast_nodes if m]
        disp_pts = np.vstack([vtk_to_numpy(m.GetPolyData().GetPoints().GetData())
                               for m in disp])
        mn, mx = disp_pts.min(axis=0), disp_pts.max(axis=0)
        cx = (mn[0] + mx[0]) / 2
        cy = (mn[1] + mx[1]) / 2
        cz = (mn[2] + mx[2]) / 2
        focal = (cx, cy, cz)
        viewUp = (0, 0, 1)
        D = 10000

        s45 = math.sin(math.radians(45))
        c45 = math.cos(math.radians(45))

        # (suffix, camera_position)
        # In RAS, +Y = Anterior (toward patient's face), so the anterior camera
        # sits at +Y and looks toward −Y to see the front teeth.
        views = [
            ("anterior",      (cx,            cy + D,        cz)),
            ("posterior",     (cx,            cy - D,        cz)),
            ("left",          (cx - D,        cy,            cz)),
            ("right",         (cx + D,        cy,            cz)),
            ("oblique_left",  (cx - D * s45,  cy + D * c45,  cz)),
            ("oblique_right", (cx + D * s45,  cy + D * c45,  cz)),
        ]

        # Per-timepoint camera computation (used when normalizeZoom is off)
        renderer  = threeDView.renderWindow().GetRenderers().GetFirstRenderer()
        viewAngle = renderer.GetActiveCamera().GetViewAngle()
        if precomputedCameras is None:
            pts_list = [vtk_to_numpy(m.GetPolyData().GetPoints().GetData())
                        for m in (tp.upperModel, tp.lowerModel) + cast_nodes if m]
            all_pts = np.vstack(pts_list)

        for suffix, camPos_rough in views:
            if precomputedCameras and suffix in precomputedCameras:
                adj_focal, adj_cam, viewUp = precomputedCameras[suffix]
            else:
                adj_focal, adj_cam = self._fitCameraToMeshPoints(
                    all_pts, camPos_rough, focal, viewUp, viewAngle,
                    aspect=size[0] / size[1]
                )
            path = os.path.join(outputDir, f"{tp.label}_{suffix}.png")
            self._captureTransparent(threeDView, path, size=size,
                                     focal=adj_focal, camPos=adj_cam, viewUp=viewUp)
            print(f"  {tp.label}_{suffix}.png")

    def _fitCameraToMeshPoints(self, pts, camPos, roughFocal, viewUp,
                               viewAngle_deg, aspect=1.0, margin=1.2):
        """Compute camera focal point and position that centre and zoom to *pts*.

        Projects all mesh vertices onto the view plane (defined by the direction
        camPos→roughFocal and viewUp), finds the projected bounding-box centre
        (adjusted focal) and half-extents, then derives the required camera
        distance analytically:

            dist = max(half_h / tan(vFOV/2),
                       half_w / (tan(vFOV/2) * aspect)) * margin

        This is independent of ResetCamera and correctly handles horseshoe
        shapes whose bounding-box centre lies in empty interior space.

        Returns (focal, camPos) as tuples ready to pass to the MRML camera node.
        """
        import math

        cam = np.array(camPos,     dtype=float)
        foc = np.array(roughFocal, dtype=float)
        up  = np.array(viewUp,     dtype=float)

        d = foc - cam;  d /= np.linalg.norm(d)
        r = np.cross(d, up);  r /= np.linalg.norm(r)
        u = np.cross(r, d);   u /= np.linalg.norm(u)

        rel      = pts - foc
        r_coords = rel @ r
        u_coords = rel @ u

        r_ctr = (r_coords.min() + r_coords.max()) / 2.0
        u_ctr = (u_coords.min() + u_coords.max()) / 2.0
        adj_focal = foc + r_ctr * r + u_ctr * u

        half_w = (r_coords.max() - r_coords.min()) / 2.0
        half_h = (u_coords.max() - u_coords.min()) / 2.0

        tan_h = math.tan(math.radians(viewAngle_deg / 2.0))
        dist  = max(half_h / tan_h,
                    half_w / (tan_h * aspect)) * margin

        adj_cam = adj_focal - dist * d
        return tuple(adj_focal), tuple(adj_cam)

    def _fitCamera(self, threeDView, cx, cy, cz, lookFromInferior, archModel, size=(1, 1)):
        """Compute occlusal camera parameters for *archModel*. Returns (focal, camPos, viewUp)."""
        renderer  = threeDView.renderWindow().GetRenderers().GetFirstRenderer()
        viewAngle = renderer.GetActiveCamera().GetViewAngle()

        D = 10000
        if lookFromInferior:
            rough_cam = (cx, cy, cz - D)
            viewUp    = (0, 1, 0)
        else:
            rough_cam = (cx, cy, cz + D)
            viewUp    = (0, -1, 0)

        pts = vtk_to_numpy(archModel.GetPolyData().GetPoints().GetData())
        focal, camPos = self._fitCameraToMeshPoints(
            pts, rough_cam, (cx, cy, cz), viewUp, viewAngle,
            aspect=size[0] / size[1]
        )
        return focal, camPos, viewUp

    def _setupViewForModel(self, archModel, distMapModel, threeDView, lookFromInferior,
                           size=(1, 1), castModel=None):
        """Hide everything, show archModel + distMapModel + optional castModel.
        Returns (focal, camPos, viewUp) — caller passes these to _captureTransparent."""
        for i in range(slicer.mrmlScene.GetNumberOfNodesByClass("vtkMRMLModelNode")):
            n = slicer.mrmlScene.GetNthNodeByClass(i, "vtkMRMLModelNode")
            dn = n.GetDisplayNode()
            if dn:
                dn.SetVisibility(False)
        for node in (archModel, distMapModel, castModel):
            if node and node.GetDisplayNode():
                node.GetDisplayNode().SetVisibility(True)

        bounds = [0.0] * 6
        archModel.GetRASBounds(bounds)
        cx = (bounds[0] + bounds[1]) / 2
        cy = (bounds[2] + bounds[3]) / 2
        cz = (bounds[4] + bounds[5]) / 2

        # Fit camera to arch + cast combined so the wider base is fully in frame
        pts_list = [vtk_to_numpy(archModel.GetPolyData().GetPoints().GetData())]
        if castModel:
            pts_list.append(vtk_to_numpy(castModel.GetPolyData().GetPoints().GetData()))
        all_pts = np.vstack(pts_list)

        renderer  = threeDView.renderWindow().GetRenderers().GetFirstRenderer()
        viewAngle = renderer.GetActiveCamera().GetViewAngle()
        D = 10000
        if lookFromInferior:
            rough_cam = (cx, cy, cz - D)
            viewUp    = (0, 1, 0)
        else:
            rough_cam = (cx, cy, cz + D)
            viewUp    = (0, -1, 0)

        focal, camPos = self._fitCameraToMeshPoints(
            all_pts, rough_cam, (cx, cy, cz), viewUp, viewAngle,
            aspect=size[0] / size[1]
        )
        return focal, camPos, viewUp

    def _takeButterflyScreenshot(self, tp, upperMap, lowerMap, threeDView, path,
                                  size=(500, 500), precomputedCamera=None, hinge=None,
                                  showCast=False, showLegend=True):
        """Rotate lower jaw 180° around the LR axis posterior to the upper jaw,
        take a combined screenshot, then undo."""
        if hinge is not None:
            cy_hinge, cz_hinge = hinge
        else:
            bounds = [0.0] * 6
            tp.upperModel.GetRASBounds(bounds)
            cy_hinge = bounds[2] - 1.0   # 1 mm posterior (Y_min of upper jaw)
            cz_hinge = bounds[4]          # inferior edge of upper jaw

        # 180° rotation around the LR (X) axis through (_, cy_hinge, cz_hinge):
        #   x' = x,  y' = 2*cy_hinge - y,  z' = 2*cz_hinge - z
        mat = vtk.vtkMatrix4x4()
        mat.SetElement(0, 0,  1)
        mat.SetElement(1, 1, -1);  mat.SetElement(1, 3, 2 * cy_hinge)
        mat.SetElement(2, 2, -1);  mat.SetElement(2, 3, 2 * cz_hinge)
        mat.SetElement(3, 3,  1)

        tfmNode = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLLinearTransformNode", "_butterfly_tmp"
        )
        tfmNode.SetMatrixTransformToParent(mat)
        lower_xfm_nodes = [tp.lowerModel, lowerMap]
        if showCast and tp.lowerCast:
            lower_xfm_nodes.append(tp.lowerCast)
        for node in lower_xfm_nodes:
            if node:
                node.SetAndObserveTransformNodeID(tfmNode.GetID())

        # Hide everything, then show arch + map nodes (+ casts if requested)
        for i in range(slicer.mrmlScene.GetNumberOfNodesByClass("vtkMRMLModelNode")):
            n = slicer.mrmlScene.GetNthNodeByClass(i, "vtkMRMLModelNode")
            if n.GetDisplayNode():
                n.GetDisplayNode().SetVisibility(False)
        show_nodes = [tp.upperModel, upperMap, tp.lowerModel, lowerMap]
        if showCast:
            show_nodes += [tp.upperCast, tp.lowerCast]
        for node in show_nodes:
            if node and node.GetDisplayNode():
                node.GetDisplayNode().SetVisibility(True)
        self._setColorLegendVisibility(upperMap, showLegend)
        self._setColorLegendVisibility(lowerMap, showLegend)

        # Build world-space point cloud: upper jaw as-is, lower jaw with butterfly transform
        pts_upper = vtk_to_numpy(tp.upperModel.GetPolyData().GetPoints().GetData())
        pts_lower = vtk_to_numpy(tp.lowerModel.GetPolyData().GetPoints().GetData())
        mat_np = np.array([[mat.GetElement(i, j) for j in range(4)] for i in range(4)])
        pts_lower_xfm = (mat_np @ np.hstack(
            [pts_lower, np.ones((len(pts_lower), 1))]
        ).T).T[:, :3]
        pts_list = [pts_upper, pts_lower_xfm]
        if showCast:
            if tp.upperCast:
                pts_list.insert(1, vtk_to_numpy(tp.upperCast.GetPolyData().GetPoints().GetData()))
            if tp.lowerCast:
                pts_lc = vtk_to_numpy(tp.lowerCast.GetPolyData().GetPoints().GetData())
                pts_list.append((mat_np @ np.hstack(
                    [pts_lc, np.ones((len(pts_lc), 1))]).T).T[:, :3])
        all_pts = np.vstack(pts_list)

        # Rough focal: centre of the combined axis-aligned bounding box
        rough_focal = all_pts.mean(axis=0)
        viewUp = (0, 1, 0)
        D = 10000
        rough_cam = rough_focal + np.array([0, 0, -D])

        if precomputedCamera:
            focal, camPos, viewUp = precomputedCamera
        else:
            renderer  = threeDView.renderWindow().GetRenderers().GetFirstRenderer()
            viewAngle = renderer.GetActiveCamera().GetViewAngle()
            focal, camPos = self._fitCameraToMeshPoints(
                all_pts, rough_cam, rough_focal, viewUp, viewAngle,
                aspect=size[0] / size[1]
            )
        self._captureTransparent(threeDView, path, size=size,
                                  focal=focal, camPos=camPos, viewUp=viewUp)

        # Undo: remove transform from all transformed lower nodes
        for node in lower_xfm_nodes:
            if node:
                node.SetAndObserveTransformNodeID(None)
        slicer.mrmlScene.RemoveNode(tfmNode)

    def _captureTransparent(self, threeDView, filePath, size=(500, 500),
                             focal=None, camPos=None, viewUp=None):
        """Capture the 3D view to a PNG with transparent background at *size* pixels.

        If focal/camPos/viewUp are provided they are applied to both the MRML
        camera node and the VTK camera immediately before renderWindow.Render(),
        after all other scene modifications, so nothing can override the zoom.
        """
        renderWindow = threeDView.renderWindow()
        renderer     = renderWindow.GetRenderers().GetFirstRenderer()
        viewNode     = threeDView.mrmlViewNode()

        # Save background via the MRML view node (authoritative source for Slicer)
        origBg1  = tuple(viewNode.GetBackgroundColor())
        origBg2  = tuple(viewNode.GetBackgroundColor2())

        # Temporarily resize the render window
        origSize = renderWindow.GetSize()
        renderWindow.SetSize(*size)

        # Enable alpha bit planes and depth peeling
        renderWindow.SetAlphaBitPlanes(1)
        renderWindow.SetMultiSamples(0)
        renderer.SetUseDepthPeeling(True)
        renderer.SetMaximumNumberOfPeels(100)
        renderer.SetOcclusionRatio(0.0)

        # Hide slice planes in the 3D view
        sliceVisibility = {}
        for name in slicer.app.layoutManager().sliceViewNames():
            sliceNode = slicer.app.layoutManager().sliceWidget(name).mrmlSliceNode()
            sliceVisibility[name] = (sliceNode.GetSliceVisible(), sliceNode.GetWidgetVisible())
            sliceNode.SetSliceVisible(False)
            sliceNode.SetWidgetVisible(False)

        # Black + alpha=0 background (both view node and renderer)
        viewNode.SetBackgroundColor(0.0, 0.0, 0.0)
        viewNode.SetBackgroundColor2(0.0, 0.0, 0.0)
        renderer.SetBackground(0.0, 0.0, 0.0)
        renderer.SetBackground2(0.0, 0.0, 0.0)
        renderer.SetGradientBackground(False)
        if hasattr(renderer, "SetBackgroundAlpha"):
            renderer.SetBackgroundAlpha(0.0)

        # Apply camera last — after all MRML modifications — so nothing overrides it.
        # Set both the MRML node (for Slicer tracking) and the VTK camera directly.
        if focal is not None:
            cameraNode = slicer.modules.cameras.logic().GetViewActiveCameraNode(
                threeDView.mrmlViewNode()
            )
            cameraNode.SetFocalPoint(*focal)
            cameraNode.SetPosition(*camPos)
            cameraNode.SetViewUp(*viewUp)
            cam_vtk = renderer.GetActiveCamera()
            cam_vtk.SetFocalPoint(*focal)
            cam_vtk.SetPosition(*camPos)
            cam_vtk.SetViewUp(*viewUp)
            # In orthographic (parallel) projection, zoom is controlled by
            # ParallelScale, not camera distance.  Convert: scale = dist*tan(fov/2).
            if cam_vtk.GetParallelProjection():
                import math
                cam_dist = np.linalg.norm(np.array(camPos) - np.array(focal))
                parallelScale = cam_dist * math.tan(math.radians(cam_vtk.GetViewAngle() / 2.0))
                cam_vtk.SetParallelScale(parallelScale)
            renderer.ResetCameraClippingRange()

        renderWindow.Render()

        wti = vtk.vtkWindowToImageFilter()
        wti.SetInput(renderWindow)
        wti.SetInputBufferTypeToRGBA()
        wti.ReadFrontBufferOff()
        wti.Update()

        writer = vtk.vtkPNGWriter()
        writer.SetFileName(filePath)
        writer.SetInputData(wti.GetOutput())
        writer.Write()

        # Restore slice plane visibility
        for name, (sv, wv) in sliceVisibility.items():
            sliceNode = slicer.app.layoutManager().sliceWidget(name).mrmlSliceNode()
            sliceNode.SetSliceVisible(sv)
            sliceNode.SetWidgetVisible(wv)

        # Restore background, window size, and render settings
        viewNode.SetBackgroundColor(*origBg1)
        viewNode.SetBackgroundColor2(*origBg2)
        renderWindow.SetSize(*origSize)
        renderWindow.SetAlphaBitPlanes(0)
        renderWindow.SetMultiSamples(4)
        if hasattr(renderer, "SetBackgroundAlpha"):
            renderer.SetBackgroundAlpha(1.0)
        threeDView.forceRender()

    def _restoreAllVisibility(self, origVisibility):
        for i in range(slicer.mrmlScene.GetNumberOfNodesByClass("vtkMRMLModelNode")):
            n = slicer.mrmlScene.GetNthNodeByClass(i, "vtkMRMLModelNode")
            dn = n.GetDisplayNode()
            if dn and n.GetID() in origVisibility:
                dn.SetVisibility(origVisibility[n.GetID()])

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
                target_poly=upper_poly,
                source_model=tp.lowerModel,
                zOffset=+zOffset,
                scalarRange=scalarRange,
                thresholdRange=thresholdRange,
            )
            self._createSingleOcclusionMap(
                source_poly=upper_poly,
                target_poly=lower_poly,
                source_model=tp.upperModel,
                zOffset=-zOffset,
                scalarRange=scalarRange,
                thresholdRange=thresholdRange,
            )
            for model in (tp.upperModel, tp.lowerModel):
                self.applyDentalCastMaterial(model)

        self.createCastModels(timepoints)
        print(f"Done. {len(timepoints) * 2} occlusion map(s) in scene.")

    def _createSingleOcclusionMap(self, source_poly, target_poly, source_model,
                                   zOffset, scalarRange, thresholdRange):
        dist = vtk.vtkDistancePolyDataFilter()
        dist.SetInputData(0, source_poly)
        dist.SetInputData(1, target_poly)
        dist.SignedDistanceOff()
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
        clNode.SetPosition(1.0,.5)

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
        self._writeSummaryRow(delta_rows, primaryTau)
        print(f"\nResults written to '{RESULT_VECTORS_TABLE}', '{RESULT_DELTAS_TABLE}', "
              f"and '{RESULT_SUMMARY_TABLE}'.")

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

    # ── Trimming ──────────────────────────────────────────────────────────

    @staticmethod
    def trimModelWithCurve(model_node, curve_node):
        """Clip model in-place to the XY-projected outline of a closed markup curve.

        vtkImplicitSelectionLoop projects every model vertex onto the plane of
        the loop, so the clip is a vertical extrusion of the drawn outline —
        independent of the Z level at which the user drew the curve.
        """
        if model_node is None or curve_node is None:
            return
        poly = model_node.GetPolyData()
        if poly is None or poly.GetNumberOfPoints() == 0:
            return

        world_pts = curve_node.GetCurvePointsWorld()
        if world_pts is None or world_pts.GetNumberOfPoints() < 3:
            slicer.util.warningDisplay("Curve has too few points — place more control points.")
            return

        # Project to Z=0 so the loop lies strictly in the XY plane.
        # vtkImplicitSelectionLoop then clips as a vertical extrusion (normal = Z).
        xy_pts = vtk.vtkPoints()
        xy_pts.SetNumberOfPoints(world_pts.GetNumberOfPoints())
        for i in range(world_pts.GetNumberOfPoints()):
            p = world_pts.GetPoint(i)
            xy_pts.SetPoint(i, p[0], p[1], 0.0)

        sel_loop = vtk.vtkImplicitSelectionLoop()
        sel_loop.SetLoop(xy_pts)

        clipper = vtk.vtkClipPolyData()
        clipper.SetInputData(poly)
        clipper.SetClipFunction(sel_loop)
        clipper.InsideOutOn()   # keep inside (negative) region
        clipper.Update()

        normals = vtk.vtkPolyDataNormals()
        normals.SetInputData(clipper.GetOutput())
        normals.ComputePointNormalsOn()
        normals.ComputeCellNormalsOff()
        normals.SplittingOff()
        normals.Update()

        model_node.SetAndObservePolyData(normals.GetOutput())

    # ── Display helpers ───────────────────────────────────────────────────

    @staticmethod
    def applyDentalCastMaterial(model_node):
        """Give a model node a plaster-cast look: warm white, matte surface."""
        if model_node is None:
            return
        model_node.CreateDefaultDisplayNodes()
        dn = model_node.GetDisplayNode()
        if dn is None:
            return
        dn.SetColor(*CAST_COLOR)
        dn.SetAmbient(CAST_AMBIENT)
        dn.SetDiffuse(CAST_DIFFUSE)
        dn.SetSpecular(CAST_SPECULAR)
        dn.SetPower(CAST_SPEC_POW)

    def createCastModels(self, timepoints, resample_walls=False):
        """Build (or rebuild) trimmed art-base nodes for all timepoints."""
        for tp in timepoints:
            if not tp.upperModel and not tp.lowerModel:
                continue
            # Compute combined XY envelope so both arches share one footprint
            raw_bounds = [float('inf'), float('-inf'), float('inf'), float('-inf')]
            for m in (tp.upperModel, tp.lowerModel):
                if m is None:
                    continue
                b = [0.0] * 6
                m.GetPolyData().GetBounds(b)
                raw_bounds[0] = min(raw_bounds[0], b[0])
                raw_bounds[1] = max(raw_bounds[1], b[1])
                raw_bounds[2] = min(raw_bounds[2], b[2])
                raw_bounds[3] = max(raw_bounds[3], b[3])
            if tp.upperModel:
                tp.upperCast = self._createCastNode(tp.upperModel, jaw='upper',
                                                    xy_bounds=raw_bounds, resample_walls=resample_walls)
            if tp.lowerModel:
                tp.lowerCast = self._createCastNode(tp.lowerModel, jaw='lower',
                                                    xy_bounds=raw_bounds, resample_walls=resample_walls)

    @staticmethod
    def _buildCastPolyData(poly_data, jaw='lower', prism_height=5.0, margin=2.5,
                           xy_bounds=None, resample_walls=False):
        """Build cast geometry: gingival walls + trimmed-cast base prism.

        Stage 1 – Walls: extrude the largest boundary loop straight to
          1 mm beyond the model's extreme vertex.
        Stage 2 – Base prism: footprint follows orthodontic trimming convention:
            upper jaw – pointed anterior (narrows to midline tip),
                        chamfered posterior-lateral corners
            lower jaw – flat anterior between canine positions,
                        chamfered posterior-lateral corners

        xy_bounds: [xmin,xmax,ymin,ymax] from combined upper+lower bounds so
          both arches share one identical footprint.

        jaw='upper' → walls/prism extend superior (+Z)
        jaw='lower' → walls/prism extend inferior (−Z)
        """
        # ── Model bounds (Z from individual model) ────────────────────────
        bounds = [0.0] * 6
        poly_data.GetBounds(bounds)

        if jaw == 'upper':
            z_wall = bounds[5] + 1.0
            z_face = z_wall + prism_height
        else:
            z_wall = bounds[4] - 1.0
            z_face = z_wall - prism_height

        # ── Footprint (shared XY envelope when xy_bounds is provided) ─────
        xb0, xb1, yb0, yb1 = (
            (xy_bounds[0], xy_bounds[1], xy_bounds[2], xy_bounds[3])
            if xy_bounds is not None
            else (bounds[0], bounds[1], bounds[2], bounds[3])
        )
        xmid   = 0.0
        xhalf  = max(abs(xb0), abs(xb1)) + margin
        ymin_b = yb0 - margin
        ymax_b = yb1 + margin

        distal_cham = xhalf * 0.28
        ant_taper   = (ymax_b - ymin_b) * 0.18   # set-back of the canine shoulder from the apex
        y_canine    = ymax_b - ant_taper
        x_canine    = xhalf * 0.62

        # The posterolateral extent is pushed further out than xhalf so that the
        # diagonal from xhalf_post (posterolateral) to x_canine (canine shoulder)
        # passes through xhalf at its midpoint, enclosing the molar-level walls.
        xhalf_post  = xhalf + (xhalf - x_canine - 10)  # mirror of the anterior narrowing

        arc_n = 24
        shape = [
            [xmid - xhalf_post + distal_cham, ymin_b               ],  # post chamfer left
            [xmid + xhalf_post - distal_cham, ymin_b               ],  # post chamfer right
            [xmid + xhalf_post,               ymin_b + distal_cham ],  # post-right (chamfer end)
            [xmid + x_canine,                 y_canine             ],  # canine-right
        ]

        if jaw == 'upper':
            shape.append([xmid, ymax_b])  # V-tip at midline
        else:
            # Parabolic arc; interior points only (endpoints are the canine vertices)
            for k in range(1, arc_n):
                x_norm = 1.0 - 2.0 * k / arc_n   # sweeps +1 → -1
                x = xmid + x_canine * x_norm
                y = y_canine + (ymax_b - y_canine) * (1.0 - x_norm ** 2)
                shape.append([x, y])

        shape += [
            [xmid - x_canine,              y_canine             ],  # canine-left
            [xmid - xhalf_post,            ymin_b + distal_cham ],  # post-left (chamfer end)
        ]

        shape_xy = np.array(shape)
        n_hex = len(shape_xy)

        # ── Extract boundary edges ────────────────────────────────────────
        bfe = vtk.vtkFeatureEdges()
        bfe.SetInputData(poly_data)
        bfe.BoundaryEdgesOn()
        bfe.FeatureEdgesOff()
        bfe.ManifoldEdgesOff()
        bfe.NonManifoldEdgesOff()
        bfe.Update()
        bpd = bfe.GetOutput()

        def _order_loops(pd):
            n_cells = pd.GetNumberOfCells()
            pts     = pd.GetPoints()
            if n_cells == 0 or pts is None:
                return []
            adj = {}
            for i in range(n_cells):
                c  = pd.GetCell(i)
                p0 = c.GetPointId(0)
                p1 = c.GetPointId(1)
                adj.setdefault(p0, []).append(p1)
                adj.setdefault(p1, []).append(p0)
            visited = set()
            loops   = []
            for start in adj:
                if start in visited:
                    continue
                loop = [start]
                visited.add(start)
                prev, cur = -1, start
                while True:
                    nxt = next(
                        (n for n in adj.get(cur, []) if n != prev and n not in visited),
                        None,
                    )
                    if nxt is None:
                        break
                    loop.append(nxt)
                    visited.add(nxt)
                    prev, cur = cur, nxt
                if len(loop) > 2:
                    loops.append(np.array([pts.GetPoint(i) for i in loop]))
            return loops

        def _resample_loop(pts_3d, spacing=1.5):
            """Resample a closed polygon to ~spacing mm between vertices."""
            closed = np.vstack([pts_3d, pts_3d[:1]])
            seg    = np.linalg.norm(np.diff(closed, axis=0), axis=1)
            cum    = np.concatenate([[0.0], np.cumsum(seg)])
            n      = max(8, int(round(cum[-1] / spacing)))
            t      = np.linspace(0.0, cum[-1], n, endpoint=False)
            return np.column_stack([np.interp(t, cum, closed[:, k]) for k in range(3)])

        loops = _order_loops(bpd)

        # ── Stage 1: walls from gingival boundary to z_wall ──────────────
        wall_pd = vtk.vtkPolyData()
        if loops:
            ring  = _resample_loop(max(loops, key=len)) if resample_walls else max(loops, key=len)
            n_v   = len(ring)
            top_ring = ring.copy()
            top_ring[:, 2] = z_wall

            # Signed area of the boundary loop projected to XY.
            # Positive = CCW when viewed from +Z.
            xy = ring[:, :2]
            signed_area = 0.5 * (
                np.sum(xy[:-1, 0] * xy[1:, 1] - xy[1:, 0] * xy[:-1, 1])
                + (xy[-1, 0] * xy[0, 1] - xy[0, 0] * xy[-1, 1])
            )
            loop_ccw = signed_area > 0

            # Upper jaw extrudes +Z: CCW loop → outward with standard winding.
            # Lower jaw extrudes -Z: CCW loop → inward → must flip.
            flip = loop_ccw if jaw == 'lower' else not loop_ccw
            wall_tris = [(0, 2, 1), (0, 3, 2)] if flip else [(0, 1, 2), (0, 2, 3)]

            wpts   = vtk.vtkPoints()
            wcells = vtk.vtkCellArray()
            arch_ids = [wpts.InsertNextPoint(*p) for p in ring]
            top_ids  = [wpts.InsertNextPoint(*p) for p in top_ring]
            for i in range(n_v):
                j   = (i + 1) % n_v
                idx = [arch_ids[i], arch_ids[j], top_ids[j], top_ids[i]]
                for ti in wall_tris:
                    t = vtk.vtkTriangle()
                    for k, v in enumerate(ti):
                        t.GetPointIds().SetId(k, idx[v])
                    wcells.InsertNextCell(t)
            wall_pd.SetPoints(wpts)
            wall_pd.SetPolys(wcells)

        # Smooth normals for the organic wall surface
        wall_nrmls = vtk.vtkPolyDataNormals()
        wall_nrmls.SetInputData(wall_pd)
        wall_nrmls.SplittingOn()
        wall_nrmls.SetFeatureAngle(60.0)
        wall_nrmls.Update()

        # ── Stage 2: closed 3-D hex prism with flat (per-face) shading ───
        # Build as a single watertight solid so AutoOrientNormals works.
        hex_pts   = vtk.vtkPoints()
        hex_cells = vtk.vtkCellArray()
        # 6 arch-facing vertices + 6 exterior vertices
        w_ids = [hex_pts.InsertNextPoint(x, y, z_wall) for x, y in shape_xy]
        f_ids = [hex_pts.InsertNextPoint(x, y, z_face) for x, y in shape_xy]

        def _cap_polygon(ids):
            pg = vtk.vtkPolygon()
            pg.GetPointIds().SetNumberOfIds(len(ids))
            for k, pid in enumerate(ids):
                pg.GetPointIds().SetId(k, pid)
            hex_cells.InsertNextCell(pg)

        _cap_polygon(w_ids)          # arch-facing cap
        _cap_polygon(f_ids[::-1])    # exterior cap (reversed = outward normal)

        for i in range(n_hex):
            j = (i + 1) % n_hex
            # Standard winding for hex CCW from above + upper jaw (+Z)
            # Lower jaw needs flip because dz is negative
            if jaw == 'lower':
                quad_ids = [w_ids[i], w_ids[j], f_ids[j], f_ids[i]]
                quad_tris = [(0, 2, 1), (0, 3, 2)]
            else:
                quad_ids = [w_ids[i], w_ids[j], f_ids[j], f_ids[i]]
                quad_tris = [(0, 1, 2), (0, 2, 3)]
            for ti in quad_tris:
                t = vtk.vtkTriangle()
                for k, v in enumerate(ti):
                    t.GetPointIds().SetId(k, quad_ids[v])
                hex_cells.InsertNextCell(t)

        hex_solid = vtk.vtkPolyData()
        hex_solid.SetPoints(hex_pts)
        hex_solid.SetPolys(hex_cells)

        tri_hex = vtk.vtkTriangleFilter()
        tri_hex.SetInputData(hex_solid)
        tri_hex.Update()

        clean_hex = vtk.vtkCleanPolyData()
        clean_hex.SetInputData(tri_hex.GetOutput())
        clean_hex.SetTolerance(0.01)
        clean_hex.Update()

        # Per-face (flat) normals: feature angle near 0 splits every edge
        hex_nrmls = vtk.vtkPolyDataNormals()
        hex_nrmls.SetInputData(clean_hex.GetOutput())
        hex_nrmls.ConsistencyOn()
        hex_nrmls.AutoOrientNormalsOn()
        hex_nrmls.SplittingOn()
        hex_nrmls.SetFeatureAngle(1.0)
        hex_nrmls.Update()

        # ── Combine walls + prism ─────────────────────────────────────────
        final = vtk.vtkAppendPolyData()
        final.AddInputData(wall_nrmls.GetOutput())
        final.AddInputData(hex_nrmls.GetOutput())
        final.Update()

        return final.GetOutput()

    def _createCastNode(self, source_model, jaw='lower', xy_bounds=None, resample_walls=False):
        """Build (or rebuild) a closed cast model node from an open IOS arch."""
        name = f"{source_model.GetName()}_cast"
        node = slicer.mrmlScene.GetFirstNodeByName(name)
        if node is None:
            node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLModelNode", name)
        node.SetAndObservePolyData(
            self._buildCastPolyData(source_model.GetPolyData(), jaw=jaw,
                                    xy_bounds=xy_bounds, resample_walls=resample_walls)
        )
        node.CreateDefaultDisplayNodes()
        self.applyDentalCastMaterial(node)
        # Place next to the source model in the subject hierarchy
        shNode = slicer.vtkMRMLSubjectHierarchyNode.GetSubjectHierarchyNode(slicer.mrmlScene)
        src_item  = shNode.GetItemByDataNode(source_model)
        cast_item = shNode.GetItemByDataNode(node)
        if src_item and cast_item:
            shNode.SetItemParent(cast_item, shNode.GetItemParent(src_item))
        return node

    # ── Report generation ─────────────────────────────────────────────────

    def generateReport(self, timepoints, screenshotDir):
        """Render report_template.html via Jinja2 and convert to PDF via WeasyPrint.

        Saves occlusion_analysis_report.html (always) and .pdf (if WeasyPrint
        succeeds) into screenshotDir. Returns (html_path, generated_paths).
        """
        import os

        try:
            import weasyprint
        except ImportError:
            slicer.util.pip_install("weasyprint")
            import weasyprint

        import jinja2

        module_dir = os.path.dirname(os.path.abspath(__file__))
        template_path = os.path.join(module_dir, "Resources", "report_template.html")
        with open(template_path, encoding="utf-8") as f:
            template_src = f.read()

        context = self._prepareReportContext(timepoints, screenshotDir)
        env = jinja2.Environment(loader=jinja2.BaseLoader(), autoescape=False)
        html = env.from_string(template_src).render(**context)

        html_path = os.path.join(screenshotDir, "occlusion_analysis_report.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)

        generated = [html_path]
        try:
            pdf_path = os.path.join(screenshotDir, "occlusion_analysis_report.pdf")
            weasyprint.HTML(string=html, base_url=screenshotDir).write_pdf(pdf_path)
            generated.append(pdf_path)
            slicer.util.infoDisplay(f"Report PDF written: {pdf_path}", autoCloseMsec=2000)
        except Exception as exc:
            slicer.util.warningDisplay(f"WeasyPrint PDF conversion failed:\n{exc}")

        return html_path, generated

    def _prepareReportContext(self, timepoints, screenshotDir):
        import os, base64, datetime

        LATERAL = ["oblique_left", "anterior", "oblique_right",
                   "left", "posterior", "right"]
        OCC     = ["upper", "butterfly", "lower"]

        def load_img(label, view):
            path = os.path.join(screenshotDir, f"{label}_{view}.png")
            if not os.path.isfile(path):
                return None
            with open(path, "rb") as f:
                data = base64.b64encode(f.read()).decode("ascii")
            return f"data:image/png;base64,{data}"

        tp_data = []
        for tp in timepoints:
            lbl = tp.label
            images = {v: load_img(lbl, v) for v in LATERAL + OCC}
            tp_data.append({
                "label":       lbl,
                "has_lateral": any(images[v] for v in LATERAL),
                "has_occ":     any(images[v] for v in OCC),
                "images":      images,
            })

        def robust_sym(val):
            return "✓" if str(val).strip().lower() == "true" else "✗"

        vec_rows = []
        for r in self._tableNodeToRows(RESULT_VECTORS_TABLE):
            vec_rows.append({
                "timepoint":   r["timepoint"],
                "tau_mm":      f"{float(r['tau_mm']):.2f}",
                "OCA_mm2":     f"{float(r['OCA_mm2']):.2f}",
                "OCN":         f"{float(r['OCN']):.0f}",
                "OCN_cluster": f"{float(r['OCN_cluster']):.0f}",
                "OAS":         f"{float(r['OAS']):.4f}",
            })

        delta_rows = []
        for r in self._tableNodeToRows(RESULT_DELTAS_TABLE):
            verdict = str(r["verdict"]).strip()
            delta_rows.append({
                "comparison":   r["comparison"],
                "tau_mm":       f"{float(r['tau_mm']):.2f}",
                "dOCA_mm2":     f"{float(r['dOCA_mm2']):.2f}",
                "dOCN":         f"{float(r['dOCN']):.0f}",
                "dOAS":         f"{float(r['dOAS']):.4f}",
                "dOCA_robust":  robust_sym(r["dOCA_robust"]),
                "dOCN_robust":  robust_sym(r["dOCN_robust"]),
                "dOAS_robust":  robust_sym(r["dOAS_robust"]),
                "verdict":      verdict,
                "verdict_class": f"verdict-{verdict.lower()}",
            })

        return {
            "date":       datetime.date.today().isoformat(),
            "timepoints": tp_data,
            "vec_rows":   vec_rows,
            "delta_rows": delta_rows,
        }

    def _tableNodeToRows(self, name):
        """Return table node contents as list of dicts (str for all values)."""
        node = slicer.mrmlScene.GetFirstNodeByName(name)
        if node is None:
            return []
        t = node.GetTable()
        n_rows = t.GetNumberOfRows()
        n_cols = t.GetNumberOfColumns()
        if n_rows == 0 or n_cols == 0:
            return []
        cols = [t.GetColumn(i) for i in range(n_cols)]
        result = []
        for r in range(n_rows):
            result.append({c.GetName(): c.GetValue(r) for c in cols})
        return result

    def _writeSummaryRow(self, delta_rows, primaryTau):
        """Wide-format table: one row, one column group per timepoint pair (primary τ only).

        Column names: {A}-{B}_dOCA_mm2, {A}-{B}_dOCN, {A}-{B}_dOAS, {A}-{B}_verdict
        One row per case → easy to paste into a master results spreadsheet.
        """
        # Collect primary-τ rows in pair order (insertion order preserved, py3.7+)
        prim = {}
        for row in delta_rows:
            if abs(row["tau"] - primaryTau) < 1e-9:
                prim.setdefault(row["comparison"], row)

        col_defs = []
        for comparison in prim:
            prefix = comparison.replace(" → ", "-")
            col_defs += [
                (f"{prefix}_dOCA_mm2", False),
                (f"{prefix}_dOCN",     False),
                (f"{prefix}_dOAS",     False),
                (f"{prefix}_verdict",  True),
            ]

        node, cols = self._makeOrResetTable(RESULT_SUMMARY_TABLE, col_defs)
        node.GetTable().SetNumberOfRows(1)
        for comparison, row in prim.items():
            prefix = comparison.replace(" → ", "-")
            cols[f"{prefix}_dOCA_mm2"].SetValue(0, row["dOCA"])
            cols[f"{prefix}_dOCN"].SetValue(0, float(row["dOCN"]))
            cols[f"{prefix}_dOAS"].SetValue(0, row["dOAS"])
            cols[f"{prefix}_verdict"].SetValue(0, row["verdict"])
        node.Modified()


# ──────────────────────────────────────────────────────────────────────────────

def _sign(x):
    return (x > 0) - (x < 0)
