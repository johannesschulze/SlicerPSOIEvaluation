"""
OrbitalVolumeWorkflowModule
============================
Slicer-Modul mit grafischer Oberfläche für:
  1. Erstellung der Orbita-Eingangsebene aus einer Closed Curve
     (Logik aus orbital_surface_from_curve.py)
  2. Segmentierung des intraorbitalen Volumens via Fast Marching
     (Logik aus intraorbital_volume_segmentation.py)

Unterstützt linke und rechte Orbita getrennt.
"""

import logging
from typing import Optional

import numpy as np
import vtk

from __main__ import slicer
import qt

from slicer.i18n import tr as _
from slicer.i18n import translate
from slicer.ScriptedLoadableModule import (
    ScriptedLoadableModule,
    ScriptedLoadableModuleLogic,
    ScriptedLoadableModuleTest,
    ScriptedLoadableModuleWidget,
)
from slicer.parameterNodeWrapper import parameterNodeWrapper
from slicer.util import VTKObservationMixin
from slicer import (
    vtkMRMLScalarVolumeNode, vtkMRMLModelNode, vtkMRMLSegmentationNode,
    vtkMRMLMarkupsFiducialNode, vtkMRMLLinearTransformNode,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Modul-Klasse
# ═══════════════════════════════════════════════════════════════════════════════

class OrbitalVolumeWorkflowModule(ScriptedLoadableModule):

    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = _("OrbitalVolumeWorkflowModule")
        self.parent.categories = [translate("qSlicerAbstractCoreModule", "PSOI Evaluation")]
        self.parent.dependencies = []
        self.parent.contributors = ["Johannes Schulze (Bundeswehrkrankenhaus Ulm)"]
        self.parent.helpText = _(
            "This module creates an entry plane for the orbit from a closed curve "
            "and subsequently segments the intraorbital volume using Fast Marching. "
            "The left and right orbits are managed separately."
        )
        self.parent.acknowledgementText = _(
            "Developed by Johannes Schulze (Bundeswehrkrankenhaus Ulm) "
            "without external funding."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Parameter-Node
# ═══════════════════════════════════════════════════════════════════════════════

@parameterNodeWrapper
class OrbitalVolumeWorkflowModuleParameterNode:
    ctVolume:              vtkMRMLScalarVolumeNode
    planeModelLeft:        vtkMRMLModelNode
    planeModelRight:       vtkMRMLModelNode
    segmentationNodeLeft:  vtkMRMLSegmentationNode
    segmentationNodeRight: vtkMRMLSegmentationNode
    seedNodeLeft:          vtkMRMLMarkupsFiducialNode
    seedNodeRight:         vtkMRMLMarkupsFiducialNode
    step:             int  = 0
    sideIsLeft:       bool = True
    rightSideVisited: bool = False
    # Segmentierungsparameter – separat für linke und rechte Seite
    huMinLeft:          int   = -200
    huMinRight:         int   = -200
    huMaxLeft:          int   =  300
    huMaxRight:         int   =  300
    autoSeedLeft:       bool  = True
    autoSeedRight:      bool  = True
    seedOffsetLeft:     float = 10.0
    seedOffsetRight:    float = 10.0
    orbitalDepthLeft:   float = 55.0
    orbitalDepthRight:  float = 55.0
    radiusMarginLeft:   float =  5.0
    radiusMarginRight:  float =  5.0
    stoppingValueLeft:  float = 25.0
    stoppingValueRight: float = 25.0
    speedSigmaLeft:     float = 70.0
    speedSigmaRight:    float = 70.0
    posteriorBoostLeft:  float = 1.5
    posteriorBoostRight: float = 1.5
    showSeedLeft:           bool  = True
    showSeedRight:          bool  = True
    # 0 = manual, 1 = mirror contralateral, 2 = model-based
    seedModeLeft:            int   = 0
    seedModeRight:           int   = 0
    modelSeedNodeLeft:       vtkMRMLSegmentationNode
    modelSeedNodeRight:      vtkMRMLSegmentationNode
    modelSeedTransformLeft:  vtkMRMLLinearTransformNode
    modelSeedTransformRight: vtkMRMLLinearTransformNode
    posteriorMarkupLeft:     vtkMRMLMarkupsFiducialNode
    posteriorMarkupRight:   vtkMRMLMarkupsFiducialNode
    removeSatellitesLeft:   bool  = True
    removeSatellitesRight:  bool  = True
    satelliteDiamLeft:      float = 3.0
    satelliteDiamRight:     float = 3.0


# ═══════════════════════════════════════════════════════════════════════════════
# Widget
# ═══════════════════════════════════════════════════════════════════════════════

class OrbitalVolumeWorkflowModuleWidget(ScriptedLoadableModuleWidget, VTKObservationMixin):

    def __init__(self, parent=None) -> None:
        ScriptedLoadableModuleWidget.__init__(self, parent)
        VTKObservationMixin.__init__(self)
        self.logic = None
        self._parameterNode = None
        self._parameterNodeGuiTag = None
        # Per-ParameterNode-Zustand (gespeichert nach Node-ID, damit Wechsel verlustfrei sind)
        self._statePerPN = {}   # {pn_id: {curveNodes, surfaceTexts, segTexts, segNodes, segIds}}
        # Aktiv genutzter Per-Seite-Zustand (True = links, False = rechts)
        self._curveNodes   = {True: None, False: None}
        self._surfaceTexts = {True: "",   False: ""}
        self._segTexts     = {True: "",   False: ""}
        self._vrDisplayNode = None
        # Segmentierungs-Nodes und Observer für automatische Volumen-Neuberechnung
        self._segNodes     = {True: None, False: None}
        self._segIds       = {True: None, False: None}
        self._segObservers             = {True: None, False: None}
        self._posteriorMarkupObservers = {True: None, False: None}
        self._volumeUpdateSide = None
        self._volumeUpdateTimer = qt.QTimer()
        self._volumeUpdateTimer.setSingleShot(True)
        self._volumeUpdateTimer.setInterval(1500)
        self._volumeUpdateTimer.connect('timeout()', self._doVolumeUpdate)

    # ------------------------------------------------------------------
    # Lebenszyklus
    # ------------------------------------------------------------------

    def setup(self) -> None:
        # Load translation for current locale
        _translator = qt.QTranslator()
        _locale = qt.QLocale.system().name()  # e.g. "de_DE"
        _qm = self.resourcePath(f"Translations/OrbitalVolumeWorkflowModule_{_locale}.qm")
        if _translator.load(_qm):
            qt.QCoreApplication.installTranslator(_translator)
        else:
            # Try language-only fallback (e.g. "de" from "de_DE")
            _qm_lang = self.resourcePath(
                f"Translations/OrbitalVolumeWorkflowModule_{_locale.split('_')[0]}.qm"
            )
            if _translator.load(_qm_lang):
                qt.QCoreApplication.installTranslator(_translator)

        ScriptedLoadableModuleWidget.setup(self)

        uiWidget = slicer.util.loadUI(self.resourcePath("UI/OrbitalVolumeWorkflowModule.ui"))
        self.layout.addWidget(uiWidget)
        self.ui = slicer.util.childWidgetVariables(uiWidget)
        uiWidget.setMRMLScene(slicer.mrmlScene)

        self.logic = OrbitalVolumeWorkflowModuleLogic()

        self.addObserver(slicer.mrmlScene, slicer.mrmlScene.StartCloseEvent, self.onSceneStartClose)
        self.addObserver(slicer.mrmlScene, slicer.mrmlScene.EndCloseEvent,   self.onSceneEndClose)

        # ParameterNode-Selektor: erst Filter setzen, dann Scene zuweisen, damit
        # der Filter beim initialen Scene-Scan bereits aktiv ist.
        self.ui.parameterNodeSelector.addAttribute(
            "vtkMRMLScriptedModuleNode", "ModuleName", self.moduleName
        )
        self.ui.parameterNodeSelector.setMRMLScene(slicer.mrmlScene)
        self.ui.parameterNodeSelector.connect(
            "currentNodeChanged(vtkMRMLNode*)", self.setParameterNode
        )

        # Workflow-Buttons
        self.ui.createSurfaceButton.connect("clicked(bool)",    self.onCreateSurfaceButton)
        self.ui.segmentVolumeButton.connect("clicked(bool)",    self.onSegmentVolumeButton)
        self.ui.autoSeedCheckBox.connect("toggled(bool)",       self.onAutoSeedToggled)
        self.ui.stepsToolbox.connect("currentChanged(int)",     self.onStepsToolboxCurrentChanged)
        self.ui.placePosteriorMarkupButton.connect("clicked(bool)", self.onPlacePosteriorMarkupButton)
        self.ui.removeSatellitesCheckBox.connect(
            "toggled(bool)", lambda checked: self.ui.satelliteDiamSpinBox.setEnabled(checked)
        )
        for rb in [self.ui.rbSeedManual, self.ui.rbSeedContralateral, self.ui.rbSeedModelBased]:
            rb.connect("toggled(bool)", lambda checked: checked and self.onSeedModeChanged())
        self.ui.modelSeedSelector.connect(
            "currentNodeChanged(vtkMRMLNode*)", self.onModelSeedChanged
        )
        self.ui.positionModelButton.connect("clicked(bool)", self.onPositionModelButton)

        # Seiten-Buttons
        self.ui.btnSideLeft.connect("clicked()",  lambda: self.onSideChanged(True))
        self.ui.btnSideRight.connect("clicked()", lambda: self.onSideChanged(False))

        # Selektoren manuell beobachten (kein SlicerParameterName für seitenspezifische Nodes)
        self.ui.curveSelector.connect(
            "currentNodeChanged(vtkMRMLNode*)", self.onCurveChanged)
        self.ui.planeModelSelector.connect(
            "currentNodeChanged(vtkMRMLNode*)", self.onPlaneModelChanged)

        # CT-Volume → Volume-Rendering; HU-Shift-Slider; Toggle-Button
        self.ui.ctVolumeSelector.connect(
            "currentNodeChanged(vtkMRMLNode*)", self.onCTVolumeChanged)
        self.ui.huShiftSlider.connect("valueChanged(double)", self.onHUShiftChanged)
        self.ui.toggleVolumeRenderingButton.connect(
            "toggled(bool)", self.onToggleVolumeRendering)

        # Neue-Kurve-Button
        self.ui.createCurveButton.connect("clicked(bool)", self.onCreateCurveButton)

        # Segment-Editor-Button (nach Segmentierung aktiviert)
        self.ui.openSegmentEditorButton.connect("clicked(bool)", self.onOpenSegmentEditorButton)

        self.initializeParameterNode()

    def cleanup(self) -> None:
        self._volumeUpdateTimer.stop()
        for side in [True, False]:
            node = self._segNodes.get(side)
            obs  = self._segObservers.get(side)
            if node is not None and obs is not None:
                node.RemoveObserver(obs)
        if self._parameterNode is not None:
            for side, attr in [(True, "posteriorMarkupLeft"), (False, "posteriorMarkupRight")]:
                markup = getattr(self._parameterNode, attr, None)
                obs    = self._posteriorMarkupObservers.get(side)
                if markup is not None and obs is not None:
                    markup.RemoveObserver(obs)
        self.removeObservers()

    def enter(self) -> None:
        self.initializeParameterNode()
        if self._parameterNode:
            self.ui.stepsToolbox.setCurrentIndex(self._parameterNode.step)
            isLeft = self._parameterNode.sideIsLeft
            self.ui.btnSideLeft.setChecked(isLeft)
            self.ui.btnSideRight.setChecked(not isLeft)
            self._refreshSideUI(isLeft)

    def exit(self) -> None:
        if self._parameterNode:
            self._parameterNode.disconnectGui(self._parameterNodeGuiTag)
            self._parameterNodeGuiTag = None

    def onSceneStartClose(self, caller, event) -> None:
        self.setParameterNode(None)

    def onSceneEndClose(self, caller, event) -> None:
        if self.parent.isEntered:
            self.initializeParameterNode()

    # ------------------------------------------------------------------
    # Parameter-Node
    # ------------------------------------------------------------------

    def initializeParameterNode(self) -> None:
        pn = self.logic.getParameterNode()
        # Direkt setzen – nicht auf das Combo-Box-Signal warten (Box kann beim
        # ersten Start leer/inaktiv sein, bevor ein Node existiert).
        self.setParameterNode(pn)
        # Combo Box synchronisieren ohne erneutes Signal
        wasBlocked = self.ui.parameterNodeSelector.blockSignals(True)
        self.ui.parameterNodeSelector.setCurrentNode(pn.parameterNode)
        self.ui.parameterNodeSelector.blockSignals(wasBlocked)

    def setParameterNode(self, inputParameterNode) -> None:
        # Eingabe kann ein roher vtkMRMLNode (aus Combo Box) oder bereits
        # ein gewrappter OrbitalVolumeWorkflowModuleParameterNode sein.
        if isinstance(inputParameterNode, slicer.vtkMRMLNode):
            # ModuleName-Attribut setzen, damit der Node im gefilterten Selector bleibt
            inputParameterNode.SetAttribute("ModuleName", self.moduleName)
            inputParameterNode = OrbitalVolumeWorkflowModuleParameterNode(inputParameterNode)

        # Gleicher unterliegender Node → nichts zu tun
        if (self._parameterNode is not None
                and inputParameterNode is not None
                and self._parameterNode.parameterNode is inputParameterNode.parameterNode):
            return

        # ---- Alten PN aufräumen ----
        if self._parameterNode is not None:
            old_id = self._parameterNode.parameterNode.GetID()
            # Per-PN-Zustand sichern
            self._statePerPN[old_id] = {
                "curveNodes":   dict(self._curveNodes),
                "surfaceTexts": dict(self._surfaceTexts),
                "segTexts":     dict(self._segTexts),
                "segNodes":     dict(self._segNodes),
                "segIds":       dict(self._segIds),
            }
            # Observer vom alten Seg-Nodes lösen
            for side in [True, False]:
                node = self._segNodes.get(side)
                obs  = self._segObservers.get(side)
                if node is not None and obs is not None:
                    node.RemoveObserver(obs)
            self._segObservers = {True: None, False: None}
            # Observer vom alten Posterior-Markup-Nodes lösen
            for side, attr in [(True, "posteriorMarkupLeft"), (False, "posteriorMarkupRight")]:
                markup = getattr(self._parameterNode, attr, None)
                obs    = self._posteriorMarkupObservers.get(side)
                if markup is not None and obs is not None:
                    markup.RemoveObserver(obs)
            self._posteriorMarkupObservers = {True: None, False: None}
            self._parameterNode.disconnectGui(self._parameterNodeGuiTag)
            self._parameterNodeGuiTag = None

        self._parameterNode = inputParameterNode

        if self._parameterNode is None:
            return

        # ---- Neuen PN aktivieren ----
        new_id = self._parameterNode.parameterNode.GetID()
        if new_id in self._statePerPN:
            s = self._statePerPN[new_id]
            self._curveNodes   = s["curveNodes"]
            self._surfaceTexts = s["surfaceTexts"]
            self._segTexts     = s["segTexts"]
            self._segNodes     = s["segNodes"]
            self._segIds       = s["segIds"]
        else:
            self._curveNodes   = {True: None, False: None}
            self._surfaceTexts = {True: "",   False: ""}
            self._segTexts     = {True: "",   False: ""}
            self._segNodes     = {True: None, False: None}
            self._segIds       = {True: None, False: None}

        # Observer an vorhandene Seg-Nodes des neuen PN hängen
        self._segObservers = {True: None, False: None}
        for isLeft, attr in [(True, "segmentationNodeLeft"), (False, "segmentationNodeRight")]:
            seg_node = getattr(self._parameterNode, attr, None)
            if seg_node is None:
                continue
            # Segment-ID aus dem Node holen (wird u.U. neu befüllt)
            seg_id = self._segIds.get(isLeft)
            if seg_id is None:
                seg = seg_node.GetSegmentation()
                for i in range(seg.GetNumberOfSegments()):
                    if seg.GetNthSegment(i).GetName() == "IntraorbitalVolume":
                        seg_id = seg.GetNthSegmentID(i)
                        self._segIds[isLeft] = seg_id
                        break
            if seg_id:
                self._segNodes[isLeft] = seg_node
                self._segObservers[isLeft] = seg_node.AddObserver(
                    vtk.vtkCommand.ModifiedEvent,
                    lambda c, e, side=isLeft: self._onSegmentModified(side),
                )

        # Observer an vorhandene Posterior-Markup-Nodes des neuen PN hängen
        for isLeft_pm, attr in [(True, "posteriorMarkupLeft"), (False, "posteriorMarkupRight")]:
            markup = getattr(self._parameterNode, attr, None)
            if markup is None:
                continue
            obs = markup.AddObserver(
                vtk.vtkCommand.ModifiedEvent,
                lambda c, e, side=isLeft_pm: self._onPosteriorMarkupModified(side),
            )
            self._posteriorMarkupObservers[isLeft_pm] = obs

        self._parameterNodeGuiTag = self._parameterNode.connectGui(self.ui)
        isLeft = self._parameterNode.sideIsLeft
        self.ui.btnSideLeft.setChecked(isLeft)
        self.ui.btnSideRight.setChecked(not isLeft)
        self._refreshSideUI(isLeft)

    # ------------------------------------------------------------------
    # Signalhandler – Seitenauswahl
    # ------------------------------------------------------------------

    def onSideChanged(self, isLeft: bool) -> None:
        if self._parameterNode:
            # Aktuelle Parameter der verlassenen Seite speichern
            self._saveParamsForSide(self._parameterNode.sideIsLeft)
            # Beim ersten Besuch der rechten Seite: linke Parameter übernehmen
            if not isLeft and not self._parameterNode.rightSideVisited:
                self._copyParamsToSide(fromLeft=True)
                self._parameterNode.rightSideVisited = True
            self._parameterNode.sideIsLeft = isLeft
        self.ui.btnSideLeft.setChecked(isLeft)
        self.ui.btnSideRight.setChecked(not isLeft)
        self._refreshSideUI(isLeft)

    def _refreshSideUI(self, isLeft: bool) -> None:
        """Aktualisiert Selektoren, Parameter-Widgets und Labels für die gewählte Seite."""
        self.ui.curveSelector.blockSignals(True)
        self.ui.curveSelector.setCurrentNode(self._curveNodes[isLeft])
        self.ui.curveSelector.blockSignals(False)

        self.ui.planeModelSelector.blockSignals(True)
        if self._parameterNode:
            plane = (self._parameterNode.planeModelLeft
                     if isLeft else self._parameterNode.planeModelRight)
            self.ui.planeModelSelector.setCurrentNode(plane)
        self.ui.planeModelSelector.blockSignals(False)

        self._loadParamsForSide(isLeft)
        self.ui.createSurfaceButton.setEnabled(self._curveNodes[isLeft] is not None)
        self.ui.surfaceResultLabel.setText(self._surfaceTexts[isLeft])
        self.ui.segmentationResultLabel.setText(self._segTexts[isLeft])

    def _saveParamsForSide(self, isLeft: bool) -> None:
        if self._parameterNode is None:
            return
        s = "Left" if isLeft else "Right"
        self._parameterNode.__setattr__(f"huMin{s}",        self.ui.huMinSpinBox.value)
        self._parameterNode.__setattr__(f"huMax{s}",        self.ui.huMaxSpinBox.value)
        self._parameterNode.__setattr__(f"autoSeed{s}",     self.ui.autoSeedCheckBox.isChecked())
        self._parameterNode.__setattr__(f"seedOffset{s}",   self.ui.seedOffsetSpinBox.value)
        self._parameterNode.__setattr__(f"orbitalDepth{s}", self.ui.orbitalDepthSpinBox.value)
        self._parameterNode.__setattr__(f"radiusMargin{s}", self.ui.radiusMarginSpinBox.value)
        self._parameterNode.__setattr__(f"stoppingValue{s}",self.ui.stoppingValueSpinBox.value)
        self._parameterNode.__setattr__(f"speedSigma{s}",   self.ui.speedSigmaSpinBox.value)
        self._parameterNode.__setattr__(f"posteriorBoost{s}",self.ui.posteriorBoostSpinBox.value)
        self._parameterNode.__setattr__(f"showSeed{s}", self.ui.showSeedCheckBox.isChecked())
        mode = (1 if self.ui.rbSeedContralateral.isChecked()
                else 2 if self.ui.rbSeedModelBased.isChecked() else 0)
        self._parameterNode.__setattr__(f"seedMode{s}", mode)
        if isLeft:
            self._parameterNode.modelSeedNodeLeft  = self.ui.modelSeedSelector.currentNode()
        else:
            self._parameterNode.modelSeedNodeRight = self.ui.modelSeedSelector.currentNode()
        self._parameterNode.__setattr__(f"removeSatellites{s}", self.ui.removeSatellitesCheckBox.isChecked())
        self._parameterNode.__setattr__(f"satelliteDiam{s}",    self.ui.satelliteDiamSpinBox.value)

    def _loadParamsForSide(self, isLeft: bool) -> None:
        if self._parameterNode is None:
            return
        s = "Left" if isLeft else "Right"
        self.ui.huMinSpinBox.setValue(        self._parameterNode.__getattribute__(f"huMin{s}"))
        self.ui.huMaxSpinBox.setValue(        self._parameterNode.__getattribute__(f"huMax{s}"))
        autoSeed = self._parameterNode.__getattribute__(f"autoSeed{s}")
        self.ui.autoSeedCheckBox.setChecked(autoSeed)
        self.ui.seedOffsetSpinBox.setValue(   self._parameterNode.__getattribute__(f"seedOffset{s}"))
        self.ui.seedOffsetSpinBox.setEnabled(not autoSeed)
        self.ui.orbitalDepthSpinBox.setValue( self._parameterNode.__getattribute__(f"orbitalDepth{s}"))
        self.ui.radiusMarginSpinBox.setValue( self._parameterNode.__getattribute__(f"radiusMargin{s}"))
        self.ui.stoppingValueSpinBox.setValue(self._parameterNode.__getattribute__(f"stoppingValue{s}"))
        self.ui.speedSigmaSpinBox.setValue(   self._parameterNode.__getattribute__(f"speedSigma{s}"))
        self.ui.posteriorBoostSpinBox.setValue(self._parameterNode.__getattribute__(f"posteriorBoost{s}"))
        self.ui.showSeedCheckBox.setChecked(self._parameterNode.__getattribute__(f"showSeed{s}"))
        # Restore seed-mode radio buttons without triggering onSeedModeChanged
        mode = self._parameterNode.__getattribute__(f"seedMode{s}")
        for rb in [self.ui.rbSeedManual, self.ui.rbSeedContralateral, self.ui.rbSeedModelBased]:
            rb.blockSignals(True)
        self.ui.rbSeedManual.setChecked(mode == 0)
        self.ui.rbSeedContralateral.setChecked(mode == 1)
        self.ui.rbSeedModelBased.setChecked(mode == 2)
        for rb in [self.ui.rbSeedManual, self.ui.rbSeedContralateral, self.ui.rbSeedModelBased]:
            rb.blockSignals(False)
        # Contralateral option only available when the other side is already segmented
        contra_node = (self._parameterNode.segmentationNodeRight
                       if isLeft else self._parameterNode.segmentationNodeLeft)
        self.ui.rbSeedContralateral.setEnabled(contra_node is not None)
        # Show/hide model-template row
        is_model = (mode == 2)
        self.ui.labelModelSeed.setVisible(is_model)
        self.ui.modelSeedWidget.setVisible(is_model)
        # Restore model seed selector
        model_seed = getattr(self._parameterNode, f"modelSeedNode{s}", None)
        self.ui.modelSeedSelector.blockSignals(True)
        self.ui.modelSeedSelector.setCurrentNode(model_seed)
        self.ui.modelSeedSelector.blockSignals(False)
        self.ui.positionModelButton.setEnabled(model_seed is not None)
        remove_sat = self._parameterNode.__getattribute__(f"removeSatellites{s}")
        self.ui.removeSatellitesCheckBox.setChecked(remove_sat)
        self.ui.satelliteDiamSpinBox.setValue(       self._parameterNode.__getattribute__(f"satelliteDiam{s}"))
        self.ui.satelliteDiamSpinBox.setEnabled(remove_sat)

    def _copyParamsToSide(self, fromLeft: bool) -> None:
        """Kopiert alle Segmentierungsparameter von einer Seite zur anderen."""
        if self._parameterNode is None:
            return
        src = "Left" if fromLeft else "Right"
        dst = "Right" if fromLeft else "Left"
        for param in ["huMin", "huMax", "autoSeed", "seedOffset", "orbitalDepth",
                      "radiusMargin", "stoppingValue", "speedSigma", "posteriorBoost",
                      "showSeed", "seedMode", "removeSatellites", "satelliteDiam"]:
            val = self._parameterNode.__getattribute__(f"{param}{src}")
            self._parameterNode.__setattr__(f"{param}{dst}", val)

    def onCurveChanged(self, node) -> None:
        isLeft = self._parameterNode.sideIsLeft if self._parameterNode else True
        self._curveNodes[isLeft] = node
        self.ui.createSurfaceButton.setEnabled(node is not None)

    def onPlaneModelChanged(self, node) -> None:
        if self._parameterNode is None:
            return
        if self._parameterNode.sideIsLeft:
            self._parameterNode.planeModelLeft = node
        else:
            self._parameterNode.planeModelRight = node

    def onCreateCurveButton(self) -> None:
        isLeft = self._parameterNode.sideIsLeft if self._parameterNode else True
        side_suffix = "L" if isLeft else "R"

        curve_node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsClosedCurveNode")
        curve_node.SetName(f"OrbitalRim_{side_suffix}")
        disp = curve_node.GetDisplayNode()
        if disp:
            disp.SetColor(0.0, 1.0, 0.0)
            disp.SetGlyphScale(3.0)

        self._curveNodes[isLeft] = curve_node
        self.ui.curveSelector.blockSignals(True)
        self.ui.curveSelector.setCurrentNode(curve_node)
        self.ui.curveSelector.blockSignals(False)
        self.ui.createSurfaceButton.setEnabled(True)

        # Zeichen-Modus aktivieren
        selectionNode = slicer.app.applicationLogic().GetSelectionNode()
        selectionNode.SetActivePlaceNodeID(curve_node.GetID())
        selectionNode.SetActivePlaceNodeClassName("vtkMRMLMarkupsClosedCurveNode")
        interactionNode = slicer.app.applicationLogic().GetInteractionNode()
        interactionNode.SetCurrentInteractionMode(
            slicer.vtkMRMLInteractionNode.Place
        )

    def onToggleVolumeRendering(self, checked: bool) -> None:
        if self._vrDisplayNode is not None:
            self._vrDisplayNode.SetVisibility(checked)
        self.ui.toggleVolumeRenderingButton.setText(_("3D on") if not checked else _("3D off"))

    def onCTVolumeChanged(self, volume_node) -> None:
        if volume_node is None:
            return
        self._applyVolumeRendering(volume_node)
        self._centerViewAnterior(volume_node)

    def onHUShiftChanged(self, shift_hu: float) -> None:
        if self._vrDisplayNode is None:
            return
        volPropNode = self._vrDisplayNode.GetVolumePropertyNode()
        if volPropNode is None:
            return

        vrLogic = slicer.modules.volumerendering.logic()
        presetNode = vrLogic.GetPresetByName("CT-Bone")
        if presetNode is None:
            return

        # Preset frisch kopieren, dann Shift anwenden
        volPropNode.Copy(presetNode)
        volProp = volPropNode.GetVolumeProperty()

        opFn = volProp.GetScalarOpacity()
        n = opFn.GetSize()
        op_nodes = []
        for i in range(n):
            v = [0.0] * 4
            opFn.GetNodeValue(i, v)
            op_nodes.append(v[:])
        opFn.RemoveAllPoints()
        for v in op_nodes:
            opFn.AddPoint(v[0] + shift_hu, v[1], v[2], v[3])

        colFn = volProp.GetRGBTransferFunction()
        n = colFn.GetSize()
        col_nodes = []
        for i in range(n):
            v = [0.0] * 6
            colFn.GetNodeValue(i, v)
            col_nodes.append(v[:])
        colFn.RemoveAllPoints()
        for v in col_nodes:
            colFn.AddRGBPoint(v[0] + shift_hu, v[1], v[2], v[3], v[4], v[5])

        volPropNode.Modified()

    def _applyVolumeRendering(self, volume_node) -> None:
        from PSOILib import helperfunctions
        helperfunctions.showVolumeRendering(volume_node, preset="CT-Bone")

        # DisplayNode für den HU-Shift-Mechanismus referenzieren
        vrLogic = slicer.modules.volumerendering.logic()
        self._vrDisplayNode = vrLogic.CreateDefaultVolumeRenderingNodes(volume_node)

        # Toggle-Button in den "ein"-Zustand bringen (ohne erneutes Signal)
        self.ui.toggleVolumeRenderingButton.blockSignals(True)
        self.ui.toggleVolumeRenderingButton.setChecked(True)
        self.ui.toggleVolumeRenderingButton.setText(_("3D off"))
        self.ui.toggleVolumeRenderingButton.blockSignals(False)

        # Aktuellen HU-Shift anwenden (überschreibt ggf. das frisch gesetzte Preset)
        self.onHUShiftChanged(self.ui.huShiftSlider.value)

    def _centerViewAnterior(self, volume_node) -> None:
        layoutManager = slicer.app.layoutManager()
        if layoutManager is None:
            return
        threeDWidget = layoutManager.threeDWidget(0)
        if threeDWidget is None:
            return
        threeDView = threeDWidget.threeDView()

        bounds = [0.0] * 6
        volume_node.GetRASBounds(bounds)
        cx = (bounds[0] + bounds[1]) / 2
        cy = (bounds[2] + bounds[3]) / 2
        cz = (bounds[4] + bounds[5]) / 2
        extent = max(bounds[1]-bounds[0], bounds[3]-bounds[2], bounds[5]-bounds[4])

        viewNode = threeDView.mrmlViewNode()
        cameraNode = slicer.modules.cameras.logic().GetViewActiveCameraNode(viewNode)
        if cameraNode is None:
            return

        # Von anterior betrachten: Kamera auf +Y (RAS: A = anterior = +Y)
        cameraNode.SetFocalPoint(cx, cy, cz)
        cameraNode.SetPosition(cx, cy + extent * 1.5, cz)
        cameraNode.SetViewUp(0.0, 0.0, 1.0)
        cameraNode.ResetClippingRange()
        threeDView.forceRender()

    # ------------------------------------------------------------------
    # Signalhandler – Workflow
    # ------------------------------------------------------------------

    def onStepsToolboxCurrentChanged(self, currentId: int) -> None:
        if self._parameterNode:
            self._parameterNode.step = currentId

    def onAutoSeedToggled(self, checked: bool) -> None:
        self.ui.seedOffsetSpinBox.setEnabled(not checked)

    def onSeedModeChanged(self) -> None:
        is_contra = self.ui.rbSeedContralateral.isChecked()
        is_model  = self.ui.rbSeedModelBased.isChecked()
        # For region-seed modes the FM starts from a large pre-computed volume,
        # so a much lower stopping value is sufficient.
        self.ui.stoppingValueSpinBox.setValue(15.0 if (is_contra or is_model) else 25.0)
        # Show / hide the model-template row
        self.ui.labelModelSeed.setVisible(is_model)
        self.ui.modelSeedWidget.setVisible(is_model)

    def onModelSeedChanged(self, node) -> None:
        self.ui.positionModelButton.setEnabled(node is not None)
        if self._parameterNode is None:
            return
        isLeft = self._parameterNode.sideIsLeft
        if isLeft:
            self._parameterNode.modelSeedNodeLeft  = node
        else:
            self._parameterNode.modelSeedNodeRight = node

    def onPositionModelButton(self) -> None:
        if self._parameterNode is None:
            return
        isLeft      = self._parameterNode.sideIsLeft
        seg_node    = self.ui.modelSeedSelector.currentNode()
        volume_node = self.ui.ctVolumeSelector.currentNode()
        if seg_node is None:
            slicer.util.warningDisplay(_("Please select a template segmentation first."))
            return

        tf_attr = "modelSeedTransformLeft" if isLeft else "modelSeedTransformRight"
        existing_tf = getattr(self._parameterNode, tf_attr, None)

        if existing_tf is not None and slicer.mrmlScene.IsNodePresent(existing_tf):
            transform_node = existing_tf
        else:
            side_suffix = "L" if isLeft else "R"
            transform_node = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLLinearTransformNode", f"ModelSeedTransform_{side_suffix}"
            )
            transform_node.CreateDefaultDisplayNodes()
            setattr(self._parameterNode, tf_attr, transform_node)

        # Place the transform's origin at the orbital rim centroid (user sees handles there)
        plane = (self._parameterNode.planeModelLeft if isLeft
                 else self._parameterNode.planeModelRight)
        if plane is not None:
            centroid, _ = self.logic._getPlaneFromModel(plane)
        elif volume_node is not None:
            bounds = [0.0] * 6
            volume_node.GetRASBounds(bounds)
            centroid = np.array([(bounds[0]+bounds[1])/2,
                                  (bounds[2]+bounds[3])/2,
                                  (bounds[4]+bounds[5])/2])
        else:
            centroid = np.zeros(3)

        mat = vtk.vtkMatrix4x4()
        mat.Identity()
        mat.SetElement(0, 3, float(centroid[0]))
        mat.SetElement(1, 3, float(centroid[1]))
        mat.SetElement(2, 3, float(centroid[2]))
        transform_node.SetMatrixTransformToParent(mat)

        # Apply the transform to the template – user can now drag it into position
        seg_node.SetAndObserveTransformNodeID(transform_node.GetID())

        # Show interaction handles (translation + rotation; no scaling needed)
        disp = transform_node.GetDisplayNode()
        disp.SetEditorVisibility(True)
        disp.SetEditorTranslationEnabled(True)
        disp.SetEditorRotationEnabled(True)
        disp.SetEditorScalingEnabled(False)

        slicer.app.layoutManager().threeDWidget(0).threeDView().resetFocalPoint()

    def onCreateSurfaceButton(self) -> None:
        with slicer.util.tryWithErrorDisplay(_("Error creating the orbital plane."), waitCursor=True):
            curve_node = self.ui.curveSelector.currentNode()
            if curve_node is None:
                raise ValueError(_("Please select a closed curve (Closed Curve)."))

            isLeft = self._parameterNode.sideIsLeft
            color = (0.2, 0.7, 1.0) if isLeft else (1.0, 0.55, 0.1)

            model_node = self.logic.createOrbitalSurface(
                curve_node=curve_node,
                method=self.ui.methodComboBox.currentText,
                subdivision_distance=self.ui.subdivisionDistanceSpinBox.value,
                smooth_iterations=self.ui.smoothIterationsSpinBox.value,
                color=color,
            )

            n_tris = model_node.GetPolyData().GetNumberOfCells()
            n_pts  = model_node.GetPolyData().GetNumberOfPoints()
            text = (
                _("<b>Created:</b> {name}<br>Triangles: {tris} &nbsp;|&nbsp; Points: {pts}").format(
                    name=model_node.GetName(), tris=n_tris, pts=n_pts
                )
            )
            self._surfaceTexts[isLeft] = text
            self.ui.surfaceResultLabel.setText(text)

            if isLeft:
                self._parameterNode.planeModelLeft = model_node
            else:
                self._parameterNode.planeModelRight = model_node

            self.ui.planeModelSelector.blockSignals(True)
            self.ui.planeModelSelector.setCurrentNode(model_node)
            self.ui.planeModelSelector.blockSignals(False)

            self._nextStep()

    def onSegmentVolumeButton(self) -> None:
        with slicer.util.tryWithErrorDisplay(_("Error during volume segmentation."), waitCursor=True):
            volume_node = self.ui.ctVolumeSelector.currentNode()
            plane_model = self.ui.planeModelSelector.currentNode()

            if volume_node is None:
                raise ValueError(_("Please select a CT volume."))
            if plane_model is None:
                raise ValueError(_("Please select an orbital plane (mesh)."))

            seed_offset = (
                None if self.ui.autoSeedCheckBox.isChecked()
                else self.ui.seedOffsetSpinBox.value
            )

            isLeft = self._parameterNode.sideIsLeft
            self._saveParamsForSide(isLeft)
            existing_seg  = (self._parameterNode.segmentationNodeLeft
                             if isLeft else self._parameterNode.segmentationNodeRight)
            existing_seed = (self._parameterNode.seedNodeLeft
                             if isLeft else self._parameterNode.seedNodeRight)

            # --- Seed-Modus bestimmen ---
            seed_mode = (1 if self.ui.rbSeedContralateral.isChecked()
                         else 2 if self.ui.rbSeedModelBased.isChecked() else 0)

            # Modus 1: Gegenseite spiegeln
            contra_seg_node = None
            contra_seg_id   = None
            if seed_mode == 1:
                contra_node = (self._parameterNode.segmentationNodeRight
                               if isLeft else self._parameterNode.segmentationNodeLeft)
                contra_side = not isLeft
                if contra_node is not None:
                    contra_seg_node = contra_node
                    contra_seg_id   = self._segIds.get(contra_side)
                    if contra_seg_id is None:
                        seg = contra_node.GetSegmentation()
                        for i in range(seg.GetNumberOfSegments()):
                            if seg.GetNthSegment(i).GetName() == "IntraorbitalVolume":
                                contra_seg_id = seg.GetNthSegmentID(i)
                                break

            # Modus 2: Modell-basierter Seed
            model_seed_node = None
            model_seed_id   = None
            if seed_mode == 2:
                model_seed_node = (self._parameterNode.modelSeedNodeLeft if isLeft
                                   else self._parameterNode.modelSeedNodeRight)
                if model_seed_node is not None:
                    seg = model_seed_node.GetSegmentation()
                    if seg.GetNumberOfSegments() > 0:
                        model_seed_id = seg.GetNthSegmentID(0)

            result = self.logic.segmentIntraorbitalVolume(
                volume_node=volume_node,
                plane_model=plane_model,
                hu_min=self.ui.huMinSpinBox.value,
                hu_max=self.ui.huMaxSpinBox.value,
                seed_offset_mm=seed_offset,
                orbital_depth_mm=self.ui.orbitalDepthSpinBox.value,
                radius_margin_mm=self.ui.radiusMarginSpinBox.value,
                stopping_value=self.ui.stoppingValueSpinBox.value,
                speed_sigma=self.ui.speedSigmaSpinBox.value,
                posterior_boost=self.ui.posteriorBoostSpinBox.value,
                show_seed=self.ui.showSeedCheckBox.isChecked(),
                existing_segmentation_node=existing_seg,
                existing_seed_node=existing_seed,
                contralateral_seg_node=contra_seg_node,
                contralateral_seg_id=contra_seg_id,
                model_seed_node=model_seed_node,
                model_seed_id=model_seed_id,
                remove_satellites=self.ui.removeSatellitesCheckBox.isChecked(),
                min_satellite_diameter_mm=self.ui.satelliteDiamSpinBox.value,
            )

            if isLeft:
                self._parameterNode.segmentationNodeLeft = result["segmentation_node"]
                self._parameterNode.seedNodeLeft         = result["seed_node"]
            else:
                self._parameterNode.segmentationNodeRight = result["segmentation_node"]
                self._parameterNode.seedNodeRight         = result["seed_node"]

            text = (
                _("<b>Intraorbital volume: {vol:.2f} ml</b><br>"
                  "Voxels: {vox}<br>"
                  "Seed offset: {off:.1f} mm &nbsp;|&nbsp; "
                  "HU at seed: {hu:.0f}").format(
                    vol=result['volume_ml'],
                    vox=f"{result['voxel_count']:,}",
                    off=result['offset_mm'],
                    hu=result['hu_at_seed'],
                )
            )
            self._segTexts[isLeft] = text
            self.ui.segmentationResultLabel.setText(text)

            # Alle Slice-Ansichten auf das Zentrum der Segmentierung springen
            seg_node = result["segmentation_node"]
            bounds = [0.0] * 6
            seg_node.GetRASBounds(bounds)
            cx = (bounds[0] + bounds[1]) / 2
            cy = (bounds[2] + bounds[3]) / 2
            cz = (bounds[4] + bounds[5]) / 2
            for view_name in ["Red", "Green", "Yellow"]:
                sliceWidget = slicer.app.layoutManager().sliceWidget(view_name)
                if sliceWidget:
                    sliceWidget.sliceLogic().GetSliceNode().JumpSliceByCentering(cx, cy, cz)

            # 3D-Ansicht auf Segmentierungs-Zentrum verschieben (Kamera-Richtung beibehalten)
            threeDWidget = slicer.app.layoutManager().threeDWidget(0)
            if threeDWidget:
                threeDView = threeDWidget.threeDView()
                cameraNode = slicer.modules.cameras.logic().GetViewActiveCameraNode(
                    threeDView.mrmlViewNode()
                )
                if cameraNode:
                    fp = cameraNode.GetFocalPoint()
                    pos = cameraNode.GetPosition()
                    dx, dy, dz = cx - fp[0], cy - fp[1], cz - fp[2]
                    cameraNode.SetFocalPoint(cx, cy, cz)
                    cameraNode.SetPosition(pos[0]+dx, pos[1]+dy, pos[2]+dz)
                    cameraNode.ResetClippingRange()
                    threeDView.forceRender()

            # Volume-Rendering ausblenden
            if self._vrDisplayNode is not None:
                self._vrDisplayNode.SetVisibility(False)
                self.ui.toggleVolumeRenderingButton.blockSignals(True)
                self.ui.toggleVolumeRenderingButton.setChecked(False)
                self.ui.toggleVolumeRenderingButton.setText(_("3D on"))
                self.ui.toggleVolumeRenderingButton.blockSignals(False)

            # Observer für manuelle Nachbearbeitung einrichten
            seg_node_new = result["segmentation_node"]
            seg_id_new   = result["segment_id"]
            old_node = self._segNodes[isLeft]
            old_obs  = self._segObservers[isLeft]
            if old_node is not None and old_obs is not None:
                old_node.RemoveObserver(old_obs)
            self._segNodes[isLeft] = seg_node_new
            self._segIds[isLeft]   = seg_id_new
            self._segObservers[isLeft] = seg_node_new.AddObserver(
                vtk.vtkCommand.ModifiedEvent,
                lambda c, e, side=isLeft: self._onSegmentModified(side),
            )
            self.ui.openSegmentEditorButton.setEnabled(True)

    def _nextStep(self) -> None:
        if self.ui.stepsToolbox.currentIndex < self.ui.stepsToolbox.count - 1:
            self.ui.stepsToolbox.setCurrentIndex(self.ui.stepsToolbox.currentIndex + 1)

    def onOpenSegmentEditorButton(self) -> None:
        isLeft   = self._parameterNode.sideIsLeft if self._parameterNode else True
        seg_node = self._segNodes.get(isLeft)
        if seg_node is None:
            return
        volume_node = self.ui.ctVolumeSelector.currentNode()

        slicer.util.selectModule("SegmentEditor")
        qt.QApplication.processEvents()
        try:
            w = slicer.modules.segmenteditor.widgetRepresentation().self()
            w.editor.setSegmentationNode(seg_node)
            if volume_node:
                try:
                    w.editor.setSourceVolumeNode(volume_node)
                except AttributeError:
                    w.editor.setMasterVolumeNode(volume_node)
            w.editor.setActiveEffectByName("Scissors")
        except Exception as exc:
            logging.warning(f"SegmentEditor setup failed: {exc}")

    def onPlacePosteriorMarkupButton(self) -> None:
        if self._parameterNode is None:
            return
        isLeft   = self._parameterNode.sideIsLeft
        attr     = "posteriorMarkupLeft" if isLeft else "posteriorMarkupRight"
        existing = getattr(self._parameterNode, attr, None)

        # Reuse existing node (just clear the point) or create a fresh one
        if existing is not None and slicer.mrmlScene.IsNodePresent(existing):
            node = existing
            node.RemoveAllControlPoints()
        else:
            side_suffix = "L" if isLeft else "R"
            node = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLMarkupsFiducialNode", f"PosteriorBoundary_{side_suffix}"
            )
            disp = node.GetDisplayNode()
            disp.SetSelectedColor(1.0, 0.2, 0.2)
            disp.SetColor(1.0, 0.2, 0.2)
            disp.SetGlyphScale(3.0)
            setattr(self._parameterNode, attr, node)

            # Attach observer so that depth updates automatically when the point moves
            obs = node.AddObserver(
                vtk.vtkCommand.ModifiedEvent,
                lambda c, e, side=isLeft: self._onPosteriorMarkupModified(side),
            )
            old_obs = self._posteriorMarkupObservers.get(isLeft)
            if old_obs is not None:
                node.RemoveObserver(old_obs)
            self._posteriorMarkupObservers[isLeft] = obs

        node.SetMaximumNumberOfControlPoints(1)
        selNode   = slicer.app.applicationLogic().GetSelectionNode()
        selNode.SetActivePlaceNodeID(node.GetID())
        selNode.SetActivePlaceNodeClassName("vtkMRMLMarkupsFiducialNode")
        interNode = slicer.app.applicationLogic().GetInteractionNode()
        interNode.SetCurrentInteractionMode(slicer.vtkMRMLInteractionNode.Place)

    def _onPosteriorMarkupModified(self, isLeft: bool) -> None:
        """Recomputes orbital depth from the posterior boundary markup and updates the spinbox."""
        if self._parameterNode is None:
            return
        # Only update when we're viewing this side
        if self._parameterNode.sideIsLeft != isLeft:
            return
        plane  = (self._parameterNode.planeModelLeft  if isLeft
                  else self._parameterNode.planeModelRight)
        markup = (self._parameterNode.posteriorMarkupLeft  if isLeft
                  else self._parameterNode.posteriorMarkupRight)
        volume = self.ui.ctVolumeSelector.currentNode()
        if plane is None or markup is None or volume is None:
            return
        if markup.GetNumberOfControlPoints() == 0:
            return

        pt = [0.0, 0.0, 0.0]
        markup.GetNthControlPointPositionWorld(0, pt)
        centroid, normal = self.logic._getPlaneFromModel(plane)
        normal = self.logic._ensurePosteriorDirection(normal, centroid, volume)
        depth  = float(np.dot(np.array(pt) - centroid, normal))
        # Clamp to spinbox limits
        depth  = max(20.0, min(100.0, round(depth, 1)))

        self.ui.orbitalDepthSpinBox.blockSignals(True)
        self.ui.orbitalDepthSpinBox.setValue(depth)
        self.ui.orbitalDepthSpinBox.blockSignals(False)
        s = "Left" if isLeft else "Right"
        self._parameterNode.__setattr__(f"orbitalDepth{s}", depth)

    def _onSegmentModified(self, isLeft: bool) -> None:
        self._volumeUpdateSide = isLeft
        self._volumeUpdateTimer.start()

    def _doVolumeUpdate(self) -> None:
        isLeft   = self._volumeUpdateSide
        if isLeft is None:
            return
        seg_node = self._segNodes.get(isLeft)
        seg_id   = self._segIds.get(isLeft)
        if seg_node is None or seg_id is None:
            return
        volume_ml, voxel_count = self._calculateVolumeFromSegment(seg_node, seg_id)
        text = (
            _("<b>Intraorbital volume: {vol:.2f} ml</b>"
              " &nbsp;<i>(after manual editing)</i><br>"
              "Voxels: {vox}").format(vol=volume_ml, vox=f"{voxel_count:,}")
        )
        self._segTexts[isLeft] = text
        if self._parameterNode and self._parameterNode.sideIsLeft == isLeft:
            self.ui.segmentationResultLabel.setText(text)

    def _calculateVolumeFromSegment(self, seg_node, seg_id) -> tuple:
        try:
            seg     = seg_node.GetSegmentation()
            segment = seg.GetSegment(seg_id)
            if segment is None:
                return 0.0, 0
            binary_lm = segment.GetRepresentation("Binary labelmap")
            if binary_lm is None:
                return 0.0, 0
            import vtk.util.numpy_support as nps
            scalars = binary_lm.GetPointData().GetScalars()
            if scalars is None:
                return 0.0, 0
            arr         = nps.vtk_to_numpy(scalars)
            voxel_count = int((arr > 0).sum())
            spacing     = binary_lm.GetSpacing()
            volume_ml   = voxel_count * spacing[0] * spacing[1] * spacing[2] / 1000.0
            return volume_ml, voxel_count
        except Exception as exc:
            logging.warning(f"Volume recalculation failed: {exc}")
            return 0.0, 0


# ═══════════════════════════════════════════════════════════════════════════════
# Logic
# ═══════════════════════════════════════════════════════════════════════════════

class OrbitalVolumeWorkflowModuleLogic(ScriptedLoadableModuleLogic):

    def __init__(self) -> None:
        ScriptedLoadableModuleLogic.__init__(self)

    def getParameterNode(self):
        return OrbitalVolumeWorkflowModuleParameterNode(super().getParameterNode())

    # ------------------------------------------------------------------
    # Öffentliche API
    # ------------------------------------------------------------------

    def createOrbitalSurface(
        self,
        curve_node,
        method: str = "auto",
        subdivision_distance: float = 5.0,
        smooth_iterations: int = 0,
        color: tuple = (0.2, 0.7, 1.0),
        opacity: float = 0.65,
    ):
        """
        Erstellt ein Surface-Mesh aus einer vtkMRMLMarkupsClosedCurveNode.

        Gibt den erzeugten vtkMRMLModelNode zurück.
        """
        print(f"\n{'='*55}")
        print(f"  Orbital Surface Generator")
        print(f"  Curve : {curve_node.GetName()}")

        pts = self._sampleCurvePoints(curve_node, subdivision_distance)
        print(f"  Points along curve   : {len(pts)}")

        if method == "auto":
            try:
                ci = self._concavityIndex(pts)
                print(f"  Concavity index      : {ci:.3f}")
                method = "delaunay" if ci > 1.08 else "fan"
                print(f"  Method (auto)        : {method}")
            except Exception:
                method = "delaunay"
                print("  Method (fallback)    : delaunay")
        else:
            print(f"  Method               : {method}")

        raw_poly = (
            self._delaunayTriangulation(pts)
            if method == "delaunay"
            else self._fanTriangulation(pts)
        )

        final_poly = self._postprocessSurface(raw_poly, smooth_iterations)

        model_node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLModelNode")
        model_node.SetName(f"{curve_node.GetName()}_OrbitalPlane")
        model_node.SetAndObservePolyData(final_poly)
        model_node.CreateDefaultDisplayNodes()

        disp = model_node.GetDisplayNode()
        disp.SetOpacity(opacity)
        disp.SetColor(*color)
        disp.SetBackfaceCulling(False)
        disp.SetRepresentation(2)
        disp.SetEdgeVisibility(True)

        print(f"  Triangles / Points   : {final_poly.GetNumberOfCells()} / {final_poly.GetNumberOfPoints()}")
        print(f"  Node name            : {model_node.GetName()}")
        print(f"{'='*55}\n")

        return model_node

    def segmentIntraorbitalVolume(
        self,
        volume_node,
        plane_model,
        hu_min: int = -200,
        hu_max: int = 300,
        seed_offset_mm=None,
        orbital_depth_mm: float = 55.0,
        radius_margin_mm: float = 5.0,
        stopping_value: float = 25.0,
        speed_sigma: float = 70.0,
        posterior_boost: float = 1.5,
        show_seed: bool = True,
        segment_color: tuple = (0.2, 0.8, 0.5),
        existing_segmentation_node=None,
        existing_seed_node=None,
        contralateral_seg_node=None,
        contralateral_seg_id=None,
        model_seed_node=None,
        model_seed_id=None,
        remove_satellites: bool = True,
        min_satellite_diameter_mm: float = 3.0,
    ) -> dict:
        """
        Segmentiert das intraorbitale Volumen via Fast Marching.

        Gibt ein dict zurück:
          segmentation_node, segment_id, volume_ml, voxel_count,
          seed_ras, offset_mm, hu_at_seed
        """
        print(f"\n{'='*60}")
        print(f"  Intraorbital Volume Segmentation")
        print(f"{'='*60}")
        print(f"  CT volume    : {volume_node.GetName()}")
        print(f"  Entry plane  : {plane_model.GetName()}")

        centroid, normal = self._getPlaneFromModel(plane_model)
        normal = self._ensurePosteriorDirection(normal, centroid, volume_node)
        orbital_radius_mm = self._estimateOrbitalRadius(plane_model)

        print(f"  Centroid (RAS): {np.round(centroid, 1)}")
        print(f"  Normal vector : {np.round(normal, 3)}")
        print(f"  Orbital radius: {orbital_radius_mm:.1f} mm")

        if seed_offset_mm is not None:
            seed_ras = centroid + seed_offset_mm * normal
            hu = self._getHUatRAS(seed_ras, volume_node)
            offset_used = seed_offset_mm
            print(f"  Seed offset   : {offset_used} mm (manual), HU={hu:.0f}")
            if hu is None or not (hu_min <= hu <= hu_max):
                print(f"  Warning: HU={hu} outside [{hu_min}, {hu_max}] - used anyway")
        else:
            seed_ras, offset_used, hu = self._findSeedPoint(
                centroid, normal, volume_node, hu_min, hu_max
            )
            print(f"  Seed offset   : {offset_used:.1f} mm (auto), HU={hu:.0f}")

        print(f"  Seed pos (RAS): {np.round(seed_ras, 1)}")

        seed_name = plane_model.GetName().replace("_OrbitalPlane", "") + "_Seed"
        seed_node = self._placeSeedFiducial(
            seed_ras, name=seed_name, existing_node=existing_seed_node
        )
        seed_node.GetDisplayNode().SetVisibility(1 if show_seed else 0)

        seg_node_name = plane_model.GetName().replace("_OrbitalPlane", "_IntraorbitalSeg")

        if existing_segmentation_node is not None:
            segmentation_node = existing_segmentation_node
            segmentation_node.SetName(seg_node_name)
            segmentation_node.GetSegmentation().RemoveAllSegments()
            print(f"  Existing segmentation node will be overwritten: {seg_node_name}")
        else:
            segmentation_node = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLSegmentationNode", seg_node_name
            )
            segmentation_node.CreateDefaultDisplayNodes()

        segmentation_node.SetReferenceImageGeometryParameterFromVolumeNode(volume_node)

        contra_info = " + contralateral mirror" if contralateral_seg_node is not None else ""
        print(
            f"\n  Fast Marching "
            f"(stopping={stopping_value}, sigma={speed_sigma}, "
            f"boost={posterior_boost}, "
            f"cylinder: {orbital_depth_mm} mm x "
            f"r={orbital_radius_mm:.1f}+{radius_margin_mm} mm"
            f"{contra_info}) ..."
        )

        seg_id, volume_ml, voxel_count = self._fastMarchingSegmentation(
            volume_node, segmentation_node, seed_ras,
            centroid, normal, orbital_radius_mm,
            orbital_depth_mm, radius_margin_mm,
            plane_model=plane_model,
            stopping_value=stopping_value,
            speed_sigma=speed_sigma,
            posterior_boost=posterior_boost,
            contralateral_seg_node=contralateral_seg_node,
            contralateral_seg_id=contralateral_seg_id,
            model_seed_node=model_seed_node,
            model_seed_id=model_seed_id,
        )

        print("  Smoothing: Closing (3 mm) ...")
        self._smoothSegment(segmentation_node, seg_id, kernel_size_mm=3.0, method="closing")
        print("  Smoothing: Opening (3 mm) ...")
        self._smoothSegment(segmentation_node, seg_id, kernel_size_mm=3.0, method="opening")

        if remove_satellites:
            print(f"  Satellite removal (min diameter {min_satellite_diameter_mm} mm) ...")
            self._removeSatelliteRegions(segmentation_node, seg_id, min_satellite_diameter_mm)

        seg = segmentation_node.GetSegmentation()
        segment = seg.GetSegment(seg_id)
        segment.SetName("IntraorbitalVolume")
        segment.SetColor(*segment_color)

        disp = segmentation_node.GetDisplayNode()
        disp.SetOpacity3D(0.5)
        disp.SetOpacity2DFill(0.4)
        disp.SetVisibility3D(True)

        segmentation_node.CreateClosedSurfaceRepresentation()
        slicer.app.layoutManager().threeDWidget(0).threeDView().resetFocalPoint()

        print(f"\n{'─'*60}")
        print(f"  Intraorbital volume    : {volume_ml:.2f} ml")
        print(f"  Voxels                 : {voxel_count:,}")
        print(f"  Segment node           : {seg_node_name}")
        print(f"{'='*60}\n")

        slicer.util.setSliceViewerLayers(background=volume_node)

        return {
            "segmentation_node": segmentation_node,
            "segment_id":        seg_id,
            "volume_ml":         volume_ml,
            "voxel_count":       voxel_count,
            "seed_ras":          seed_ras,
            "seed_node":         seed_node,
            "offset_mm":         offset_used,
            "hu_at_seed":        hu,
        }

    # ------------------------------------------------------------------
    # Hilfsfunktionen – Orbital Surface
    # ------------------------------------------------------------------

    def _sampleCurvePoints(self, curve_node, subdivision_distance: float):
        """Resamples the closed curve at uniform arc-length intervals and returns all control points as an (N,3) RAS array."""
        curve_node.ResampleCurveWorld(subdivision_distance)
        n = curve_node.GetNumberOfControlPoints()
        pts = np.zeros((n, 3))
        for i in range(n):
            p = [0.0, 0.0, 0.0]
            curve_node.GetNthControlPointPositionWorld(i, p)
            pts[i] = p
        return pts

    def _concavityIndex(self, pts):
        """Returns convex-hull-area / polygon-area; values > 1 indicate a concave (non-convex) curve, which selects fan over Delaunay triangulation."""
        from scipy.spatial import ConvexHull

        centroid = pts.mean(axis=0)
        centered = pts - centroid
        _, _, Vt = np.linalg.svd(centered, full_matrices=False)
        pts2d = centered @ Vt[:2].T

        x, y = pts2d[:, 0], pts2d[:, 1]
        poly_area = 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))

        hull = ConvexHull(pts2d)
        hull_area = hull.volume  # in 2D ist .volume die Fläche

        return hull_area / poly_area if poly_area > 1e-6 else 1.0

    def _pcaProject(self, pts):
        """Projects 3D points onto their best-fit plane via SVD; returns 2D coordinates, centroid, and the rotation matrix Vt for back-projection."""
        centroid = pts.mean(axis=0)
        centered = pts - centroid
        _, _, Vt = np.linalg.svd(centered, full_matrices=False)
        return centered @ Vt[:2].T, centroid, Vt

    def _fanTriangulation(self, pts):
        """Connects every consecutive edge of the curve to the centroid, producing a star-shaped mesh; robust for concave or self-intersecting curves where Delaunay would produce artefacts."""
        n = len(pts)
        centroid = pts.mean(axis=0)

        vtk_pts = vtk.vtkPoints()
        for p in pts:
            vtk_pts.InsertNextPoint(p.tolist())
        vtk_pts.InsertNextPoint(centroid.tolist())
        center_idx = n

        triangles = vtk.vtkCellArray()
        for i in range(n):
            tri = vtk.vtkTriangle()
            tri.GetPointIds().SetId(0, center_idx)
            tri.GetPointIds().SetId(1, i)
            tri.GetPointIds().SetId(2, (i + 1) % n)
            triangles.InsertNextCell(tri)

        poly = vtk.vtkPolyData()
        poly.SetPoints(vtk_pts)
        poly.SetPolys(triangles)
        return poly

    def _delaunayTriangulation(self, pts):
        """Projects the curve onto its best-fit plane, runs constrained 2D Delaunay triangulation, then back-projects to 3D; produces a higher-quality mesh for convex curves than fan triangulation."""
        pts2d, centroid, Vt = self._pcaProject(pts)

        vtk_pts = vtk.vtkPoints()
        for p2 in pts2d:
            vtk_pts.InsertNextPoint(p2[0], p2[1], 0.0)

        n = len(pts2d)
        boundary = vtk.vtkCellArray()
        polyline = vtk.vtkPolyLine()
        polyline.GetPointIds().SetNumberOfIds(n + 1)
        for i in range(n):
            polyline.GetPointIds().SetId(i, i)
        polyline.GetPointIds().SetId(n, 0)
        boundary.InsertNextCell(polyline)

        boundary_poly = vtk.vtkPolyData()
        boundary_poly.SetPoints(vtk_pts)
        boundary_poly.SetLines(boundary)

        delaunay = vtk.vtkDelaunay2D()
        delaunay.SetInputData(boundary_poly)
        delaunay.SetSourceData(boundary_poly)
        delaunay.SetTolerance(0.001)
        delaunay.SetAlpha(0.0)
        delaunay.Update()

        result_2d = delaunay.GetOutput()

        result_pts = vtk.vtkPoints()
        for i in range(result_2d.GetNumberOfPoints()):
            p = result_2d.GetPoint(i)
            p3d = np.array([p[0], p[1]]) @ Vt[:2] + centroid
            result_pts.InsertNextPoint(p3d.tolist())

        out_poly = vtk.vtkPolyData()
        out_poly.SetPoints(result_pts)
        out_poly.SetPolys(result_2d.GetPolys())
        return out_poly

    def _postprocessSurface(self, poly_data, smooth_iterations: int = 0):
        """Optionally smooths the mesh with a windowed-sinc filter, then recomputes consistent point and cell normals required for correct rendering and ray-casting."""
        if smooth_iterations > 0:
            smoother = vtk.vtkWindowedSincPolyDataFilter()
            smoother.SetInputData(poly_data)
            smoother.SetNumberOfIterations(smooth_iterations)
            smoother.BoundarySmoothingOff()
            smoother.NonManifoldSmoothingOn()
            smoother.NormalizeCoordinatesOn()
            smoother.Update()
            poly_data = smoother.GetOutput()

        normals = vtk.vtkPolyDataNormals()
        normals.SetInputData(poly_data)
        normals.ComputePointNormalsOn()
        normals.ComputeCellNormalsOn()
        normals.AutoOrientNormalsOn()
        normals.ConsistencyOn()
        normals.Update()
        return normals.GetOutput()

    # ------------------------------------------------------------------
    # Hilfsfunktionen – Volumensegmentierung
    # ------------------------------------------------------------------

    def _estimateOrbitalRadius(self, plane_model) -> float:
        """Returns the maximum distance from the mesh centroid to any rim point; using max (not mean) ensures the prefilter sphere encloses the entire rim, including eccentric superior regions."""
        poly = plane_model.GetPolyData()
        n_pts = poly.GetNumberOfPoints()
        pts = np.array([poly.GetPoint(i) for i in range(n_pts)])
        centroid = pts.mean(axis=0)
        # max statt mean: Zylinder muss den gesamten Orbitarand umschließen,
        # da sonst exzentrische Randbereiche (z.B. superior) abgeschnitten werden
        return float(np.linalg.norm(pts - centroid, axis=1).max())

    def _getPlaneFromModel(self, model_node):
        """Fits a plane to all mesh vertices via SVD and returns (centroid, unit_normal); the least-significant singular vector (Vt[2]) is the best-fit normal."""
        poly = model_node.GetPolyData()
        pts = np.array([poly.GetPoint(i) for i in range(poly.GetNumberOfPoints())])
        centroid = pts.mean(axis=0)
        centered = pts - centroid
        _, _, Vt = np.linalg.svd(centered, full_matrices=False)
        return centroid, Vt[2]

    def _ensurePosteriorDirection(self, normal, centroid, volume_node):
        """Flips the plane normal if it points anteriorly (away from the volume centre); the Fast Marching depth axis must point into the orbit, not out of it."""
        bounds = [0.0] * 6
        volume_node.GetRASBounds(bounds)
        vol_center = np.array([
            (bounds[0] + bounds[1]) / 2,
            (bounds[2] + bounds[3]) / 2,
            (bounds[4] + bounds[5]) / 2,
        ])
        if np.dot(normal, vol_center - centroid) < 0:
            normal = -normal
        return normal

    def _rasToIjk(self, ras_point, volume_node):
        """Converts a RAS world coordinate to the nearest integer voxel index (IJK) using the volume's RAS-to-IJK matrix."""
        mat = vtk.vtkMatrix4x4()
        volume_node.GetRASToIJKMatrix(mat)
        ras_h = list(ras_point) + [1.0]
        ijk_h = mat.MultiplyPoint(ras_h)
        return np.round(ijk_h[:3]).astype(int)

    def _getHUatRAS(self, ras_point, volume_node):
        """Reads the scalar (HU) value from the CT volume at a given RAS position; returns None if the point lies outside the image extent."""
        ijk = self._rasToIjk(ras_point, volume_node)
        img = volume_node.GetImageData()
        dims = img.GetDimensions()
        if any(ijk[i] < 0 or ijk[i] >= dims[i] for i in range(3)):
            return None
        return img.GetScalarComponentAsFloat(int(ijk[0]), int(ijk[1]), int(ijk[2]), 0)

    def _findSeedPoint(self, centroid, normal, volume_node, hu_min, hu_max):
        """Walks along the orbital axis from the rim inward in 2 mm steps until a voxel within the soft-tissue HU window is found; raises if no candidate is found within 30 mm."""
        SEED_OFFSET_START_MM = 10.0
        SEED_OFFSET_STEP_MM  =  2.0
        SEED_OFFSET_MAX_MM   = 30.0

        offset = SEED_OFFSET_START_MM
        while offset <= SEED_OFFSET_MAX_MM:
            candidate = centroid + offset * normal
            hu = self._getHUatRAS(candidate, volume_node)
            if hu is not None and hu_min <= hu <= hu_max:
                return candidate, offset, hu
            offset += SEED_OFFSET_STEP_MM

        raise RuntimeError(
            _("No valid seed point found within the HU window [{hu_min}, {hu_max}] "
              "within {max_mm} mm. "
              "Adjust the HU window or seed offset.").format(
                hu_min=hu_min, hu_max=hu_max, max_mm=SEED_OFFSET_MAX_MM
            )
        )

    def _placeSeedFiducial(self, seed_ras, name: str = "OrbitalSeed", existing_node=None):
        """Creates a yellow fiducial markup at seed_ras, or repositions an existing one in-place to avoid cluttering the scene hierarchy on re-segmentation."""
        if existing_node is not None and slicer.mrmlScene.IsNodePresent(existing_node):
            existing_node.SetName(name)
            existing_node.RemoveAllControlPoints()
            existing_node.AddControlPoint(seed_ras.tolist())
            return existing_node
        node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode")
        node.SetName(name)
        node.AddControlPoint(seed_ras.tolist())
        node.GetDisplayNode().SetSelectedColor(1.0, 0.8, 0.0)
        node.GetDisplayNode().SetGlyphScale(3.0)
        return node

    def _fastMarchingSegmentation(
        self,
        volume_node, segmentation_node, seed_ras,
        centroid, normal, orbital_radius_mm,
        orbital_depth_mm, radius_margin_mm,
        plane_model=None,
        stopping_value: float = 25.0,
        speed_sigma: float = 70.0,
        posterior_boost: float = 1.5,
        contralateral_seg_node=None,
        contralateral_seg_id=None,
        model_seed_node=None,
        model_seed_id=None,
    ):
        """Segments the intraorbital volume via Fast Marching on a HU-derived speed image.

        Speed is a Gaussian of HU centred at 0 (soft tissue near HU=0 is fast; bone and air are both near zero and act as barriers).
        A spherical prefilter limits the working region for performance; within
        that region the orbital rim mesh defines the anterior boundary and
        orbital_depth_mm the posterior limit.  A posterior_boost factor
        gradually increases speed towards the back of the orbit to prevent
        premature stopping in low-contrast fat.  The result is imported as a
        new segment into segmentation_node.
        """
        import SimpleITK as sitk
        import sitkUtils
        from scipy.interpolate import griddata

        # --- 1. CT-Volumen als NumPy-Array laden ---
        # sitkUtils überträgt den Slicer-Volume-Node nach SimpleITK; Cast stellt
        # sicher dass wir float32-Werte (HU) und nicht integer-Rohdaten verwenden.
        sitk_image = sitkUtils.PullVolumeFromSlicer(volume_node)
        hu_arr = sitk.GetArrayFromImage(sitk.Cast(sitk_image, sitk.sitkFloat32))

        # --- 2. Speed-Image aus HU ableiten ---
        # Gaußkurve mit Maximum bei HU=0: Weichgewebe ≈ 1, Knochen/Luft ≈ 0.
        # speed_sigma bestimmt die Breite – größere Werte tolerieren mehr HU-Abweichung.
        speed_arr = np.exp(-(hu_arr / speed_sigma) ** 2).astype(np.float32)

        # --- 3. Koordinatensystem: RAS → LPS ---
        # SimpleITK und VTK arbeiten in LPS; Slicer-Koordinaten sind RAS.
        # X und Y werden negiert, Z bleibt gleich.
        c_lps = np.array([-centroid[0], -centroid[1],  centroid[2]])
        n_lps = np.array([-normal[0],   -normal[1],    normal[2]])

        # --- 4. Physikalische LPS-Koordinaten für jeden Voxel berechnen ---
        # Aus Gitterindizes (i,j,k) wird über Origin, Spacing und Direction-Matrix
        # die tatsächliche 3D-Position im Patientenraum errechnet.
        origin    = np.array(sitk_image.GetOrigin())
        spacing   = np.array(sitk_image.GetSpacing())
        direction = np.array(sitk_image.GetDirection()).reshape(3, 3)

        Nz, Ny, Nx = hu_arr.shape
        IZ, IY, IX = np.meshgrid(np.arange(Nz), np.arange(Ny), np.arange(Nx), indexing='ij')
        idx_flat = np.stack([IX.flatten(), IY.flatten(), IZ.flatten()], axis=1).astype(np.float64)
        lps_pts = origin + (direction @ (idx_flat * spacing).T).T

        # --- 5. Orbita-Koordinaten: Tiefe und lateraler Abstand ---
        # rel: Versatz jedes Voxels vom Orbitazentrum (Centroid des Rimmodells).
        # depth: Projektion auf die Orbitaachse (normal) – positiv = posterior.
        # lateral: senkrechter Abstand von der Achse (für den Fallback ohne Mesh).
        rel     = lps_pts - c_lps
        depth   = rel @ n_lps
        lateral = np.linalg.norm(rel - np.outer(depth, n_lps), axis=1)

        if plane_model is not None:
            # --- 6. Lokales 2D-Koordinatensystem in der Orbitaebene aufbauen ---
            # u_vec zeigt "nach oben" (world-up projiziert auf die Orbitaebene),
            # v_vec steht senkrecht dazu; zusammen spannen sie die Eingangsebene auf.
            world_up = np.array([0.0, 0.0, 1.0])
            u_vec = world_up - np.dot(world_up, n_lps) * n_lps
            if np.linalg.norm(u_vec) < 1e-6:  # Fallback wenn Achse ≈ world_up
                u_vec = np.array([1.0, 0.0, 0.0]) - np.dot(np.array([1.0, 0.0, 0.0]), n_lps) * n_lps
            u_vec /= np.linalg.norm(u_vec)
            v_vec = np.cross(n_lps, u_vec)

            # --- 7. Mesh-Punkte in das lokale Koordinatensystem projizieren ---
            # mesh_d: Tiefe jedes Rimmodell-Punktes entlang der Orbitaachse.
            # mesh_u/v: 2D-Position im Orbitaebenenkoordinatensystem.
            poly = plane_model.GetPolyData()
            mesh_ras = np.array([poly.GetPoint(i) for i in range(poly.GetNumberOfPoints())])
            mesh_lps = mesh_ras * np.array([-1.0, -1.0, 1.0])
            mesh_rel = mesh_lps - c_lps
            mesh_d   = mesh_rel @ n_lps
            mesh_u   = mesh_rel @ u_vec
            mesh_v   = mesh_rel @ v_vec

            # --- 8. Sphärischer Vorab-Filter ---
            # Begrenzt die teure Mesh-Interpolation auf Voxel in der Nähe der Orbita.
            # Ein zylindrischer Filter entlang der Orbitaachse würde bei geneigter Ebene
            # superior gelegene Voxel fälschlicherweise ausschließen (horizontaler Z-Schnitt).
            # Der sphärische Abstand vom Centroid ist achsunabhängig und vermeidet das.
            max_extent    = orbital_radius_mm + radius_margin_mm + orbital_depth_mm
            centroid_dist = np.linalg.norm(rel, axis=1)
            in_region = (
                (centroid_dist <= max_extent) &
                (depth >= -5.0) &                    # 5 mm Puffer vor der Eingangsebene
                (depth <= orbital_depth_mm + 5.0)    # 5 mm Puffer hinter der Tiefengrenze
            )

            # --- 9. Für jeden Voxel in der Region: Tiefe des Rimmodells interpolieren ---
            # griddata bestimmt an der lateralen (u,v)-Position jedes Voxels die
            # zugehörige Tiefe der Orbitaöffnungsfläche (mesh_d).
            # Nearest-Fallback für Voxel außerhalb der konvexen Hülle der Meshpunkte.
            rel_in       = rel[in_region]
            depth_in_reg = depth[in_region]
            vu = rel_in @ u_vec
            vv = rel_in @ v_vec

            mesh_depth_at_voxel = griddata(
                np.column_stack([mesh_u, mesh_v]),
                mesh_d,
                np.column_stack([vu, vv]),
                method='linear', fill_value=np.nan,
            )
            nan_mask = np.isnan(mesh_depth_at_voxel)
            if nan_mask.any():
                mesh_depth_at_voxel[nan_mask] = griddata(
                    np.column_stack([mesh_u, mesh_v]),
                    mesh_d,
                    np.column_stack([vu[nan_mask], vv[nan_mask]]),
                    method='nearest',
                )

            # --- 10. Ausschlussmaske berechnen ---
            # Außerhalb der Vorab-Region: immer ausgeschlossen.
            # Innerhalb: ausgeschlossen wenn anterior zur Orbitaöffnungsfläche
            # (depth < mesh_depth) oder tiefer als die Tiefengrenze (depth > orbital_depth_mm).
            outside = ~in_region
            outside[in_region] = (
                (depth_in_reg < mesh_depth_at_voxel) |  # anterior zum Rimmodell
                (depth_in_reg > orbital_depth_mm)        # posterior zur Tiefengrenze
            )
        else:
            # Fallback ohne Rimmodell: einfacher zylindrischer Bereich
            outside = (
                (depth <= 0) |
                (depth > orbital_depth_mm) |
                (lateral > orbital_radius_mm + radius_margin_mm)
            )

        # --- 11. Posterior-Boost anwenden ---
        # Orbitales Fett weiter posterior hat oft niedrigen Kontrast → Front stoppt zu früh.
        # Der Faktor steigt linear von 1.0 (anterior) auf 1+posterior_boost (posterior),
        # sodass die Front im hinteren Orbitabereich schneller läuft.
        depth_norm = np.clip(depth / orbital_depth_mm, 0.0, 1.0)
        posterior_factor = (1.0 + posterior_boost * depth_norm).reshape(Nz, Ny, Nx)
        speed_arr = np.clip(speed_arr * posterior_factor, 1e-4, None)

        # --- 12. Ausgeschlossene Voxel als harte Barriere setzen ---
        # speed=1e-4 ist praktisch null: die Front kann diese Voxel zwar theoretisch
        # erreichen, aber erst nach extrem langer Reisezeit (weit jenseits stopping_value).
        speed_arr[outside.reshape(Nz, Ny, Nx)] = 1e-4

        # --- 13. Speed-Image zurück nach SimpleITK konvertieren ---
        # CopyInformation überträgt Origin, Spacing und Direction, damit der
        # FM-Filter die korrekte Geometrie kennt.
        speed_sitk = sitk.GetImageFromArray(speed_arr)
        speed_sitk.CopyInformation(sitk_image)

        # --- 14. Trial-Punkte zusammenstellen ---
        # Primärer Seed: einzelner Punkt auf der Orbitaachse.
        s_lps = [-float(seed_ras[0]), -float(seed_ras[1]), float(seed_ras[2])]
        seed_idx = speed_sitk.TransformPhysicalPointToIndex(s_lps)
        print(f"  FM-Seed-Index (LPS): {seed_idx}")

        # Bestimme den FM-Startbereich je nach Seed-Modus:
        # - Gegenseite: spiegeln + 10 % schrumpfen (Knochen-/Siebbeinchutz)
        # - Modell:     positioniertes Template direkt exportieren (lila Anzeige)
        # - Manuell:    einzelner Punkt auf der Orbitaachse (Fallback für alle Modi)
        name_prefix = segmentation_node.GetName().replace("_IntraorbitalSeg", "") + "_"
        if contralateral_seg_node is not None and contralateral_seg_id is not None:
            region_points = self._prepareContralateralSeed(
                contralateral_seg_node, contralateral_seg_id, volume_node, name_prefix
            )
        elif model_seed_node is not None and model_seed_id is not None:
            region_points = self._prepareModelSeed(
                model_seed_node, model_seed_id, volume_node, name_prefix
            )
        else:
            region_points = []

        trial_points = region_points if region_points else [seed_idx]
        if seed_idx not in trial_points:
            trial_points = [seed_idx] + trial_points

        # --- 15. Fast Marching ausführen ---
        # Der Filter berechnet die Arrival-Time für jeden Voxel ausgehend vom Seed.
        # Er stoppt wenn die minimale verbleibende Arrival-Time stopping_value überschreitet.
        fm = sitk.FastMarchingImageFilter()
        fm.SetStoppingValue(stopping_value)
        fm.SetTrialPoints(trial_points)
        arrival = fm.Execute(speed_sitk)

        # --- 16. Arrival-Time → binäre Maske ---
        # Alle Voxel mit Arrival-Time < stopping_value gehören zum Segment.
        arrival_arr = sitk.GetArrayFromImage(arrival)
        binary_arr  = (arrival_arr < stopping_value).astype(np.uint8)
        voxel_count = int(binary_arr.sum())

        sp = sitk_image.GetSpacing()
        volume_ml = voxel_count * sp[0] * sp[1] * sp[2] / 1000.0
        print(f"  Fast Marching: {voxel_count:,} voxels, {volume_ml:.2f} ml")

        # --- 17. Ergebnis als Segment in Slicer importieren ---
        # Weg: NumPy → SimpleITK → LabelMapVolumeNode → SegmentationNode.
        # Der temporäre LabelMap-Node wird nach dem Import wieder entfernt.
        binary_sitk = sitk.GetImageFromArray(binary_arr)
        binary_sitk.CopyInformation(sitk_image)

        lm_node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLabelMapVolumeNode", "TmpFMLabelmap")
        sitkUtils.PushVolumeToSlicer(binary_sitk, lm_node)
        slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(
            lm_node, segmentation_node
        )
        slicer.mrmlScene.RemoveNode(lm_node)

        seg = segmentation_node.GetSegmentation()
        seg_ids = vtk.vtkStringArray()
        seg.GetSegmentIDs(seg_ids)
        seg_id = seg_ids.GetValue(seg_ids.GetNumberOfValues() - 1)

        return seg_id, volume_ml, voxel_count

    def _smoothSegment(self, segmentation_node, seg_id, kernel_size_mm: float = 3.0, method: str = "closing"):
        """Applies morphological closing or opening to the binary labelmap using an ellipsoidal kernel scaled to physical mm.

        The array is zero-padded before the operation so that scipy's border
        treatment (outside = background) does not erode voxels at the tight
        bounding-box edge, which would create an artificial flat cut.
        """
        import vtk.util.numpy_support as nps
        from scipy import ndimage

        seg = segmentation_node.GetSegmentation()
        binary_lm = seg.GetSegment(seg_id).GetRepresentation("Binary labelmap")
        if binary_lm is None:
            raise RuntimeError(_("Segment has no binary labelmap representation."))

        dims    = binary_lm.GetDimensions()
        spacing = binary_lm.GetSpacing()
        scalars = binary_lm.GetPointData().GetScalars()

        flat      = nps.vtk_to_numpy(scalars)
        vol_array = flat.reshape(dims[2], dims[1], dims[0])

        r  = kernel_size_mm / 2.0
        rz = max(1, int(round(r / spacing[2])))
        ry = max(1, int(round(r / spacing[1])))
        rx = max(1, int(round(r / spacing[0])))
        z, y, x = np.ogrid[-rz:rz+1, -ry:ry+1, -rx:rx+1]
        struct = (z/rz)**2 + (y/ry)**2 + (x/rx)**2 <= 1.0

        binary = vol_array > 0
        # Padding verhindert Boundary-Artefakte: scipy behandelt außerhalb des
        # Arrays als Hintergrund (0), was die Erosionsphase alle Randvoxel
        # entfernt und einen künstlichen flachen Schnitt am Array-Rand erzeugt.
        pad = max(rz, ry, rx) + 1
        padded = np.pad(binary, pad, mode='constant', constant_values=0)
        if method == "closing":
            result_padded = ndimage.binary_closing(padded, structure=struct)
        elif method == "opening":
            result_padded = ndimage.binary_opening(padded, structure=struct)
        else:
            raise ValueError(_("Unknown method: {method}").format(method=repr(method)))
        result = result_padded[pad:-pad, pad:-pad, pad:-pad]

        flat_out = result.astype(np.uint8).flatten().astype(flat.dtype)
        scalars.DeepCopy(nps.numpy_to_vtk(flat_out))
        binary_lm.Modified()
        segmentation_node.Modified()

    def _removeSatelliteRegions(
        self, segmentation_node, seg_id, min_diameter_mm: float = 3.0
    ):
        """Removes connected components whose sphere-equivalent diameter is below min_diameter_mm.

        The largest component is always kept regardless of size.  For each other
        component, the effective diameter is computed as d = 2*(3V/4π)^(1/3), where V
        is the component volume in mm³.  Components below the threshold are deleted;
        the main body (largest component) is never touched.
        """
        import vtk.util.numpy_support as nps
        from scipy import ndimage

        seg      = segmentation_node.GetSegmentation()
        binary_lm = seg.GetSegment(seg_id).GetRepresentation("Binary labelmap")
        if binary_lm is None:
            return

        dims    = binary_lm.GetDimensions()
        spacing = binary_lm.GetSpacing()
        scalars = binary_lm.GetPointData().GetScalars()

        flat      = nps.vtk_to_numpy(scalars)
        vol_array = flat.reshape(dims[2], dims[1], dims[0])
        binary    = vol_array > 0

        labeled, n_components = ndimage.label(binary)
        if n_components <= 1:
            print(f"    1 component – nothing to remove")
            return

        sizes       = np.array(ndimage.sum(binary, labeled, range(1, n_components + 1)))
        vox_vol_mm3 = spacing[0] * spacing[1] * spacing[2]
        largest     = int(np.argmax(sizes)) + 1

        keep_mask = np.zeros_like(binary)
        n_removed = 0
        for label_idx, size in enumerate(sizes, start=1):
            vol_mm3  = float(size) * vox_vol_mm3
            eff_diam = 2.0 * (3.0 * vol_mm3 / (4.0 * np.pi)) ** (1.0 / 3.0)
            if label_idx == largest or eff_diam >= min_diameter_mm:
                keep_mask |= (labeled == label_idx)
            else:
                n_removed += 1
                print(f"    Component {label_idx}: d_eff={eff_diam:.1f} mm → removed")

        print(f"    {n_components} components, {n_removed} removed")
        if n_removed > 0:
            flat_out = keep_mask.astype(np.uint8).flatten().astype(flat.dtype)
            scalars.DeepCopy(nps.numpy_to_vtk(flat_out))
            binary_lm.Modified()
            segmentation_node.Modified()

    def _prepareContralateralSeed(
        self, contra_seg_node, contra_seg_id, volume_node, name_prefix=""
    ):
        """Mirrors the contralateral segment, visualises both the raw mirror and a
        10 %-shrunk version as segmentation nodes, and returns the shrunk voxel
        indices as FM trial points.

        Returns a list of (i, j, k) index tuples (empty on failure).
        """
        import SimpleITK as sitk
        import sitkUtils

        # Export contralateral segment at full CT resolution
        seg_id_arr = vtk.vtkStringArray()
        seg_id_arr.InsertNextValue(contra_seg_id)
        tmp_lm = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLLabelMapVolumeNode", "_TmpContraLM"
        )
        try:
            slicer.modules.segmentations.logic().ExportSegmentsToLabelmapNode(
                contra_seg_node, seg_id_arr, tmp_lm, volume_node
            )
            contra_sitk = sitkUtils.PullVolumeFromSlicer(tmp_lm)
        finally:
            slicer.mrmlScene.RemoveNode(tmp_lm)

        contra_arr = sitk.GetArrayFromImage(contra_sitk)  # ZYX uint8

        # Mirror across L-R axis (axis=2 in ZYX = image i axis in standard LPS CT)
        mirrored = np.ascontiguousarray(np.flip(contra_arr, axis=2))
        print(f"  Contralateral mirror: {int(mirrored.sum()):,} voxels")

        # Display full mirrored mask (blue, semi-transparent)
        mirrored_sitk = sitk.GetImageFromArray(mirrored)
        mirrored_sitk.CopyInformation(contra_sitk)
        self._importBinaryAsSegNode(
            mirrored_sitk, volume_node,
            name=f"{name_prefix}ContraMirror_Full",
            color=(0.2, 0.6, 1.0),
            opacity=0.25,
        )

        # Shrink 10 % towards mass centre so the seed stays away from bone / ethmoidal cells
        shrunk = self._shrinkBinaryMask(mirrored, scale_factor=0.9)
        print(f"  Shrunk seed region:   {int(shrunk.sum()):,} voxels")

        # Display shrunk mask (orange, slightly more opaque so it's easy to distinguish)
        shrunk_sitk = sitk.GetImageFromArray(shrunk)
        shrunk_sitk.CopyInformation(contra_sitk)
        self._importBinaryAsSegNode(
            shrunk_sitk, volume_node,
            name=f"{name_prefix}ContraMirror_Shrunk",
            color=(1.0, 0.55, 0.1),
            opacity=0.4,
        )

        # Collect nonzero indices of shrunk mask as FM trial points
        nz_k, nz_j, nz_i = np.where(shrunk > 0)
        if len(nz_i) == 0:
            print("  Shrunk seed is empty – falling back to single seed.")
            return []

        return [(int(nz_i[m]), int(nz_j[m]), int(nz_k[m])) for m in range(len(nz_i))]

    def _prepareModelSeed(
        self, model_seg_node, model_seg_id, volume_node, name_prefix=""
    ):
        """Exports the user-positioned template segmentation into the CT volume's geometry
        and returns its voxel indices as FM trial points.

        The template is expected to have a vtkMRMLLinearTransformNode applied (via
        onPositionModelButton).  ExportSegmentsToLabelmapNode honours the parent
        transform automatically, so the exported labelmap reflects the positioned state.
        """
        import SimpleITK as sitk
        import sitkUtils

        seg_id_arr = vtk.vtkStringArray()
        seg_id_arr.InsertNextValue(model_seg_id)
        tmp_lm = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLLabelMapVolumeNode", "_TmpModelLM"
        )
        try:
            slicer.modules.segmentations.logic().ExportSegmentsToLabelmapNode(
                model_seg_node, seg_id_arr, tmp_lm, volume_node
            )
            model_sitk = sitkUtils.PullVolumeFromSlicer(tmp_lm)
        finally:
            slicer.mrmlScene.RemoveNode(tmp_lm)

        model_arr = sitk.GetArrayFromImage(model_sitk)
        n_vox = int(model_arr.sum())
        print(f"  Model seed: {n_vox:,} voxels")

        if n_vox == 0:
            print("  Model seed is empty – verify the template is positioned inside the CT volume.")
            return []

        # Display the positioned seed region (purple) for user verification
        self._importBinaryAsSegNode(
            model_sitk, volume_node,
            name=f"{name_prefix}ModelSeed",
            color=(0.7, 0.2, 0.9),
            opacity=0.4,
        )

        nz_k, nz_j, nz_i = np.where(model_arr > 0)
        return [(int(nz_i[m]), int(nz_j[m]), int(nz_k[m])) for m in range(len(nz_i))]

    def _shrinkBinaryMask(self, binary_arr, scale_factor: float = 0.9):
        """Scales a binary mask towards its mass centre by scale_factor.

        Uses inverse coordinate mapping: output voxel q is set iff the source point
        centre + (q − centre) / scale_factor lies inside the original mask.
        A scale_factor of 0.9 shrinks the mask by ~10 % in each linear direction.
        """
        from scipy import ndimage

        binary = (binary_arr > 0).astype(np.float32)
        if not binary.any():
            return binary_arr.copy()

        center = np.array(ndimage.center_of_mass(binary))  # ZYX index coords

        Nz, Ny, Nx = binary.shape
        z_idx, y_idx, x_idx = np.meshgrid(
            np.arange(Nz), np.arange(Ny), np.arange(Nx), indexing='ij'
        )
        coords = np.stack([z_idx, y_idx, x_idx], axis=0).astype(np.float64)

        # Inverse-map: output voxel q comes from source = centre + (q − centre) / scale_factor
        src = (
            center[:, None, None, None]
            + (coords - center[:, None, None, None]) / scale_factor
        )

        sampled = ndimage.map_coordinates(
            binary, src.reshape(3, -1), order=1, mode='constant', cval=0.0
        )
        return (sampled.reshape(Nz, Ny, Nx) > 0.5).astype(np.uint8)

    def _importBinaryAsSegNode(self, binary_sitk, volume_node, name, color, opacity=0.35):
        """Creates (or replaces) a named segmentation node from a SimpleITK binary image."""
        import sitkUtils

        # Remove stale node from a previous run so the scene stays tidy
        old = slicer.mrmlScene.GetFirstNodeByName(name)
        if old is not None:
            slicer.mrmlScene.RemoveNode(old)

        tmp_lm = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLLabelMapVolumeNode", "_TmpImportLM"
        )
        try:
            sitkUtils.PushVolumeToSlicer(binary_sitk, tmp_lm)
            seg_node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode", name)
            seg_node.CreateDefaultDisplayNodes()
            seg_node.SetReferenceImageGeometryParameterFromVolumeNode(volume_node)
            slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(
                tmp_lm, seg_node
            )
        finally:
            slicer.mrmlScene.RemoveNode(tmp_lm)

        seg = seg_node.GetSegmentation()
        if seg.GetNumberOfSegments() > 0:
            segment = seg.GetNthSegment(0)
            segment.SetColor(*color)
            segment.SetName(name)

        disp = seg_node.GetDisplayNode()
        disp.SetOpacity3D(opacity)
        disp.SetOpacity2DFill(opacity)
        disp.SetVisibility3D(True)
        seg_node.CreateClosedSurfaceRepresentation()

        return seg_node
