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
import os
import time
from typing import Optional

import numpy as np
import vtk
import ctk

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

import SimpleITK as sitk
import sitkUtils

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
        _doc_html = os.path.join(
            os.path.dirname(__file__), "Resources", "Docs", "OrbitalVolumeWorkflowModule","orbita_volume_workflow_DE.html"
        )
        _doc_url = "file://" + _doc_html.replace("\\", "/")
        self.parent.helpText = (
            _("This module creates an entry plane for the orbit from a closed curve "
              "and subsequently segments the intraorbital volume using Fast Marching. "
              "The left and right orbits are managed separately.")
            + f' <a target="_blank" href="{_doc_url}">'
            + _("Open manual (DE)")
            + "</a>"
        )
        self.parent.acknowledgementText = _(
            "Developed by Johannes Schulze (Bundeswehrkrankenhaus Ulm/Universität Ulm) "
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
    huMinLeft:          float   = -200
    huMinRight:         float   = -200
    huMaxLeft:          float   =  300
    huMaxRight:         float   =  300
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
    posteriorCutoffNode:     vtkMRMLMarkupsFiducialNode   # shared, beide Seiten
    removeSatellitesLeft:   bool  = True
    removeSatellitesRight:  bool  = True
    satelliteDiamLeft:      float = 3.0
    satelliteDiamRight:     float = 3.0
    contraPositionedNodeLeft:       vtkMRMLSegmentationNode
    contraPositionedNodeRight:      vtkMRMLSegmentationNode
    contraPositionedTransformLeft:  vtkMRMLLinearTransformNode
    contraPositionedTransformRight: vtkMRMLLinearTransformNode
    currentStep : str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# Presets
# ═══════════════════════════════════════════════════════════════════════════════

# Keys match combobox item text; "Manual" has no entry (never applied programmatically).
SEGMENTATION_PRESETS = {
    "CT Bone Window": {
        "huMin": -200, "huMax": 300,
        "stoppingValue": 25.0, "speedSigma": 70.0,
    },
    "Intraoperative CBCT": {
        "huMin": -300, "huMax": 500,
        "stoppingValue": 20.0, "speedSigma": 120.0,
    },
}


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
        self._segVolumes   = {True: None, False: None}  # volume_ml per side
        self._vrDisplayNode = None
        # Segmentierungs-Nodes und Observer für automatische Volumen-Neuberechnung
        self._segNodes     = {True: None, False: None}
        self._segIds       = {True: None, False: None}
        self._segObservers             = {True: None, False: None}
        self._posteriorMarkupObservers = {True: None, False: None}
        self._cutoffMarkupObserver = None
        self._volumeUpdateSide = None
        self._applyingPreset = False
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

        # QTextBrowser intercepts all link clicks; redirect them to the system browser.
        for browser in self.parent.findChildren(qt.QTextBrowser):
            browser.setOpenLinks(False)
            browser.anchorClicked.connect(lambda url: qt.QDesktopServices.openUrl(url))

        uiWidget = slicer.util.loadUI(self.resourcePath("UI/OrbitalVolumeWorkflowModule.ui"))
        self.layout.addWidget(uiWidget)
        self.ui = slicer.util.childWidgetVariables(uiWidget)
        uiWidget.setMRMLScene(slicer.mrmlScene)
        self._uiWidget = uiWidget

        slicer.app.connect("paletteChanged(QPalette)", self._refreshButtonStyles)
        slicer.app.connect("styleChanged(QString)", self._refreshButtonStyles)


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
        self.ui.clearSideButton.connect("clicked(bool)",       self.onClearSideButton)
        self.ui.exportResultsButton.connect("clicked(bool)",   self.onExportResultsButton)
        self.ui.autoSeedCheckBox.connect("toggled(bool)",       self.onAutoSeedToggled)
        #self.ui.stepsToolbox.connect("currentChanged(int)",     self.onStepsToolboxCurrentChanged)
    
        self.ui.placePosteriorMarkupButton.connect("clicked(bool)", self.onPlacePosteriorMarkupButton)
        self.ui.placePosteriorMarkupButton.setIcon(qt.QIcon(self.resourcePath("Icons/MarkupsFiducialMouseModePlace.png")))
        
        self.ui.placeCutoffButton.connect("clicked(bool)", self.onPlaceCutoffButton)
        self.ui.placeCutoffButton.setIcon(qt.QIcon(self.resourcePath("Icons/MarkupsFiducialMouseModePlace.png")))

        self.ui.selectIslandButton.connect("clicked(bool)", self.onSelectIslandButton)
        self.ui.performCutoffButton.connect("clicked(bool)", self.onPerformCutoffButton)

        # Open Documentation Button
        self.ui.openDocumentationButton.connect("clicked(bool)", self.onOpenDocumentationClicked)

        self.ui.cutoffNodeSelector.connect(
            "currentNodeChanged(vtkMRMLNode*)", self.onCutoffNodeSelectorChanged
        )
        self.ui.removeSatellitesCheckBox.connect(
            "toggled(bool)", lambda checked: self.ui.satelliteDiamSpinBox.setEnabled(checked)
        )
        for rb in [self.ui.rbSeedManual, self.ui.rbSeedContralateral, self.ui.rbSeedModelBased]:
            rb.connect("toggled(bool)", lambda checked: checked and self.onSeedModeChanged())
        self.ui.modelSeedSelector.connect(
            "currentNodeChanged(vtkMRMLNode*)", self.onModelSeedChanged
        )
        self.ui.loadTemplateButton.connect("clicked(bool)",  self.onLoadTemplateButton)
        self.ui.positionModelButton.connect("clicked(bool)",  self.onPositionModelButton)
        self.ui.mirrorTemplateButton.connect("toggled(bool)", self.onMirrorTemplateToggled)
        self.ui.positionContralateralButton.connect("clicked(bool)", self.onPositionContralateralButton)

        # Seiten-Buttons
        self.ui.btnSideLeft.connect("clicked()",  lambda: self.onSideChanged(True))
        #self.ui.btnSideLeft.setIcon(qt.QIcon(self.resourcePath("Icons/orbit_left.png")))
        self.ui.btnSideRight.connect("clicked()", lambda: self.onSideChanged(False))
        #self.ui.btnSideRight.setIcon(qt.QIcon(self.resourcePath("Icons/orbit_right.png")))

        # Collapsibles
        self.ui.pageOrbitalSurface.connect("clicked(bool)", lambda x: self.onPageCollapsibleClicked(self.ui.pageOrbitalSurface, x))
        self.ui.pageVolumeSegmentation.connect("clicked(bool)", lambda x: self.onPageCollapsibleClicked(self.ui.pageVolumeSegmentation, x))

        # Selektoren manuell beobachten (kein SlicerParameterName für seitenspezifische Nodes)
        self.ui.curveSelector.connect(
            "currentNodeChanged(vtkMRMLNode*)", self.onCurveChanged)
        self.ui.planeModelSelector.connect(
            "currentNodeChanged(vtkMRMLNode*)", self.onPlaneModelChanged)

        # Preset combobox
        self.ui.presetComboBox.connect("currentIndexChanged(int)", self.onPresetChanged)
        self.ui.huMinSpinBox.connect("valueChanged(int)", self._onPresetSpinboxChanged)
        self.ui.huMaxSpinBox.connect("valueChanged(int)", self._onPresetSpinboxChanged)
        self.ui.stoppingValueSpinBox.connect("valueChanged(double)", self._onPresetSpinboxChanged)
        self.ui.speedSigmaSpinBox.connect("valueChanged(double)", self._onPresetSpinboxChanged)

        # CT-Volume → Volume-Rendering; HU-Shift-Slider; Toggle-Button
        self.ui.ctVolumeSelector.connect(
            "currentNodeChanged(vtkMRMLNode*)", self.onCTVolumeChanged)
        self.ui.huShiftSlider.connect("valueChanged(double)", self.onHUShiftChanged)
        self.ui.toggleVolumeRenderingButton.connect(
            "toggled(bool)", self.onToggleVolumeRendering)

        # Neue-Kurve-Button
        self.ui.createCurveButton.connect("clicked(bool)", self.onCreateCurveButton)
        self.ui.createCurveButton.setIcon(qt.QIcon(self.resourcePath("Icons/MarkupsClosedCurveMouseModePlace.png")))

        # Manuelle Segmentierungsknoten-Auswahl
        self.ui.segNodeSelector.setMRMLScene(slicer.mrmlScene)
        self.ui.segNodeSelector.connect(
            "currentNodeChanged(vtkMRMLNode*)", self.onSegNodeSelectorChanged)

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
            cutoff_node = getattr(self._parameterNode, "posteriorCutoffNode", None)
            if cutoff_node is not None and self._cutoffMarkupObserver is not None:
                cutoff_node.RemoveObserver(self._cutoffMarkupObserver)
        self.removeObservers()

    def enter(self) -> None:
        self.initializeParameterNode()
        if self._parameterNode:
            self._activateCurrentStep()
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

    def _findWidgetByName(self, name):
        for i in range(self.ui.mainVerticalLayout.count()):
            item_widget = self.ui.mainVerticalLayout.itemAt(i).widget()

            if item_widget != None and item_widget.objectName == name:
                return item_widget
        
        return None

    # ------------------------------------------------------------------
    # Preset
    # ------------------------------------------------------------------

    def onPresetChanged(self, index: int) -> None:
        if self._applyingPreset:
            return
        name = self.ui.presetComboBox.itemText(index)
        preset = SEGMENTATION_PRESETS.get(name)
        if preset is None:
            return  # "Manual" selected programmatically or by user — nothing to apply
        ok = slicer.util.confirmOkCancelDisplay(
            _("Apply preset \"{name}\"?\n\n"
              "HU min = {huMin}, HU max = {huMax}\n"
              "Stopping value = {stoppingValue} mm, Speed sigma = {speedSigma} HU\n\n"
              "This will overwrite the current values.").format(name=name, **preset),
            _("Apply Preset"),
        )
        if not ok:
            # Revert combobox without triggering this handler again
            self._applyingPreset = True
            self.ui.presetComboBox.setCurrentIndex(
                self.ui.presetComboBox.count - 1  # "Manual"
            )
            self._applyingPreset = False
            return
        self._applyingPreset = True
        self.ui.huMinSpinBox.setValue(preset["huMin"])
        self.ui.huMaxSpinBox.setValue(preset["huMax"])
        self.ui.stoppingValueSpinBox.setValue(preset["stoppingValue"])
        self.ui.speedSigmaSpinBox.setValue(preset["speedSigma"])
        self._applyingPreset = False

    def _onPresetSpinboxChanged(self, _value) -> None:
        if self._applyingPreset:
            return
        # Switch to "Manual" without triggering onPresetChanged
        self._applyingPreset = True
        manual_index = self.ui.presetComboBox.count - 1
        self.ui.presetComboBox.setCurrentIndex(manual_index)
        self._applyingPreset = False

    def onOpenDocumentationClicked(self, _value) -> None:
        import webbrowser
        from os import path

        _locale = qt.QLocale().system().name()[3:]
        filename = self.resourcePath(f"Docs/OrbitalVolumeWorkflowModule/orbita_volume_workflow_{_locale}.html")

        if not os.path.exists(filename):
            filename = self.resourcePath(f"Docs/OrbitalVolumeWorkflowModule/orbita_volume_workflow.html")
        
        webbrowser.open("file://" + filename)

    def onPageCollapsibleClicked(self, sender: qt.QWidget, expanded: bool) -> None:
        collapsibleWidgets = self._uiWidget.findChildren(ctk.ctkCollapsibleButton)

        for w in collapsibleWidgets:
            w.setChecked(False)

        if expanded:
            sender.setChecked(True)
            self._parameterNode.currentStep = sender.objectName
        else:
            self._parameterNode.currentStep = ""

        self._refreshButtonStyles()
            

    def _activateCurrentStep(self):
        currentStep = self._parameterNode.currentStep
        page_widget = self._findWidgetByName(currentStep)

        if page_widget != None:
            self.onPageCollapsibleClicked(page_widget, True)

        
    # ------------------------------------------------------------------
    # Parameter-Node
    # ------------------------------------------------------------------

    def initializeParameterNode(self) -> None:
        if self._parameterNode is not None:
            # Re-entering after exit(): GUI was disconnected but observers and
            # self._parameterNode are still intact — just reconnect the GUI.
            wasBlocked = self.ui.ctVolumeSelector.blockSignals(True)
            self.ui.ctVolumeSelector.setCurrentNode(self._parameterNode.ctVolume)
            self.ui.ctVolumeSelector.blockSignals(wasBlocked)
            self._parameterNodeGuiTag = self._parameterNode.connectGui(self.ui)
            wasBlocked = self.ui.parameterNodeSelector.blockSignals(True)
            self.ui.parameterNodeSelector.setCurrentNode(self._parameterNode.parameterNode)
            self.ui.parameterNodeSelector.blockSignals(wasBlocked)
            return
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
            # Save current GUI values so they survive a PN switch
            self._saveParamsForSide(self._parameterNode.sideIsLeft)
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
            cutoff_node = getattr(self._parameterNode, "posteriorCutoffNode", None)
            if cutoff_node is not None and self._cutoffMarkupObserver is not None:
                cutoff_node.RemoveObserver(self._cutoffMarkupObserver)
            self._cutoffMarkupObserver = None
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

        # Observer an vorhandenen Cutoff-Markup-Node hängen + Selector synchronisieren
        cutoff_node = getattr(self._parameterNode, "posteriorCutoffNode", None)
        if cutoff_node is not None:
            self._cutoffMarkupObserver = cutoff_node.AddObserver(
                vtk.vtkCommand.ModifiedEvent,
                lambda c, e: self._onCutoffMarkupModified(),
            )
        wasBlocked = self.ui.cutoffNodeSelector.blockSignals(True)
        self.ui.cutoffNodeSelector.setCurrentNode(cutoff_node)
        self.ui.cutoffNodeSelector.blockSignals(wasBlocked)
        self._onCutoffMarkupModified()

        # ctVolumeSelector: sync to new PN's volume; no signal blocking so that
        # onCTVolumeChanged fires and the display updates correctly.
        self.ui.ctVolumeSelector.setCurrentNode(self._parameterNode.ctVolume)

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

        # refresh button palettes after side switch
        btnActive = self.ui.btnSideLeft if isLeft else self.ui.btnSideRight
        btnInactive = self.ui.btnSideRight if isLeft else self.ui.btnSideLeft

        self._refreshButtonStyles()

        self.ui.planeModelSelector.blockSignals(True)
        if self._parameterNode:
            plane = (self._parameterNode.planeModelLeft
                     if isLeft else self._parameterNode.planeModelRight)
            self.ui.planeModelSelector.setCurrentNode(plane)
        self.ui.planeModelSelector.blockSignals(False)

        self.ui.segNodeSelector.blockSignals(True)
        if self._parameterNode:
            seg = (self._parameterNode.segmentationNodeLeft if isLeft
                   else self._parameterNode.segmentationNodeRight)
            self.ui.segNodeSelector.setCurrentNode(seg)
        self.ui.segNodeSelector.blockSignals(False)

        self._loadParamsForSide(isLeft)
        self.ui.createSurfaceButton.setEnabled(self._curveNodes[isLeft] is not None)
        self.ui.surfaceResultLabel.setText(self._surfaceTexts[isLeft])

        # Volumen nach Reload aus vorhandenem Segmentierungsknoten nachmessen
        if not self._segTexts[isLeft] and self._parameterNode is not None:
            seg_node = (self._parameterNode.segmentationNodeLeft if isLeft
                        else self._parameterNode.segmentationNodeRight)
            if seg_node is not None:
                seg = seg_node.GetSegmentation()
                seg_id = None
                for i in range(seg.GetNumberOfSegments()):
                    if seg.GetNthSegment(i).GetName() == "IntraorbitalVolume":
                        seg_id = seg.GetNthSegmentID(i)
                        break
                if seg_id is not None:
                    try:
                        vol_ml, voxel_count = self._calculateVolumeFromSegment(seg_node, seg_id)
                        self._segVolumes[isLeft] = vol_ml
                        self._segTexts[isLeft] = (
                            _("<b>Intraorbital volume: {vol:.2f} ml</b>"
                              " &nbsp;<i>(reloaded)</i><br>"
                              "Voxels: {vox}").format(
                                vol=vol_ml, vox=f"{voxel_count:,}")
                        )
                    except Exception:
                        pass

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
        # Show/hide mode-specific rows
        is_contra = (mode == 1)
        is_model  = (mode == 2)
        self.ui.labelContraPosition.setVisible(is_contra)
        self.ui.positionContralateralWidget.setVisible(is_contra)
        self.ui.positionContralateralButton.setEnabled(
            is_contra and contra_node is not None
        )
        self.ui.labelModelSeed.setVisible(is_model)
        self.ui.modelSeedWidget.setVisible(is_model)
        # Restore model seed selector
        model_seed = getattr(self._parameterNode, f"modelSeedNode{s}", None)
        self.ui.modelSeedSelector.blockSignals(True)
        self.ui.modelSeedSelector.setCurrentNode(model_seed)
        self.ui.modelSeedSelector.blockSignals(False)
        self.ui.positionModelButton.setEnabled(model_seed is not None)
        self.ui.mirrorTemplateButton.setEnabled(model_seed is not None)
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
        self._placeInFolder(curve_node, isLeft)
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
        if self._parameterNode is not None:
            self._parameterNode.ctVolume = volume_node
        if volume_node is None:
            return
        
        # hide 3D rendering of previous Volume
        if not self._vrDisplayNode is None:
            print("remove existing volume rendering node")
            slicer.modules.volumerendering.logic().RemoveVolumeRenderingDisplayNode(self._vrDisplayNode)

        self._applyVolumeRendering(volume_node)
        self._centerViewAnterior(volume_node)

        # determine minimal and maximal HU Values
        volumeArray = slicer.util.arrayFromVolume(volume_node).flatten()

        hu_min = np.min(volumeArray)
        hu_max = np.max(volumeArray)

        self.ui.huMinSpinBox.minimum = hu_min
        self.ui.huMaxSpinBox.minimum = hu_min
        self.ui.huMinSpinBox.maximum = hu_max
        self.ui.huMaxSpinBox.maximum = hu_max

    def onHUShiftChanged(self, shift_hu: float) -> None:
        """
            Get's called when the user changes the HU-Shift. Shifts the 
            Preset's values by the amount given
        """

        # get Display Node and Properties
        if self._vrDisplayNode is None:
            print("No vrDisplayNode set")
            return
        
        volPropNode = self._vrDisplayNode.GetVolumePropertyNode()
        
        if volPropNode is None:
            return
        
        # get Preset values
        vrLogic = slicer.modules.volumerendering.logic()
        presetNode = vrLogic.GetPresetByName("CT-Bone")
        
        if presetNode is None:
            return
        
        presetTransferFunction = presetNode.GetScalarOpacity()
        gradientOpacityTransferFunction = volPropNode.GetScalarOpacity()
        
        values = [0,0,0,0]
        for i in range(gradientOpacityTransferFunction.GetSize()):
            presetTransferFunction.GetNodeValue(i, values)
            values[0] += shift_hu
            gradientOpacityTransferFunction.SetNodeValue(i, values)

        """"
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
        """

    def _applyVolumeRendering(self, volume_node) -> None:
        from PSOILib import helperfunctions

        self._vrDisplayNode = helperfunctions.showVolumeRendering(volume_node, preset="CT-Bone")

        # DisplayNode für den HU-Shift-Mechanismus referenzieren
        # vrLogic = slicer.modules.volumerendering.logic()
        # self._vrDisplayNode = vrLogic.CreateDefaultVolumeRenderingNodes(volume_node)

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
        # Show / hide mode-specific rows
        self.ui.labelContraPosition.setVisible(is_contra)
        
        self.ui.positionContralateralWidget.setVisible(is_contra)
        self.ui.labelModelSeed.setVisible(is_model)
        self.ui.modelSeedWidget.setVisible(is_model)
        # Enable "Position Mirrored" only when the contralateral segmentation exists
        if is_contra and self._parameterNode is not None:
            isLeft = self._parameterNode.sideIsLeft
            contra_node = (self._parameterNode.segmentationNodeRight if isLeft
                           else self._parameterNode.segmentationNodeLeft)
            self.ui.positionContralateralButton.setEnabled(contra_node is not None)
        else:
            self.ui.positionContralateralButton.setEnabled(False)

    def onModelSeedChanged(self, node) -> None:
        self.ui.positionModelButton.setEnabled(node is not None)
        self.ui.mirrorTemplateButton.setEnabled(node is not None)
        if self._parameterNode is None:
            return
        isLeft = self._parameterNode.sideIsLeft
        if isLeft:
            self._parameterNode.modelSeedNodeLeft  = node
        else:
            self._parameterNode.modelSeedNodeRight = node

    def onMirrorTemplateToggled(self, checked: bool) -> None:
        """Spiegelt das Template-Volumen entlang der Mediansagittalebene (X=0) und härtet
        den Spiegelungs-Transform sofort, damit die Normalen korrekt bleiben.

        Da eine Reflexion involutorisch ist (M² = I), ist das Vorgehen für Ein- und
        Ausschalten identisch: den Mirror-Transform anwenden und härten.  T_centering
        wird danach mit dem neuen Schwerpunkt des gehärteten Volumens aktualisiert.
        """
        if self._parameterNode is None:
            return
        isLeft      = self._parameterNode.sideIsLeft
        seg_node    = self.ui.modelSeedSelector.currentNode()
        if seg_node is None:
            self.ui.mirrorTemplateButton.blockSignals(True)
            self.ui.mirrorTemplateButton.setChecked(False)
            self.ui.mirrorTemplateButton.blockSignals(False)
            return

        side_suffix    = "L" if isLeft else "R"
        centering_name = f"ModelSeedCentering_{side_suffix}"
        tf_attr        = "modelSeedTransformLeft" if isLeft else "modelSeedTransformRight"
        transform_node = getattr(self._parameterNode, tf_attr, None)

        centering_node = slicer.mrmlScene.GetFirstNodeByName(centering_name)
        if centering_node is None:
            slicer.util.warningDisplay(_("Please click 'Position Model' first."))
            self.ui.mirrorTemplateButton.blockSignals(True)
            self.ui.mirrorTemplateButton.setChecked(False)
            self.ui.mirrorTemplateButton.blockSignals(False)
            return

        # ── Segment aus der Positionierungs-Kette lösen ────────────────────
        seg_node.SetAndObserveTransformNodeID(None)

        # ── Reflexionsmatrix X → −X anwenden und sofort härten ─────────────
        tmp_mirror = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLLinearTransformNode", "_TmpMirrorHarden"
        )
        mat_m = vtk.vtkMatrix4x4()
        mat_m.Identity()
        mat_m.SetElement(0, 0, -1.0)
        tmp_mirror.SetMatrixTransformToParent(mat_m)
        seg_node.SetAndObserveTransformNodeID(tmp_mirror.GetID())
        slicer.modules.transforms.logic().hardenTransform(seg_node)
        slicer.mrmlScene.RemoveNode(tmp_mirror)

        # ── Neuen Schwerpunkt nach dem Härten bestimmen ─────────────────────
        seg_bounds = [0.0] * 6
        seg_node.GetRASBounds(seg_bounds)
        if seg_bounds[0] < seg_bounds[1]:
            seg_center = np.array([
                (seg_bounds[0] + seg_bounds[1]) / 2,
                (seg_bounds[2] + seg_bounds[3]) / 2,
                (seg_bounds[4] + seg_bounds[5]) / 2,
            ])
        else:
            seg_center = np.zeros(3)

        # ── T_centering mit neuem Schwerpunkt aktualisieren ─────────────────
        mat_c = vtk.vtkMatrix4x4()
        mat_c.Identity()
        mat_c.SetElement(0, 3, float(-seg_center[0]))
        mat_c.SetElement(1, 3, float(-seg_center[1]))
        mat_c.SetElement(2, 3, float(-seg_center[2]))
        centering_node.SetMatrixTransformToParent(mat_c)

        # ── Hierarchie wiederherstellen: seg → T_centering → T_main ─────────
        centering_node.SetAndObserveTransformNodeID(
            transform_node.GetID() if transform_node else None
        )
        seg_node.SetAndObserveTransformNodeID(centering_node.GetID())

    def onLoadTemplateButton(self) -> None:
        path = qt.QFileDialog.getOpenFileName(
            slicer.util.mainWindow(),
            _("Load template segmentation"),
            "",
            "Segmentation files (*.seg.nrrd *.nrrd *.nii *.nii.gz);;All files (*)",
        )
        if not path:
            return
        node = slicer.util.loadSegmentation(path)
        if node is None:
            slicer.util.errorDisplay(_("Could not load segmentation from file."))
            return
        self.ui.modelSeedSelector.setCurrentNode(node)

    def onPositionModelButton(self) -> None:
        if self._parameterNode is None:
            return
        isLeft      = self._parameterNode.sideIsLeft
        seg_node    = self.ui.modelSeedSelector.currentNode()
        volume_node = self.ui.ctVolumeSelector.currentNode()
        if seg_node is None:
            slicer.util.warningDisplay(_("Please select a template segmentation first."))
            return

        side_suffix = "L" if isLeft else "R"

        # ── Haupttransform (sichtbar, interaktiv) ──────────────────────────
        tf_attr = "modelSeedTransformLeft" if isLeft else "modelSeedTransformRight"
        existing_tf = getattr(self._parameterNode, tf_attr, None)
        if existing_tf is not None and slicer.mrmlScene.IsNodePresent(existing_tf):
            transform_node = existing_tf
        else:
            transform_node = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLLinearTransformNode", f"ModelSeedTransform_{side_suffix}"
            )
            transform_node.CreateDefaultDisplayNodes()
            setattr(self._parameterNode, tf_attr, transform_node)
        self._placeInFolder(transform_node, isLeft)

        # ── Zentrierungs-Transform (unsichtbar, Kind von T_main) ────────────
        centering_name = f"ModelSeedCentering_{side_suffix}"
        centering_node = slicer.mrmlScene.GetFirstNodeByName(centering_name)
        if centering_node is None:
            centering_node = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLLinearTransformNode", centering_name
            )
            centering_node.CreateDefaultDisplayNodes()
        self._placeInFolder(centering_node, isLeft)

        # ── Zielposition: Orbital-Ebenen-Centroid + 20 mm posterior ─────────
        plane = (self._parameterNode.planeModelLeft if isLeft
                 else self._parameterNode.planeModelRight)
        if plane is not None:
            centroid, normal = self.logic._getPlaneFromModel(plane)
            if volume_node is not None:
                posterior = self.logic._ensurePosteriorDirection(normal, centroid, volume_node)
            else:
                posterior = normal
            target = centroid + 20.0 * posterior
        elif volume_node is not None:
            bounds = [0.0] * 6
            volume_node.GetRASBounds(bounds)
            centroid = np.array([(bounds[0]+bounds[1])/2,
                                  (bounds[2]+bounds[3])/2,
                                  (bounds[4]+bounds[5])/2])
            target = centroid
        else:
            target = np.zeros(3)

        # ── Natürlichen Mittelpunkt der Template-Segmentierung bestimmen ─────
        # Zuerst alle Transforms lösen, damit GetRASBounds unkompensierte Koordinaten liefert.
        seg_node.SetAndObserveTransformNodeID(None)
        seg_bounds = [0.0] * 6
        seg_node.GetRASBounds(seg_bounds)
        if seg_bounds[0] < seg_bounds[1]:   # gültige Bounds
            seg_center = np.array([
                (seg_bounds[0] + seg_bounds[1]) / 2,
                (seg_bounds[2] + seg_bounds[3]) / 2,
                (seg_bounds[4] + seg_bounds[5]) / 2,
            ])
        else:
            seg_center = np.zeros(3)

        # ── Zwei-Transform-Hierarchie aufbauen ───────────────────────────────
        # T_centering: [I | -seg_center]  →  verschiebt Template-Mittelpunkt auf Ursprung
        mat_c = vtk.vtkMatrix4x4()
        mat_c.Identity()
        mat_c.SetElement(0, 3, float(-seg_center[0]))
        mat_c.SetElement(1, 3, float(-seg_center[1]))
        mat_c.SetElement(2, 3, float(-seg_center[2]))
        centering_node.SetMatrixTransformToParent(mat_c)

        # T_main: [I | target]  →  Translations-Vektor = Zielposition = Center of Transformation
        mat_m = vtk.vtkMatrix4x4()
        mat_m.Identity()
        mat_m.SetElement(0, 3, float(target[0]))
        mat_m.SetElement(1, 3, float(target[1]))
        mat_m.SetElement(2, 3, float(target[2]))
        transform_node.SetMatrixTransformToParent(mat_m)

        # Hierarchie: seg_node → T_centering → T_main
        centering_node.SetAndObserveTransformNodeID(transform_node.GetID())
        seg_node.SetAndObserveTransformNodeID(centering_node.GetID())

        # T_centering unsichtbar (keine Handles)
        c_disp = centering_node.GetDisplayNode()
        if c_disp:
            c_disp.SetEditorVisibility(False)

        # T_main: Handles nur in 2D-Slice-Views
        disp = transform_node.GetDisplayNode()
        disp.SetEditorVisibility(True)
        # 3D-Handles explizit deaktivieren (Slicer-Default wäre True)
        disp.SetEditorTranslationEnabled(False)
        disp.SetEditorRotationEnabled(False)
        disp.SetEditorScalingEnabled(False)
        # 2D-Slice-Handles aktivieren
        disp.SetEditorTranslationSliceEnabled(True)
        disp.SetEditorRotationSliceEnabled(True)
        disp.SetEditorScalingSliceEnabled(True)
        disp.SetScaleHandleComponentVisibilitySlice([True, True, True, False])
        disp.SetTranslationHandleComponentVisibilitySlice([True, True, True, True])

        slicer.app.layoutManager().threeDWidget(0).threeDView().resetFocalPoint()

    def onPositionContralateralButton(self) -> None:
        """Spiegelt das kontralaterale Segment und positioniert die Spiegelung
        mit interaktiven 2D-Handles – analog zu onPositionModelButton().

        Das erzeugte 'ContraPositioned_{s}'-Segment wird beim nächsten Klick
        auf 'Segment Volume' (Modus 1) statt der automatischen Spiegelung als
        FM-Seed verwendet.
        """
        if self._parameterNode is None:
            return
        isLeft  = self._parameterNode.sideIsLeft
        s       = "Left" if isLeft else "Right"
        side_suffix = "L" if isLeft else "R"

        contra_node = (self._parameterNode.segmentationNodeRight if isLeft
                       else self._parameterNode.segmentationNodeLeft)
        if contra_node is None:
            slicer.util.warningDisplay(_("Bitte zuerst die Gegenseite segmentieren."))
            return

        # Gegenseiten-Segment-ID ermitteln
        contra_side  = not isLeft
        contra_seg_id = self._segIds.get(contra_side)
        if contra_seg_id is None:
            seg = contra_node.GetSegmentation()
            for i in range(seg.GetNumberOfSegments()):
                if seg.GetNthSegment(i).GetName() == "IntraorbitalVolume":
                    contra_seg_id = seg.GetNthSegmentID(i)
                    break
        if contra_seg_id is None:
            slicer.util.warningDisplay(
                _("Kein IntraorbitalVolume-Segment auf der Gegenseite gefunden.")
            )
            return

        volume_node = self.ui.ctVolumeSelector.currentNode()

        import SimpleITK as sitk
        import sitkUtils

        # Gegenseiten-Segment in Labelmap exportieren und spiegeln (L-R)
        seg_id_arr = vtk.vtkStringArray()
        seg_id_arr.InsertNextValue(contra_seg_id)
        tmp_lm = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLLabelMapVolumeNode", "_TmpContraLM_Pos"
        )
        try:
            slicer.modules.segmentations.logic().ExportSegmentsToLabelmapNode(
                contra_node, seg_id_arr, tmp_lm, volume_node
            )
            contra_sitk = sitkUtils.PullVolumeFromSlicer(tmp_lm)
        finally:
            slicer.mrmlScene.RemoveNode(tmp_lm)

        contra_arr = sitk.GetArrayFromImage(contra_sitk)
        mirrored = np.ascontiguousarray(np.flip(contra_arr, axis=2))
        mirrored_sitk = sitk.GetImageFromArray(mirrored)
        mirrored_sitk.CopyInformation(contra_sitk)

        # Positionier-Node anlegen oder wiederverwenden
        pos_node_attr = f"contraPositionedNode{s}"
        existing_pos  = getattr(self._parameterNode, pos_node_attr, None)
        if existing_pos is not None and slicer.mrmlScene.IsNodePresent(existing_pos):
            existing_pos.SetAndObserveTransformNodeID(None)
            existing_pos.GetSegmentation().RemoveAllSegments()
            pos_node = existing_pos
        else:
            pos_node = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLSegmentationNode", f"ContraPositioned_{side_suffix}"
            )
            pos_node.CreateDefaultDisplayNodes()
            if volume_node:
                pos_node.SetReferenceImageGeometryParameterFromVolumeNode(volume_node)
            setattr(self._parameterNode, pos_node_attr, pos_node)
        self._placeInFolder(pos_node, isLeft)

        # Gespiegelte Maske importieren
        lm_tmp = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLLabelMapVolumeNode", "_TmpMirroredLM"
        )
        sitkUtils.PushVolumeToSlicer(mirrored_sitk, lm_tmp)
        try:
            slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(
                lm_tmp, pos_node
            )
        finally:
            slicer.mrmlScene.RemoveNode(lm_tmp)

        # Darstellung: blau, halbtransparent
        disp_seg = pos_node.GetDisplayNode()
        if disp_seg:
            disp_seg.SetOpacity3D(0.4)
            disp_seg.SetOpacity2DFill(0.3)
        pos_seg = pos_node.GetSegmentation()
        if pos_seg.GetNumberOfSegments() > 0:
            pos_seg.GetNthSegment(0).SetColor(0.2, 0.6, 1.0)

        # ── Haupt-Transform (interaktiv) ──────────────────────────────────
        tf_attr    = f"contraPositionedTransform{s}"
        existing_tf = getattr(self._parameterNode, tf_attr, None)
        if existing_tf is not None and slicer.mrmlScene.IsNodePresent(existing_tf):
            transform_node = existing_tf
        else:
            transform_node = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLLinearTransformNode", f"ContraPositionedTransform_{side_suffix}"
            )
            transform_node.CreateDefaultDisplayNodes()
            setattr(self._parameterNode, tf_attr, transform_node)
        self._placeInFolder(transform_node, isLeft)

        # ── Zentrierungs-Transform (unsichtbar) ───────────────────────────
        centering_name = f"ContraPositionedCentering_{side_suffix}"
        centering_node = slicer.mrmlScene.GetFirstNodeByName(centering_name)
        if centering_node is None:
            centering_node = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLLinearTransformNode", centering_name
            )
            centering_node.CreateDefaultDisplayNodes()
        self._placeInFolder(centering_node, isLeft)

        # ── Zielposition: Orbital-Ebenen-Centroid + 20 mm posterior ───────
        plane = (self._parameterNode.planeModelLeft if isLeft
                 else self._parameterNode.planeModelRight)
        if plane is not None:
            centroid, normal = self.logic._getPlaneFromModel(plane)
            if volume_node is not None:
                posterior = self.logic._ensurePosteriorDirection(normal, centroid, volume_node)
            else:
                posterior = normal
            target = centroid + 20.0 * posterior
        elif volume_node is not None:
            bounds = [0.0] * 6
            volume_node.GetRASBounds(bounds)
            target = np.array([
                (bounds[0]+bounds[1])/2,
                (bounds[2]+bounds[3])/2,
                (bounds[4]+bounds[5])/2,
            ])
        else:
            target = np.zeros(3)

        # Mittelpunkt des gespiegelten Segments
        seg_bounds = [0.0] * 6
        pos_node.GetRASBounds(seg_bounds)
        if seg_bounds[0] < seg_bounds[1]:
            seg_center = np.array([
                (seg_bounds[0]+seg_bounds[1])/2,
                (seg_bounds[2]+seg_bounds[3])/2,
                (seg_bounds[4]+seg_bounds[5])/2,
            ])
        else:
            seg_center = np.zeros(3)

        # ── Zwei-Transform-Hierarchie ──────────────────────────────────────
        mat_c = vtk.vtkMatrix4x4()
        mat_c.Identity()
        mat_c.SetElement(0, 3, float(-seg_center[0]))
        mat_c.SetElement(1, 3, float(-seg_center[1]))
        mat_c.SetElement(2, 3, float(-seg_center[2]))
        centering_node.SetMatrixTransformToParent(mat_c)

        mat_m = vtk.vtkMatrix4x4()
        mat_m.Identity()
        mat_m.SetElement(0, 3, float(target[0]))
        mat_m.SetElement(1, 3, float(target[1]))
        mat_m.SetElement(2, 3, float(target[2]))
        transform_node.SetMatrixTransformToParent(mat_m)

        centering_node.SetAndObserveTransformNodeID(transform_node.GetID())
        pos_node.SetAndObserveTransformNodeID(centering_node.GetID())

        # T_centering unsichtbar (keine Handles)
        c_disp = centering_node.GetDisplayNode()
        if c_disp:
            c_disp.SetEditorVisibility(False)

        # T_main: Handles nur in 2D-Slice-Views
        disp = transform_node.GetDisplayNode()
        disp.SetEditorVisibility(True)
        disp.SetEditorTranslationEnabled(False)
        disp.SetEditorRotationEnabled(False)
        disp.SetEditorScalingEnabled(False)
        disp.SetEditorTranslationSliceEnabled(True)
        disp.SetEditorRotationSliceEnabled(True)
        disp.SetEditorScalingSliceEnabled(True)
        disp.SetScaleHandleComponentVisibilitySlice([True, True, True, False])
        disp.SetTranslationHandleComponentVisibilitySlice([True, True, True, True])

        slicer.app.layoutManager().threeDWidget(0).threeDView().resetFocalPoint()

    def onExportResultsButton(self) -> None:
        """Exportiert Segmentierungsparameter und -ergebnisse beider Seiten als Excel-Datei
        in das Verzeichnis des CT-Volumes (results_orbital_volume.xlsx)."""
        import os

        if self._parameterNode is None:
            slicer.util.warningDisplay(_("No parameter node active."))
            return

        try:
            import openpyxl
        except ImportError:
            slicer.util.pip_install("openpyxl")
            import openpyxl

        

        # Zielverzeichnis aus dem CT-Volume ableiten
        volume_node = self._parameterNode.ctVolume
        ct_dir = None
        if volume_node:
            sn = volume_node.GetStorageNode()
            if sn and sn.GetFileName():
                ct_dir = os.path.dirname(sn.GetFileName())
        if not ct_dir:
            ct_dir = qt.QFileDialog.getExistingDirectory(
                slicer.util.mainWindow(), _("Select output directory")
            )
            if not ct_dir:
                return

        excel_path = os.path.join(ct_dir, "results_orbital_volume.xlsx")

        # Collect all module PNs now (needed for deletion logic below too)
        all_pn_nodes = []
        _col = slicer.mrmlScene.GetNodesByClass("vtkMRMLScriptedModuleNode")
        _col.InitTraversal()
        _node = _col.GetNextItemAsObject()
        while _node:
            if _node.GetAttribute("ModuleName") == self.moduleName:
                all_pn_nodes.append(_node)
            _node = _col.GetNextItemAsObject()
        export_pn_names = {(n.GetName() or "–") for n in all_pn_nodes}

        # Existierende Datei laden oder neu erstellen
        if os.path.exists(excel_path):
            wb = openpyxl.load_workbook(excel_path)
            ws = wb.active
            # Vorhandene Zeilen für alle zu exportierenden PNs entfernen (Update-Semantik)
            rows_to_delete = [
                row_idx for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2)
                if row[0] in export_pn_names  # Spalte "Parameter-Node"
            ]
            for row_idx in reversed(rows_to_delete):
                ws.delete_rows(row_idx)
        else:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Intraorbital Volume"
            headers = [
                "Parameter-Node", "CT-Volume", "Seite", "Seed-Modus",
                "HU Min", "HU Max",
                "Stopping Value (FM Limit)", "Speed Sigma (σ)",
                "Posteriorer Boost", "Satelliten entfernen",
                "Min. Satelliten-Ø (mm)", "Intraorbitalvolumen (ml)",
                "Gegenseite / Seite",
            ]
            ws.append(headers)
            # Spaltenbreiten
            for col, width in enumerate([28, 22, 8, 24, 8, 8, 24, 16, 18, 22, 22, 24, 18], start=1):
                ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = width

        seed_mode_labels = {0: "Manuell", 1: "Gegenseite gespiegelt", 2: "Modellbasiert"}

        for raw_pn in all_pn_nodes:
            pn = OrbitalVolumeWorkflowModuleParameterNode(raw_pn)
            pn_name = raw_pn.GetName() if raw_pn.GetName() else "–"

            # CT volume and output directory
            pn_vol_node = pn.ctVolume
            pn_ct_name = pn_vol_node.GetName() if pn_vol_node else "–"

            # Compute volumes for both sides
            vols = {}
            for isLeft in [True, False]:
                seg_node = (pn.segmentationNodeLeft if isLeft else pn.segmentationNodeRight)
                if seg_node is None:
                    vols[isLeft] = None
                    continue
                # Use cached value if this is the active PN
                vol = None
                if (pn.parameterNode is self._parameterNode.parameterNode):
                    vol = self._segVolumes.get(isLeft)
                if vol is None:
                    seg = seg_node.GetSegmentation()
                    if seg.GetNumberOfSegments() > 0:
                        seg_id = seg.GetNthSegmentID(0)
                        vol, _voxels = self._calculateVolumeFromSegment(seg_node, seg_id)
                vols[isLeft] = vol

            for isLeft in [True, False]:
                seg_node = (pn.segmentationNodeLeft if isLeft else pn.segmentationNodeRight)
                if seg_node is None:
                    continue

                s = "Left" if isLeft else "Right"
                vol_ml    = vols[isLeft]
                other_vol = vols[not isLeft]

                if vol_ml and other_vol and vol_ml > 0:
                    ratio = vol_ml / other_vol
                else:
                    ratio = None

                row = [
                    pn_name,
                    pn_ct_name,
                    "Links" if isLeft else "Rechts",
                    seed_mode_labels.get(pn.__getattribute__(f"seedMode{s}"), "–"),
                    pn.__getattribute__(f"huMin{s}"),
                    pn.__getattribute__(f"huMax{s}"),
                    pn.__getattribute__(f"stoppingValue{s}"),
                    pn.__getattribute__(f"speedSigma{s}"),
                    pn.__getattribute__(f"posteriorBoost{s}"),
                    "Ja" if pn.__getattribute__(f"removeSatellites{s}") else "Nein",
                    pn.__getattribute__(f"satelliteDiam{s}"),
                    round(vol_ml, 3) if vol_ml is not None else "–",
                    ratio if ratio is not None else "–",
                ]
                ws.append(row)

                if ratio is not None:
                    ws.cell(row=ws.max_row, column=13).number_format = "0.0%"

        from openpyxl.styles import Font, Color, Border, Side

        # Allen Zellen die Schriftfarbe "auto" zuweisen, damit es auch im Dark mode
        # richtig dargestellt wird
        cell_font = Font(name="Liberations Sans", color=Color(auto=True))
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.font = cell_font

        # Titelzeile fett darstellen
        header_font = Font(name="Liberations Sans", color=Color(auto=True), bold=True)
        header_border = Border(bottom=Side(border_style="thin",color=Color(auto=True)))   

        for cell in ws[1]:
            cell.font = header_font
            cell.border = header_border

        # Ergebnis epeichern
        wb.save(excel_path)
        slicer.util.infoDisplay(
            _("Results saved to:\n{path}").format(path=excel_path)
        )

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
            self._placeInFolder(model_node, isLeft)

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

            # Bestehende Nodes nur wiederverwenden wenn sie zum aktuellen CT gehören.
            # Bei neuem ParameterNode oder gewechseltem CT-Volume neue Nodes anlegen.
            ct_prefix = volume_node.GetName()
            if existing_seg is not None and ct_prefix not in existing_seg.GetName():
                existing_seg  = None
                existing_seed = None

            # --- Seed-Modus bestimmen ---
            seed_mode = (1 if self.ui.rbSeedContralateral.isChecked()
                         else 2 if self.ui.rbSeedModelBased.isChecked() else 0)

            # Modus 1: Gegenseite spiegeln (ggf. mit manuell positioniertem Spiegel-Segment)
            contra_seg_node = None
            contra_seg_id   = None
            model_seed_node = None
            model_seed_id   = None
            if seed_mode == 1:
                s = "Left" if isLeft else "Right"
                pos_node = getattr(self._parameterNode, f"contraPositionedNode{s}", None)
                pos_tf   = getattr(self._parameterNode, f"contraPositionedTransform{s}", None)
                if (pos_node is not None and slicer.mrmlScene.IsNodePresent(pos_node)
                        and pos_tf is not None and slicer.mrmlScene.IsNodePresent(pos_tf)):
                    # Verwende das manuell positionierte Spiegel-Segment als FM-Seed
                    model_seed_node = pos_node
                    pos_seg = pos_node.GetSegmentation()
                    if pos_seg.GetNumberOfSegments() > 0:
                        model_seed_id = pos_seg.GetNthSegmentID(0)
                else:
                    # Automatische Spiegelung (Standard-Pfad)
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
            if seed_mode == 2:
                model_seed_node = (self._parameterNode.modelSeedNodeLeft if isLeft
                                   else self._parameterNode.modelSeedNodeRight)
                if model_seed_node is not None:
                    seg = model_seed_node.GetSegmentation()
                    if seg.GetNumberOfSegments() > 0:
                        model_seed_id = seg.GetNthSegmentID(0)

            # Posteriore Cutoff-Ebene (gemeinsam für beide Seiten)
            cutoff_node = self._parameterNode.posteriorCutoffNode
            if cutoff_node is None or cutoff_node.GetNumberOfControlPoints() == 0:
                raise ValueError(
                    _("Bitte zuerst einen posterioren Cutoff-Punkt setzen "
                      "(Schaltfläche 'P' im Abschnitt 'Posteriore Cutoff-Ebene').")
                )
            _cutoff_pt = [0.0, 0.0, 0.0]
            cutoff_node.GetNthControlPointPositionWorld(0, _cutoff_pt)
            posterior_cutoff_ras = np.array(_cutoff_pt)

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
                posterior_cutoff_ras=posterior_cutoff_ras,
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

            # Place result nodes and any preview nodes in the side folder
            self._placeInFolder(result["segmentation_node"], isLeft)
            self._placeInFolder(result["seed_node"], isLeft)

            _seg_prefix = result["segmentation_node"].GetName().replace("_IntraorbitalSeg", "") + "_"

            for _pname in [f"{_seg_prefix}ContraMirror_Full", f"{_seg_prefix}ModelSeed"]:
                _pnode = slicer.mrmlScene.GetFirstNodeByName(_pname)
                if _pnode is not None:
                    self._placeInFolder(_pnode, isLeft)

            # Keep the manual selector in sync
            wasBlocked = self.ui.segNodeSelector.blockSignals(True)

            self.ui.segNodeSelector.setCurrentNode(result["segmentation_node"])
            self.ui.segNodeSelector.blockSignals(wasBlocked)

            # Finales Volumen aus dem fertigen Segment messen (nach Smoothing + Satellitenentfernung)
            seg_node_final = result["segmentation_node"]
            seg_id_final   = result["segment_id"]
            final_vol_ml, final_vox = self._calculateVolumeFromSegment(seg_node_final, seg_id_final)

            self._segVolumes[isLeft] = final_vol_ml

            text = (
                _("<b>Intraorbital volume: {vol:.2f} ml</b><br>"
                  "Voxels: {vox}<br>"
                  "Seed offset: {off:.1f} mm &nbsp;|&nbsp; "
                  "HU at seed: {hu:.0f}").format(
                    vol=final_vol_ml,
                    vox=f"{final_vox:,}",
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

            # Interaction Handles des Modell-Seed-Transforms ausblenden
            tf_attr = "modelSeedTransformLeft" if isLeft else "modelSeedTransformRight"
            model_tf = getattr(self._parameterNode, tf_attr, None)
            if model_tf is not None and slicer.mrmlScene.IsNodePresent(model_tf):
                disp = model_tf.GetDisplayNode()
                if disp:
                    disp.SetEditorVisibility(False)

            # Interaction Handles des Contralateral-Positionierungs-Transforms ausblenden
            s = "Left" if isLeft else "Right"
            contra_pos_tf = getattr(self._parameterNode, f"contraPositionedTransform{s}", None)
            if contra_pos_tf is not None and slicer.mrmlScene.IsNodePresent(contra_pos_tf):
                disp = contra_pos_tf.GetDisplayNode()
                if disp:
                    disp.SetEditorVisibility(False)

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
        # if self.ui.stepsToolbox.currentIndex < self.ui.stepsToolbox.count - 1:
        #     self.ui.stepsToolbox.setCurrentIndex(self.ui.stepsToolbox.currentIndex + 1)
        return

    def onClearSideButton(self) -> None:
        """Löscht alle Segmentierungs-Nodes der aktuell gewählten Seite nach Bestätigung."""
        if self._parameterNode is None:
            return
        isLeft = self._parameterNode.sideIsLeft
        side_name = "Links" if isLeft else "Rechts"
        s = "Left" if isLeft else "Right"
        side_suffix = "L" if isLeft else "R"

        answer = qt.QMessageBox.question(
            slicer.util.mainWindow(),
            _("Segmentierung löschen"),
            _("Alle Segmentierungs-Nodes für Seite {side} wirklich löschen?\n"
              "(Seed, Transforms, Segmentierung, Preview-Nodes)").format(side=side_name),
            qt.QMessageBox.Yes | qt.QMessageBox.No,
            qt.QMessageBox.No,
        )
        if answer != qt.QMessageBox.Yes:
            return

        def _remove(node):
            if node is not None and slicer.mrmlScene.IsNodePresent(node):
                slicer.mrmlScene.RemoveNode(node)

        # Segmentierung + abgeleitete Preview-Nodes (benannte Hilfsknoten vom FM)
        seg_node = getattr(self._parameterNode, f"segmentationNode{s}", None)
        if seg_node is not None:
            prefix = seg_node.GetName().replace("_IntraorbitalSeg", "") + "_"
            for preview_name in [
                f"{prefix}ContraMirror_Full",
                f"{prefix}ContraMirror_Shrunk",
                f"{prefix}ModelSeed_full",
                f"{prefix}ModelSeed",
            ]:
                _remove(slicer.mrmlScene.GetFirstNodeByName(preview_name))
        _remove(seg_node)
        setattr(self._parameterNode, f"segmentationNode{s}", None)

        # Seed-Fiducial
        _remove(getattr(self._parameterNode, f"seedNode{s}", None))
        setattr(self._parameterNode, f"seedNode{s}", None)

        # Modell-Seed + Transform-Kette (Centering + Haupt-Transform)
        model_seg = getattr(self._parameterNode, f"modelSeedNode{s}", None)
        if model_seg is not None and slicer.mrmlScene.IsNodePresent(model_seg):
            centering_id = model_seg.GetTransformNodeID()
            if centering_id:
                centering_node = slicer.mrmlScene.GetNodeByID(centering_id)
                model_tf = getattr(self._parameterNode, f"modelSeedTransform{s}", None)
                if centering_node and centering_node != model_tf:
                    _remove(centering_node)
        _remove(model_seg)
        setattr(self._parameterNode, f"modelSeedNode{s}", None)

        model_tf = getattr(self._parameterNode, f"modelSeedTransform{s}", None)
        _remove(model_tf)
        setattr(self._parameterNode, f"modelSeedTransform{s}", None)

        # Centering-Transform (Fallback, falls noch vorhanden)
        _remove(slicer.mrmlScene.GetFirstNodeByName(f"ModelSeedCentering_{side_suffix}"))

        # Positioniertes Gegenseiten-Segment + Transform-Kette
        contra_pos_node = getattr(self._parameterNode, f"contraPositionedNode{s}", None)
        if contra_pos_node is not None and slicer.mrmlScene.IsNodePresent(contra_pos_node):
            centering_id = contra_pos_node.GetTransformNodeID()
            if centering_id:
                centering_node = slicer.mrmlScene.GetNodeByID(centering_id)
                contra_pos_tf = getattr(self._parameterNode, f"contraPositionedTransform{s}", None)
                if centering_node and centering_node != contra_pos_tf:
                    _remove(centering_node)
        _remove(contra_pos_node)
        setattr(self._parameterNode, f"contraPositionedNode{s}", None)

        contra_pos_tf = getattr(self._parameterNode, f"contraPositionedTransform{s}", None)
        _remove(contra_pos_tf)
        setattr(self._parameterNode, f"contraPositionedTransform{s}", None)

        _remove(slicer.mrmlScene.GetFirstNodeByName(f"ContraPositionedCentering_{side_suffix}"))

        # Posteriorer Markup dieser Seite
        pm_node = getattr(self._parameterNode, f"posteriorMarkup{s}", None)
        if pm_node is not None:
            obs = self._posteriorMarkupObservers.get(isLeft)
            if obs is not None:
                pm_node.RemoveObserver(obs)
                self._posteriorMarkupObservers[isLeft] = None
        _remove(pm_node)
        setattr(self._parameterNode, f"posteriorMarkup{s}", None)

        # Observer vom Segmentierungsknoten lösen
        seg_obs = self._segObservers.get(isLeft)
        if self._segNodes.get(isLeft) is not None and seg_obs is not None:
            try:
                self._segNodes[isLeft].RemoveObserver(seg_obs)
            except Exception:
                pass
        self._segObservers[isLeft] = None

        # Widget-Zustand zurücksetzen
        self._segTexts[isLeft] = ""
        self._segVolumes[isLeft] = None
        self._segNodes[isLeft] = None
        self._segIds[isLeft] = None

        self.ui.segmentationResultLabel.setText("")
        self.ui.openSegmentEditorButton.setEnabled(False)
        wasBlocked = self.ui.segNodeSelector.blockSignals(True)
        self.ui.segNodeSelector.setCurrentNode(None)
        self.ui.segNodeSelector.blockSignals(wasBlocked)

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
            self._placeInFolder(node, isLeft)

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

    def onSegNodeSelectorChanged(self, node) -> None:
        if self._parameterNode is None:
            return
        isLeft = self._parameterNode.sideIsLeft
        s = "Left" if isLeft else "Right"
        setattr(self._parameterNode, f"segmentationNode{s}", node)
        if node is not None:
            self.ui.openSegmentEditorButton.setEnabled(True)
            seg = node.GetSegmentation()
            seg_id = None
            for i in range(seg.GetNumberOfSegments()):
                if seg.GetNthSegment(i).GetName() == "IntraorbitalVolume":
                    seg_id = seg.GetNthSegmentID(i)
                    break
            self._segNodes[isLeft] = node
            self._segIds[isLeft]   = seg_id
            # Calculate and display volume immediately
            if seg_id is not None:
                try:
                    vol_ml, voxel_count = self._calculateVolumeFromSegment(node, seg_id)
                    self._segVolumes[isLeft] = vol_ml
                    text = (
                        _("<b>Intraorbital volume: {vol:.2f} ml</b><br>"
                          "Voxels: {vox}").format(vol=vol_ml, vox=f"{voxel_count:,}")
                    )
                    self._segTexts[isLeft] = text
                    self.ui.segmentationResultLabel.setText(text)
                except Exception:
                    pass
            else:
                self._segTexts[isLeft] = ""
                self.ui.segmentationResultLabel.setText("")
        else:
            self.ui.openSegmentEditorButton.setEnabled(False)
            self._segNodes[isLeft] = None
            self._segIds[isLeft]   = None
            self._segTexts[isLeft] = ""
            self._segVolumes[isLeft] = None
            self.ui.segmentationResultLabel.setText("")

    def onCutoffNodeSelectorChanged(self, node) -> None:
        """Wird aufgerufen wenn der Nutzer einen anderen Cutoff-Node aus dem Dropdown wählt."""
        if self._parameterNode is None:
            return
        old_node = self._parameterNode.posteriorCutoffNode
        if old_node is not node and self._cutoffMarkupObserver is not None:
            try:
                old_node.RemoveObserver(self._cutoffMarkupObserver)
            except Exception:
                pass
            self._cutoffMarkupObserver = None
        self._parameterNode.posteriorCutoffNode = node
        if node is not None and self._cutoffMarkupObserver is None:
            self._cutoffMarkupObserver = node.AddObserver(
                vtk.vtkCommand.ModifiedEvent,
                lambda c, e: self._onCutoffMarkupModified(),
            )
        self._onCutoffMarkupModified()

    def onPlaceCutoffButton(self) -> None:
        """Erstellt einen neuen Cutoff-Punkt (oder leert den bestehenden) und aktiviert
        den Platzierungs-Modus.  Wird ein bestehender Node bereits genutzt, werden nur
        die Kontrollpunkte entfernt – der Node selbst bleibt erhalten."""
        if self._parameterNode is None:
            return
        existing = self._parameterNode.posteriorCutoffNode
        if existing is not None and slicer.mrmlScene.IsNodePresent(existing):
            node = existing
            node.RemoveAllControlPoints()
        else:
            node = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLMarkupsFiducialNode", "PosteriorCutoff"
            )
            disp = node.GetDisplayNode()
            disp.SetSelectedColor(1.0, 0.4, 0.0)
            disp.SetColor(1.0, 0.4, 0.0)
            disp.SetGlyphScale(3.0)
            # Selector setzen → löst onCutoffNodeSelectorChanged aus
            # → setzt parameterNode.posteriorCutoffNode + Observer
            self.ui.cutoffNodeSelector.setCurrentNode(node)
            self._placeUnderCTVolume(node)
        node.SetMaximumNumberOfControlPoints(1)
        selNode   = slicer.app.applicationLogic().GetSelectionNode()
        selNode.SetActivePlaceNodeID(node.GetID())
        selNode.SetActivePlaceNodeClassName("vtkMRMLMarkupsFiducialNode")
        interNode = slicer.app.applicationLogic().GetInteractionNode()
        interNode.SetCurrentInteractionMode(slicer.vtkMRMLInteractionNode.Place)

    def onSelectIslandButton(self, clicked: bool):
        isLeft = self._parameterNode.sideIsLeft
        segmentationNode = self._parameterNode.segmentationNodeLeft if isLeft else self._parameterNode.segmentationNodeRight
        segment_id = segmentationNode.GetSegmentation().GetSegmentIdBySegmentName("IntraorbitalVolume")

        self.logic.segmentationKeepSelectedIsland(
            self._parameterNode.ctVolume,
            segmentationNode,
            segment_id
        )
    
    def onPerformCutoffButton(self, clicked: bool):
        isLeft = self._parameterNode.sideIsLeft
        segmentationNode = self._parameterNode.segmentationNodeLeft if isLeft else self._parameterNode.segmentationNodeRight
        segment_id = segmentationNode.GetSegmentation().GetSegmentIdBySegmentName("IntraorbitalVolume")
        mask_segment_id = segmentationNode.GetSegmentation().GetSegmentIdBySegmentName("Mask")

        self.logic.segmentationPerformCutoff(
            self._parameterNode.ctVolume,
            segmentationNode,
            segment_id,
            mask_segment_id
        )

    def _onCutoffMarkupModified(self) -> None:
        """Aktualisiert das Positions-Label der posterioren Cutoff-Ebene."""
        if self._parameterNode is None:
            return
        node = self._parameterNode.posteriorCutoffNode
        if node is None or node.GetNumberOfControlPoints() == 0:
            self.ui.cutoffPositionLabel.setText(_("nicht gesetzt"))
            return
        pt = [0.0, 0.0, 0.0]
        node.GetNthControlPointPositionWorld(0, pt)
        self.ui.cutoffPositionLabel.setText(
            f"R={pt[0]:.1f}  A={pt[1]:.1f}  S={pt[2]:.1f}"
        )

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
        self._segVolumes[isLeft] = volume_ml
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
            import vtk.util.numpy_support as nps # pyright: ignore[reportMissingImports]
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

    # ------------------------------------------------------------------
    # Theme-aware styling
    # 
    # obsolete, stylesheets break theming in dark mode
    # ------------------------------------------------------------------

    def _refreshButtonStyles(self, *_) -> None:
        """Reapplies theme-sensitive stylesheets after a palette/style change."""
        defaultPalette = slicer.app.palette()

        themable_widgets = [
            self.ui.pageVolumeSegmentation,
            self.ui.pageOrbitalSurface,
            self.ui.btnSideLeft,
            self.ui.btnSideRight
        ]

        # iterate through all widgets that need their colours adjusted
        for w in themable_widgets:
            if w.isChecked():
                # widget is selected/expanded, apply special palette
                activePalette = slicer.app.palette()
                activePalette.setColor(qt.QPalette.Button, qt.QColor("green"))
                activePalette.setColor(qt.QPalette.ButtonText, qt.QColor("white"))
                w.setPalette(activePalette)

                # make sure that the widgets children still have the default palette
                children = w.findChildren(qt.QWidget)
                for i in range(len(children)):
                    children[i].setPalette(defaultPalette)
            else:
                # widget not selected, apply default palette
                w.setPalette(defaultPalette)
           

    # ------------------------------------------------------------------
    # Subject Hierarchy helpers
    # ------------------------------------------------------------------

    def _shCTItemID(self) -> int:
        """SH item ID of the currently selected CT volume; creates the entry if missing.
        Falls back to scene root when no CT is selected."""
        shNode = slicer.vtkMRMLSubjectHierarchyNode.GetSubjectHierarchyNode(slicer.mrmlScene)
        ct_node = self.ui.ctVolumeSelector.currentNode()
        if ct_node is None:
            return shNode.GetSceneItemID()
        item_id = shNode.GetItemByDataNode(ct_node)
        if item_id == shNode.GetInvalidItemID():
            item_id = shNode.CreateItem(shNode.GetSceneItemID(), ct_node)
        return item_id

    def _getOrCreateSideFolder(self, isLeft: bool) -> int:
        shNode = slicer.vtkMRMLSubjectHierarchyNode.GetSubjectHierarchyNode(slicer.mrmlScene)
        parent_id = self._shCTItemID()
        pn_name = (self._parameterNode.parameterNode.GetName()
                   if self._parameterNode else "OrbitalVolumeWorkflow")
        side_label = "Links" if isLeft else "Rechts"
        folder_name = f"{pn_name} Orbital Volume Segmentation – {side_label}"
        child_ids = vtk.vtkIdList()
        shNode.GetItemChildren(parent_id, child_ids)
        for i in range(child_ids.GetNumberOfIds()):
            child_id = child_ids.GetId(i)
            if shNode.GetItemName(child_id) == folder_name:
                return child_id
        return shNode.CreateFolderItem(parent_id, folder_name)

    def _placeInFolder(self, node, isLeft: bool) -> None:
        if node is None:
            return
        shNode = slicer.vtkMRMLSubjectHierarchyNode.GetSubjectHierarchyNode(slicer.mrmlScene)
        folder_id = self._getOrCreateSideFolder(isLeft)
        item_id = shNode.GetItemByDataNode(node)
        if item_id == shNode.GetInvalidItemID():
            shNode.CreateItem(folder_id, node)
        else:
            shNode.SetItemParent(item_id, folder_id)

    def _placeUnderCTVolume(self, node) -> None:
        if node is None:
            return
        shNode = slicer.vtkMRMLSubjectHierarchyNode.GetSubjectHierarchyNode(slicer.mrmlScene)
        parent_id = self._shCTItemID()
        item_id = shNode.GetItemByDataNode(node)
        if item_id == shNode.GetInvalidItemID():
            shNode.CreateItem(parent_id, node)
        else:
            shNode.SetItemParent(item_id, parent_id)


# ═══════════════════════════════════════════════════════════════════════════════
# Logic
# ═══════════════════════════════════════════════════════════════════════════════

class OrbitalVolumeWorkflowModuleLogic(ScriptedLoadableModuleLogic):

    COLORS_SEGMENTATION = [
        (0.2, 0.8, 0.5), #green
        (0.5, 0.2, 0.8), #blue
        (0.8, 0.5, 0.2) #red
    ]

    COLORS_ORBITAL_PLANE = [
        (0.7, 1.0, 0.2), # green
        (0.2, 0.7, 1.0), # blue
        (1.0, 0.2, 0.7) # red
    ]

    COLOR_DEFAULT = (0.5, 0.5, 0.5) #grey

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
        color: tuple = None,
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
    
        if color == None:
            color = self._getColorForActiveParameterNode("OrbitalPlane")

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
        segment_color: tuple = None,
        existing_segmentation_node=None,
        existing_seed_node=None,
        contralateral_seg_node=None,
        contralateral_seg_id=None,
        model_seed_node=None,
        model_seed_id=None,
        remove_satellites: bool = True,
        min_satellite_diameter_mm: float = 3.0,
        posterior_cutoff_ras=None,
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

        ct_vol_name   = volume_node.GetName()
        side_suffix   = plane_model.GetName().replace("_OrbitalPlane", "")
        seed_name     = f"{ct_vol_name}_{side_suffix}_Seed"
        seg_node_name = f"{ct_vol_name}_{side_suffix}_IntraorbitalSeg"

        seed_node = self._placeSeedFiducial(
            seed_ras, name=seed_name, existing_node=existing_seed_node
        )
        seed_node.GetDisplayNode().SetVisibility(1 if show_seed else 0)

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

        seg_id, volume_ml, voxel_count, mask_segment_id = self._fastMarchingSegmentation(
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
            posterior_cutoff_ras=posterior_cutoff_ras,
        )        
 
        """ =========================
                Segment Management
            ========================= """

        if segment_color == None:
            segment_color = self._getColorForActiveParameterNode("OrbitalPlane")

        seg = segmentation_node.GetSegmentation()
        segment_volume = seg.GetSegment(seg_id)
        segment_volume.SetName("IntraorbitalVolume")
        segment_volume.SetColor(*segment_color)

        mask_color = np.clip(np.multiply(segment_color, 2),0,1)

        segment_mask = seg.GetSegment(mask_segment_id)
        segment_mask.SetName("Mask")
        segment_mask.SetColor(mask_color)

        disp = segmentation_node.GetDisplayNode()
        disp.SetOpacity3D(0.5)
        disp.SetOpacity2DFill(0.4)
        disp.SetVisibility3D(True)

        segmentation_node.CreateClosedSurfaceRepresentation()
        slicer.app.layoutManager().threeDWidget(0).threeDView().resetFocalPoint()

        """ =====================
                Postprocessing
            ===================== """

        print("\n  Postprocessing segmentation...")

        # Prepare the segmentation editor
        segmentEditorWidget = self._prepareSegmentEditor(volume_node, segmentation_node, seg_id) 

        # hide the mask segment, otherwise it will also be edited by JOIN_TAUBIN Smoothing
        segmentation_node.GetDisplayNode().SetSegmentVisibility(mask_segment_id, False) 

        # 1. Smoothin Joint 0.8 everywhere
        smoothing_factor = 0.8
        print(f"  - Smoothing (Joint Taubin) with a smoothing factor of {smoothing_factor}...", end="")
        segmentEditorWidget.setActiveEffectByName("Smoothing")
        effect = segmentEditorWidget.activeEffect()
        effect.setParameter("SmoothingMethod", "JOINT_TAUBIN")
        effect.setParameter("JointTaubinSmoothingFactor", smoothing_factor)
        effect.parameterSetNode().SetMaskMode(slicer.vtkMRMLSegmentationNode.EditAllowedEverywhere)
        effect.parameterSetNode().SetOverwriteMode(slicer.vtkMRMLSegmentEditorNode.OverwriteNone)
        effect.self().onApply()
        time.sleep(1)
        print(" Done")
        
        # 2. Smoothing Closing 1 mm for small irregularites
        kernel_size = 1
        print(f"  - Smoothing (Closing) with a kernel size of {smoothing_factor} mm...", end="")
        segmentEditorWidget.setActiveEffectByName("Smoothing")
        effect = segmentEditorWidget.activeEffect()
        effect.setParameter("SmoothingMethod", "MORPHOLOGICAL_CLOSING")
        effect.setParameter("KernelSizeMm", kernel_size)
        effect.parameterSetNode().SetMaskMode(slicer.vtkMRMLSegmentationNode.EditAllowedEverywhere)
        effect.parameterSetNode().SetOverwriteMode(slicer.vtkMRMLSegmentEditorNode.OverwriteNone)
        effect.self().onApply()
        time.sleep(1)
        print(" Done")

        # 3. Threshold -1000 bis hu-max insidehsegment
        print(f"  - Thresholding to remove intersection with bone (HU-Threshold {hu_max})...",  end="")
        segmentEditorWidget.setActiveEffectByName("Threshold")
        effect = segmentEditorWidget.activeEffect()
        effect.setParameter("MinimumThreshold",-1000)
        effect.setParameter("MaximumThreshold", hu_max)
        effect.parameterSetNode().SetMaskMode(slicer.vtkMRMLSegmentationNode.EditAllowedInsideVisibleSegments)
        effect.self().onApply()
        time.sleep(1)
        print(" Done")

        # 4. Smoothing Opening 8-10 mm inside segment
        kernel_size = 8
        print(f"  - Smoothing (Opening) with a kernel size of {kernel_size} mm...", end="")
        segmentEditorWidget.setActiveEffectByName("Smoothing")
        effect = segmentEditorWidget.activeEffect()
        effect.setParameter("SmoothingMethod", "MORPHOLOGICAL_OPENING")
        effect.setParameter("KernelSizeMm", kernel_size)
        effect.parameterSetNode().SetMaskMode(slicer.vtkMRMLSegmentationNode.EditAllowedEverywhere)
        effect.self().onApply()
        print(" Done")

        msgBox = qt.QMessageBox(qt.QMessageBox.Information,
                                "Segmentation complete",
                                "Segmentation almost complete.<br /> If the Segmentation contains multiple "  
                                "Islands select the Island within a slice View. Otherwise just continue by "
                                "pressing the <i>Finish</i>-Button. If you want to inspect the segmentation "
                                "before finishing click <i>Inspect</i>")
        msgBoxButtonFinish = msgBox.addButton("Finish Segmentation", qt.QMessageBox.AcceptRole)
        msgBoxButtonIsland = msgBox.addButton("Pick Island", qt.QMessageBox.ActionRole)
        msgBoxButtonInspect = msgBox.addButton("Inspect", qt.QMessageBox.ActionRole)
        msgBox.exec()

        if (msgBox.clickedButton() == msgBoxButtonIsland):
            # 4. Islands keep larges everywhere
            print(f"  - Switching to Segment Editor", end="")
            self.segmentationKeepSelectedIsland(volume_node, segmentation_node, seg_id)

        else:
            if (msgBox.clickedButton() == msgBoxButtonFinish):
                # 5. Clip anterior border using the mask
                print(f"  - Clipping anterior border...", end="")
                self.segmentationPerformCutoff(volume_node, segmentation_node, seg_id, mask_segment_id)
                print(" Done")
            
            segmentEditorWidget.setActiveEffectByName("NULL")

        print(f"\n{'─'*60}")
        print(f"  FM raw voxels          : {voxel_count:,}  ({volume_ml:.2f} ml before post-processing)")
        print(f"  Segment node           : {seg_node_name}")
        print(f"{'='*60}\n")

        slicer.util.setSliceViewerLayers(background=volume_node)

        return {
            "segmentation_node":   segmentation_node,
            "segment_id":          seg_id,
            "volume_ml":           volume_ml,
            "voxel_count":         voxel_count,
            "seed_ras":            seed_ras,
            "seed_node":           seed_node,
            "offset_mm":           offset_used,
            "hu_at_seed":          hu,
            "voxel_exclusion_map": mask_segment_id
        }

    def _prepareSegmentEditor(self, volume_node, segmentation_node, segment_id):
        segmentEditorWidget = slicer.modules.segmenteditor.widgetRepresentation().self().editor    
        segmentEditorWidget.setSegmentationNode(segmentation_node)
        segmentEditorWidget.setSourceVolumeNode(volume_node)
        segmentEditorNode = segmentEditorWidget.mrmlSegmentEditorNode()
        segmentEditorNode.SetSelectedSegmentID(segment_id)

        return segmentEditorWidget

    def segmentationKeepSelectedIsland(self, volume_node, segmentation_node, segment_id):
        segmentEditorWidget = self._prepareSegmentEditor(volume_node, segmentation_node, segment_id)

        segmentEditorWidget.setActiveEffectByName("Islands")
        effect = segmentEditorWidget.activeEffect()
        effect.setParameter("Operation", "KEEP_SELECTED_ISLAND")
        effect.parameterSetNode().SetMaskMode(slicer.vtkMRMLSegmentationNode.EditAllowedEverywhere)

    def segmentationPerformCutoff(self, volume_node, segmentation_node, segment_id, mask_segment_id):
        segmentEditorWidget = self._prepareSegmentEditor(volume_node, segmentation_node, segment_id)

        segmentEditorWidget.setActiveEffectByName("Logical operators")
        effect = segmentEditorWidget.activeEffect()
        effect.setParameter("Operation","SUBTRACT")
        effect.setParameter("ModifierSegmentID", mask_segment_id)
        effect.parameterSetNode().SetMaskMode(slicer.vtkMRMLSegmentationNode.EditAllowedEverywhere)
        effect.self().onApply()


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
    
    def _keepIslandAtSeed(self, segmentation_node, segment_id, seed_ras):
        """ Copies the Behaviour of the Islands-Segment-Effect, but uses the seed RAS vor selecting the island

        :param segmentation_node: _description_
        :param segment_id: _description_
        :param seed_ras: _description_
        """

        segment = segmentation_node.GetSegment(segment_id)        
        # selectedSegmentLabelmap = segment.GetRepresentation("Binary labelmap")
        # We need to know exactly the value of the segment voxels, apply threshold to make force the selected label value
        labelValue = 1
        backgroundValue = 0

        labelmapVolumeNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLabelMapVolumeNode")
        slicer.modules.segmentations.logic().ExportSegmentsToLabelmapNode(segmentation_node, segment_id, labelmapVolumeNode)

        segmentImageData = labelmapVolumeNode.GetImageData()

        thresh = vtk.vtkImageThreshold()
        thresh.SetInputData(segmentImageData)
        thresh.ThresholdByLower(0)
        thresh.SetInValue(backgroundValue)
        thresh.SetOutValue(labelValue)
        thresh.SetOutputScalarType(segmentImageData.GetScalarType())
        thresh.Update()

        # Create oriented image data from output
        import vtkSegmentationCorePython as vtkSegmentationCore # pyright: ignore[reportMissingImports]

        inputLabelImage = slicer.vtkOrientedImageData()
        inputLabelImage.ShallowCopy(thresh.GetOutput())
        selectedSegmentLabelmapImageToWorldMatrix = vtk.vtkMatrix4x4()
        segmentImageData.GetImageToWorldMatrix(selectedSegmentLabelmapImageToWorldMatrix)
        inputLabelImage.SetImageToWorldMatrix(selectedSegmentLabelmapImageToWorldMatrix)

        # Process segmentation
        floodFillingFilter = vtk.vtkImageThresholdConnectivity()
        floodFillingFilter.SetInputData(inputLabelImage)
        seedPoints = vtk.vtkPoints()
        origin = inputLabelImage.GetOrigin()
        spacing = inputLabelImage.GetSpacing()
        seedPoints.InsertNextPoint(origin[0] + seed_ras[0], origin[1] + seed_ras[1] * spacing[1], origin[2] + seed_ras[2])
        floodFillingFilter.SetSeedPoints(seedPoints)
        floodFillingFilter.ThresholdBetween(1, 1)
        floodFillingFilter.SetInValue(1)
        floodFillingFilter.SetOutValue(0)
        floodFillingFilter.Update()

        # Import segment from vtkImageData
        segmentImageData.DeepCopy(floodFillingFilter.GetOutput())
        slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(labelmapVolumeNode, segmentation_node, segment_id)

        # Cleanup temporary nodes
        slicer.mrmlScene.RemoveNode(labelmapVolumeNode.GetDisplayNode().GetColorNode())
        slicer.mrmlScene.RemoveNode(labelmapVolumeNode)


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
        posterior_cutoff_ras=None,
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

        # treat intraorbital air like soft-tissue
        hu_arr[hu_arr < -400] = 0

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
        
        outside = outside.reshape(Nz, Ny, Nx)

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
        # speed_arr[outside] = 1e-4

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

        # Trial-Punkte posterior zur Cutoff-Ebene entfernen.
        # FM-Trial-Points haben Arrival-Time=0 unabhängig vom Speed-Wert.
        # Ebenennormale = (0,1,0) in LPS = Posterior-Richtung (Koronalebene).
        # Da CTs korrekt ausgerichtet sind, entspricht das der anatomischen AP-Achse.
        if posterior_cutoff_ras is not None and region_points:
            cutoff_lps_pre = np.array([
                -posterior_cutoff_ras[0],
                -posterior_cutoff_ras[1],
                 posterior_cutoff_ras[2],
            ])
            posterior_dir = np.array([0.0, 1.0, 0.0])  # +Y in LPS = Posterior
            pts_ijk = np.array(region_points, dtype=np.float64)  # (N, 3): i,j,k
            pts_lps = origin + (direction @ (pts_ijk * spacing).T).T
            keep = (pts_lps - cutoff_lps_pre) @ posterior_dir <= 0
            n_before = len(region_points)
            region_points = [p for p, k in zip(region_points, keep) if k]
            print(f"  Cutoff-Filter Trial-Punkte: {len(region_points)}/{n_before} behalten")

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

        # --- 16b. Posteriore Cutoff-Ebene (hard stop, beide Seiten gleich) ---
        # Koronalebene durch den Cutoff-Punkt; Normale = (0,1,0) in LPS = Posterior.
        # Alle Voxel mit LPS_Y > cutoff_LPS_Y werden auf 0 gesetzt.
        if posterior_cutoff_ras is not None:
            cutoff_lps = np.array([
                -posterior_cutoff_ras[0],
                -posterior_cutoff_ras[1],
                 posterior_cutoff_ras[2],
            ])
            posterior_dir = np.array([0.0, 1.0, 0.0])  # +Y in LPS = Posterior
            depth_from_cutoff = (lps_pts - cutoff_lps) @ posterior_dir
            posterior_mask = depth_from_cutoff.reshape(Nz, Ny, Nx) > 0
            n_clipped = int(binary_arr[posterior_mask].sum())
            binary_arr[posterior_mask] = 0
            print(f"  Posterior cutoff: {n_clipped:,} Voxel hinter Cutoff-Ebene entfernt")

        voxel_count = int(binary_arr.sum())

        sp = sitk_image.GetSpacing()
        volume_ml = voxel_count * sp[0] * sp[1] * sp[2] / 1000.0
        print(f"  Fast Marching: {voxel_count:,} voxels, {volume_ml:.2f} ml")

        # --- 17. Ergebnis als Segment in Slicer importieren ---
        # Weg: NumPy → SimpleITK → LabelMapVolumeNode → SegmentationNode.
        # Der temporäre LabelMap-Node wird nach dem Import wieder entfernt.
        seg_id = self._convertArrayToSegment(binary_arr, sitk_image, segmentation_node)
        mask_segment_id = self._convertArrayToSegment(np.multiply(outside,1), sitk_image, segmentation_node)

        return seg_id, volume_ml, voxel_count, mask_segment_id
    
    def _convertArrayToSegment(self, binary_arr, sitk_image, segmentation_node):
        """Converts a 3-dimensional array to a 3D Slicer Segment

        :param binary_arr: 3-dimensional array
        :param sitk_image: ITK-Image object, that contains the volume properties
        :param segmentation_node: 3D Slicer segmentation node, that the segment will be added to
        :return: Segment-ID of the newly created Segment
        """        
        binary_sitk = sitk.GetImageFromArray(binary_arr)
        binary_sitk.CopyInformation(sitk_image)

        # create Segment for intraorbital Volume
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

        return seg_id

    def _smoothSegment(self, segmentation_node, seg_id, kernel_size_mm: float = 3.0, method: str = "closing"):
        """Applies morphological closing or opening to the binary labelmap using an ellipsoidal kernel scaled to physical mm.

        The array is zero-padded before the operation so that scipy's border
        treatment (outside = background) does not erode voxels at the tight
        bounding-box edge, which would create an artificial flat cut.
        """
        import vtk.util.numpy_support as nps # pyright: ignore[reportMissingImports]
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

    def _maskSegmentByHU(self, segmentation_node, seg_id, volume_node, hu_max: float) -> None:
        """Removes all voxels from the segment whose CT value exceeds hu_max.

        Smoothing (closing) can push the segmentation boundary into cortical bone.
        This mask restores the HU constraint by zeroing out any voxel where the
        original CT intensity is above hu_max.
        """
        import vtk.util.numpy_support as nps # type: ignore
        import sitkUtils
        import SimpleITK as sitk

        seg = segmentation_node.GetSegmentation()
        binary_lm = seg.GetSegment(seg_id).GetRepresentation("Binary labelmap")
        if binary_lm is None:
            return

        # Export CT into the same geometry as the binary labelmap
        ref_lm = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLabelMapVolumeNode", "_TmpHURef")
        try:
            slicer.modules.segmentations.logic().ExportAllSegmentsToLabelmapNode(
                segmentation_node, ref_lm, slicer.vtkSegmentation.EXTENT_REFERENCE_GEOMETRY
            )
            ct_sitk  = sitkUtils.PullVolumeFromSlicer(volume_node)
            ref_sitk = sitkUtils.PullVolumeFromSlicer(ref_lm)
        finally:
            slicer.mrmlScene.RemoveNode(ref_lm)

        # Resample CT to labelmap geometry (nearest-neighbour, float keeps HU values)
        resampler = sitk.ResampleImageFilter()
        resampler.SetReferenceImage(ref_sitk)
        resampler.SetInterpolator(sitk.sitkLinear)
        resampler.SetDefaultPixelValue(-1000.0)
        ct_resampled = resampler.Execute(ct_sitk)

        ct_arr  = sitk.GetArrayFromImage(ct_resampled)   # (z, y, x)
        dims    = binary_lm.GetDimensions()               # (x, y, z)
        scalars = binary_lm.GetPointData().GetScalars()
        flat    = nps.vtk_to_numpy(scalars)
        seg_arr = flat.reshape(dims[2], dims[1], dims[0])  # (z, y, x)

        # Clip ct_arr to the labelmap extent if sizes differ by 1 (rounding)
        for axis, lm_size in enumerate(seg_arr.shape):
            if ct_arr.shape[axis] != lm_size:
                slices = [slice(None)] * 3
                slices[axis] = slice(0, lm_size)
                ct_arr = ct_arr[tuple(slices)]

        bone_mask = ct_arr > hu_max
        seg_arr[bone_mask] = 0

        scalars.DeepCopy(nps.numpy_to_vtk(seg_arr.astype(flat.dtype).flatten()))
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
        import vtk.util.numpy_support as nps # pyright: ignore[reportMissingImports]
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

        # Collect nonzero indices as FM trial points
        nz_k, nz_j, nz_i = np.where(mirrored > 0)
        if len(nz_i) == 0:
            print("  Mirrored seed is empty – falling back to single seed.")
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

        # Gehärtete Kopie erstellen: ExportSegmentsToLabelmapNode berücksichtigt
        # Skalierungs-Transforms nicht immer korrekt; Härten bäckt die vollständige
        # Transform-Kette (Translation, Rotation, Skalierung) in die Geometrie ein.
        hardened_copy = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLSegmentationNode", "_TmpModelSegHardened"
        )
        hardened_copy.GetSegmentation().DeepCopy(model_seg_node.GetSegmentation())
        tf_id = model_seg_node.GetTransformNodeID()
        if tf_id:
            hardened_copy.SetAndObserveTransformNodeID(tf_id)
            slicer.modules.transforms.logic().hardenTransform(hardened_copy)

        seg_id_arr = vtk.vtkStringArray()
        seg_id_arr.InsertNextValue(model_seg_id)
        tmp_lm = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLLabelMapVolumeNode", "_TmpModelLM"
        )
        try:
            slicer.modules.segmentations.logic().ExportSegmentsToLabelmapNode(
                hardened_copy, seg_id_arr, tmp_lm, volume_node
            )
            model_sitk = sitkUtils.PullVolumeFromSlicer(tmp_lm)
        finally:
            slicer.mrmlScene.RemoveNode(tmp_lm)
            slicer.mrmlScene.RemoveNode(hardened_copy)

        model_arr = sitk.GetArrayFromImage(model_sitk)
        n_vox = int(model_arr.sum())
        print(f"  Model seed: {n_vox:,} voxels")

        if n_vox == 0:
            print("  Model seed is empty – verify the template is positioned inside the CT volume.")
            return []

        # Display the full positioned template (purple) for reference
        self._importBinaryAsSegNode(
            model_sitk, volume_node,
            name=f"{name_prefix}ModelSeed",
            color=(0.7, 0.2, 0.9),
            opacity=0.25,
        )

        nz_k, nz_j, nz_i = np.where(model_arr > 0)
        return [(int(nz_i[m]), int(nz_j[m]), int(nz_k[m])) for m in range(len(nz_i))]

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
    
    def _activeParameterNodeIndex(self) -> int:
        """Returns the 0-based index of the active PN among all module PNs, or -1."""
        if self._parameterNode is None:
            return -1
        
        active = self._parameterNode.parameterNode
        all_parameter_nodes = self.getAllParameterNodes()

        # iterate over alle parameter nodes and return the index of
        # the currently active node
        for i in range(len(all_parameter_nodes)):
            if active.GetID() == all_parameter_nodes[i].parameterNode.GetID():
                return i
        
        # if no node is found return -1
        return -1

    def _getColorForActiveParameterNode(self, objectType):
        activeParameterNodeIndex = self._activeParameterNodeIndex()

        if activeParameterNodeIndex == -1:
            return self.COLOR_DEFAULT;

        colorTuple = self.COLOR_DEFAULT

        if objectType == "Segmentation":
            colorTuple = self.COLORS_SEGMENTATION[activeParameterNodeIndex]
        elif objectType == "OrbitalPlane":
            colorTuple = self.COLORS_ORBITAL_PLANE[activeParameterNodeIndex]

        return colorTuple;