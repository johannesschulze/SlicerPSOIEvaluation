import os
from typing import Annotated, Optional

from __main__ import vtk, slicer

import SegmentStatistics
import qt
import csv
import sys
import logging

# enable runtime reloading of submodules
import importlib
mod = importlib.import_module('PSOILib', __name__)
importlib.reload(mod)
__submoduleNames__=['helperfunctions']

import numpy as np
import scipy

from PSOILib import helperfunctions
from slicer.i18n import tr as _
from slicer.i18n import translate
from slicer.ScriptedLoadableModule import ScriptedLoadableModuleWidget, ScriptedLoadableModule, ScriptedLoadableModuleLogic, ScriptedLoadableModuleTest
from slicer.util import VTKObservationMixin
from slicer.parameterNodeWrapper import (
    parameterNodeWrapper,
    WithinRange,
)

from slicer import vtkMRMLScalarVolumeNode, vtkMRMLModelNode, vtkMRMLFolderDisplayNode, vtkMRMLNode, vtkMRMLSegmentationNode, vtkMRMLTransformNode

class GeneralPSOIWorkflowModule(ScriptedLoadableModule):
    """Uses ScriptedLoadableModule base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = _("General PSOI Analysis Workflow")
        # TODO: set categories (folders where the module shows up in the module selector)
        self.parent.categories = [translate("qSlicerAbstractCoreModule", "PSOI Evaluation")]
        self.parent.dependencies = ['SegmentStatistics','ModelRegistration'] 
        self.parent.contributors = ["Johannes Schulze (Bundeswehrkrankenhaus Ulm)"]
        self.parent.helpText = _("""
This is a module for the automation of the proceses related to the orbital PSI research at the German Armed Military Forces Hospital in Ulm, Germany.
""")
        # TODO: replace with organization, grant and thanks
        self.parent.acknowledgementText = _("""
This file was originally developed by Johannes Schulze (Bundeswehrkrankenhaus Ulm) without any funding or grants.
""")
        self.helperfunctions = helperfunctions

        slicer.app.connect("startupCompleted()", self.onStartupCompleted)
                
    # gets called when 3D slicer has completed it's startup, even before the module is opend
    def onStartupCompleted(self):
        return

@parameterNodeWrapper
class GeneralPSOIWorkflowModuleParameterNode:
    """
    The parameters needed by module.
    
    :var preopVolume: Description
    :var preopVolume: Description
    """

    preopVolume : vtkMRMLScalarVolumeNode
    postopVolume : vtkMRMLScalarVolumeNode
    psiPlannedModel : vtkMRMLModelNode
    psiPostopModel : vtkMRMLModelNode
    psiDistanceModel : vtkMRMLModelNode
    skullPlannedModel : vtkMRMLModelNode
    #psiPlannedName : str = "orbita_psi"

    rmsPlanToPreop : float
    rmsPlanToPostop : float
    nccRegistration : float = 0.0

    registrationMaskSegmentation : Optional[vtkMRMLSegmentationNode]
    psiPostopSegmentation : Optional[vtkMRMLSegmentationNode]
    psiPostopSegmentId : str = ""
    planToPreopTransform : Optional[vtkMRMLTransformNode]

    step : int = 0

    
class GeneralPSOIWorkflowModuleWidget(ScriptedLoadableModuleWidget, VTKObservationMixin):
    """Uses ScriptedLoadableModuleWidget base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def __init__(self, parent=None) -> None:
        """Called when the user opens the module the first time and the widget is initialized."""
        ScriptedLoadableModuleWidget.__init__(self, parent)
        VTKObservationMixin.__init__(self)   # needed for parameter node observation
        self.logic = None
        self._parameterNode = None
        self._parameterNodeGuiTag = None
        self._brainsCliNode = None
        self._brainsObserverTag = None
        self._preopCropROI = None
        self._postopCropROI = None

    def setup(self) -> None:
        """Called when the user opens the module the first time and the widget is initialized."""
        ScriptedLoadableModuleWidget.setup(self)

        # Load widget from .ui file (created by Qt Designer).
        # Additional widgets can be instantiated manually and added to self.layout.
        uiWidget = slicer.util.loadUI(self.resourcePath("UI/GeneralPSOIWorkflowModule.ui"))
        self.layout.addWidget(uiWidget)
        self.ui = slicer.util.childWidgetVariables(uiWidget)

        # Set scene in MRML widgets. Make sure that in Qt designer the top-level qMRMLWidget's
        # "mrmlSceneChanged(vtkMRMLScene*)" signal in is connected to each MRML widget's.
        # "setMRMLScene(vtkMRMLScene*)" slot.
        uiWidget.setMRMLScene(slicer.mrmlScene)

        # Create logic class. Logic implements all computations that should be possible to run
        # in batch mode, without a graphical user interface.
        self.logic = GeneralPSOIWorkflowModuleLogic()

        # Connections
        # These connections ensure that we update parameter node when scene is closed
        self.addObserver(slicer.mrmlScene, slicer.mrmlScene.StartCloseEvent, self.onSceneStartClose)
        self.addObserver(slicer.mrmlScene, slicer.mrmlScene.EndCloseEvent, self.onSceneEndClose)

        # Buttons
        self.ui.openDocumentationButton.connect("clicked(bool)", self.onOpenDocumentationButton)
        self.ui.cropPreopButton.connect("clicked(bool)", self.onCropPreopButton)
        self.ui.applyCropPreopButton.connect("clicked(bool)", self.onApplyCropPreopButton)
        self.ui.cropPostopButton.connect("clicked(bool)", self.onCropPostopButton)
        self.ui.applyCropPostopButton.connect("clicked(bool)", self.onApplyCropPostopButton)
        self.ui.alignMidlineButton.connect("clicked(bool)", self.onAlignMidlineButton)
        self.ui.prepareSceneButton.connect("clicked(bool)", self.onPrepareSceneButton)
        self.ui.drawRegistrationMaskButton.connect("clicked(bool)", self.onDrawRegistrationMaskButton)
        self.ui.performVolumeRegistrationButton.connect("clicked(bool)", self.onPerformVolumeRegistrationButton)
        self.ui.applyTransformsToPlannedModelButton.connect("clicked(bool)", self.onApplyTransformsToPlannedModelButton)
        self.ui.applyAlignmentTransformButton.connect("clicked(bool)", self.onApplyAlignmentTransformButton)
        self.ui.prepareSegmentationButton.connect("clicked(bool)", self.onPrepareSegmentationButton)
        self.ui.useExistingSegmentationButton.connect("clicked(bool)", self.onUseExistingSegmentationButton)
        self.ui.psiPostopSegmentationSelector.connect("currentNodeChanged(vtkMRMLNode*)", self._onPsiPostopSegmentationChanged)
        self.ui.psiPostopSegmentComboBox.connect("currentIndexChanged(int)", self._onPsiPostopSegmentComboBoxChanged)
        self.ui.alignPSIsButton.connect("clicked(bool)", self.onAlignPSIsButton)
        self.ui.manualAlignPSIsButton.connect("clicked(bool)", self.onManualAlignPSIsButton)
        self.ui.registerToSelectedModelButton.connect("clicked(bool)", self.onRegisterToSelectedModelButton)
        self.ui.calculateM2MDistanceButton.connect("clicked(bool)", self.onCalculateM2MDistanceButton)
        self.ui.printPSIResultsButton.connect("clicked(bool)", self.onPrintPSIResultsButton)
        self.ui.recenterPlanSTLsButton.connect("clicked(bool)", self.onRecenterPlanSTLsButton)
        self.ui.segmentPreopCTButton.connect("clicked(bool)", self.onSegmentPreopCTButton)
        self.ui.registerPlanToPreopButton.connect("clicked(bool)", self.onRegisterPlanToPreopButton)

		# change events for Node selectors
        self.ui.psiPlannedModelSelector.connect("currentNodeChanged(vtkMRMLNode*)", self.onPlannedModelSelectorChanged)
        self.ui.psiPlannedModelSelectorCalculation.connect("currentNodeChanged(vtkMRMLNode*)", self.onPlannedModelSelectorChanged)
        self.ui.psiPlannedModelSelectorOutput.connect("currentNodeChanged(vtkMRMLNode*)", self.onPlannedModelSelectorChanged)
        self.ui.psiPostopModelSelectorCalculation.connect("currentNodeChanged(vtkMRMLNode*)", self.onPostopModelSelectorChanged)
        self.ui.psiPostopModelSelectorOutput.connect("currentNodeChanged(vtkMRMLNode*)", self.onPostopModelSelectorChanged)

        self.ui.stepsToolbox.connect("currentChanged(int)", self.onStepsToolboxCurrentChanged)

        # Buttons for Maxilla Model handling
        #self.ui.labelImage.setPixmap(QPixmap(self.resourcePath("Images/orbita.png")))

        # self.ui.markupsButton.connect("clicked(bool)", self.logic.createMarkupsForPlanes)
        # self.ui.planesButton.connect("clicked(bool)", self.logic.createResectionPlanes)
        # self.ui.splitModelsButton.connect("clicked(bool)", self.logic.splitMaxillaFromMidface)
        # self.ui.alignMaxillaModelsButton.connect("clicked(bool)", self.logic.alignMaxillaModels)
        # self.ui.calculateM2MDistanceButton.connect("clicked(bool)", self.logic.calculateMaxillaModelToModelDistance)
        # self.ui.printMaxillaResultsButton.connect("clicked(bool)", self.logic.printMaxillaResults)

        # Buttons for PSI Model handling
        #
        #self.ui.calculateM2MDistancePSIButton.connect("clicked(bool)", self.logic.calculatePSIModelToModelDistance)
        #self.ui.printPSIResultsButton.connect("clicked(bool)", self.logic.printPSIResults)

        # Button for final Output of all Results
        #self.ui.printAllResultsButton.connect("clicked(bool)", self.logic.printAllResults)

        # Make sure parameter node is initialized (needed for module reload)
        self.initializeParameterNode()

    def cleanup(self) -> None:
        """Called when the application closes and the module widget is destroyed."""
        self.removeObservers()
    
    def enter(self) -> None:
        """Called each time the user opens this module."""
        # Make sure parameter node exists and observed
        self.initializeParameterNode()
        self.ui.stepsToolbox.setCurrentIndex(self._parameterNode.step)

    def exit(self) -> None:
        """Called each time the user opens a different module."""
        # Do not react to parameter node changes (GUI will be updated when the user enters into the module)
        if self._parameterNode:
            self._parameterNode.disconnectGui(self._parameterNodeGuiTag)
            self._parameterNodeGuiTag = None
            self.removeObserver(self._parameterNode, vtk.vtkCommand.ModifiedEvent, self._checkCanApply)

    def onSceneStartClose(self, caller, event) -> None:
        """Called just before the scene is closed."""
        # Parameter node will be reset, do not use it anymore
        self.setParameterNode(None)

    def onSceneEndClose(self, caller, event) -> None:
        """Called just after the scene is closed."""
        # If this module is shown while the scene is closed then recreate a new parameter node immediately
        if self.parent.isEntered:
            self.initializeParameterNode()
    
    def initializeParameterNode(self) -> None:
        """Ensure parameter node exists and observed."""
        # Parameter node stores all user choices in parameter values, node selections, etc.
        # so that when the scene is saved and reloaded, these settings are restored.

        self.setParameterNode(self.logic.getParameterNode())

        # # Select default input nodes if nothing is selected yet to save a few clicks for the user
        # if not self._parameterNode.preopVolume:
        #     firstVolumeNode = slicer.mrmlScene.GetFirstNodeByClass("vtkMRMLScalarVolumeNode")
        #     if firstVolumeNode:
        #         self._parameterNode.inputVolume = firstVolumeNode
    
    def setParameterNode(self, inputParameterNode: Optional[GeneralPSOIWorkflowModuleParameterNode]) -> None:
        """
        Set and observe parameter node.
        Observation is needed because when the parameter node is changed then the GUI must be updated immediately.
        """

        if self._parameterNode:
            self._parameterNode.disconnectGui(self._parameterNodeGuiTag)
            self.removeObserver(self._parameterNode, vtk.vtkCommand.ModifiedEvent, self._checkCanApply)
            
        self._parameterNode = inputParameterNode
        
        if self._parameterNode:
            # Note: in the .ui file, a Qt dynamic property called "SlicerParameterName" is set on each
            # ui element that needs connection.
            self._parameterNodeGuiTag = self._parameterNode.connectGui(self.ui)
            self.addObserver(self._parameterNode, vtk.vtkCommand.ModifiedEvent, self._checkCanApply)
            self._checkCanApply()

    def _checkCanApply(self, caller=None, event=None) -> None:
        hasPreop = bool(self._parameterNode and self._parameterNode.preopVolume)
        hasPostop = bool(self._parameterNode and self._parameterNode.postopVolume)
        self.ui.cropPreopButton.enabled = hasPreop
        self.ui.applyCropPreopButton.enabled = hasPreop and self._preopCropROI is not None
        self.ui.cropPostopButton.enabled = hasPostop
        self.ui.applyCropPostopButton.enabled = hasPostop and self._postopCropROI is not None
        self.ui.alignMidlineButton.enabled = hasPreop
        self.ui.prepareSceneButton.enabled = hasPreop and hasPostop
        self.ui.applyAlignmentTransformButton.enabled = (
            self._parameterNode is not None and
            self._parameterNode.planToPreopTransform is not None
        )
        hasPostopSeg = self._parameterNode is not None and self._parameterNode.psiPostopSegmentation is not None
        self.ui.psiPostopSegmentComboBox.enabled = hasPostopSeg
        self.ui.useExistingSegmentationButton.enabled = hasPostopSeg and bool(
            self._parameterNode.psiPostopSegmentId
        )

    def onStepsToolboxCurrentChanged(self, currentId) -> None:
        self.logic.getParameterNode().step = currentId
        print(currentId)

    def onCropPreopButton(self) -> None:
        with slicer.util.tryWithErrorDisplay(_("Failed to create crop ROI."), waitCursor=True):
            if self._preopCropROI is not None:
                slicer.mrmlScene.RemoveNode(self._preopCropROI)
            self._preopCropROI = self.logic.createCropROI(self._parameterNode.preopVolume)
            self._checkCanApply()

    def onApplyCropPreopButton(self) -> None:
        with slicer.util.tryWithErrorDisplay(_("Failed to apply crop."), waitCursor=True):
            self.logic.applyCrop(self._parameterNode.preopVolume, self._preopCropROI)
            self._preopCropROI = None
            self._checkCanApply()

    def onCropPostopButton(self) -> None:
        with slicer.util.tryWithErrorDisplay(_("Failed to create crop ROI."), waitCursor=True):
            if self._postopCropROI is not None:
                slicer.mrmlScene.RemoveNode(self._postopCropROI)
            self._postopCropROI = self.logic.createCropROI(self._parameterNode.postopVolume)
            self._checkCanApply()

    def onApplyCropPostopButton(self) -> None:
        with slicer.util.tryWithErrorDisplay(_("Failed to apply crop."), waitCursor=True):
            self.logic.applyCrop(self._parameterNode.postopVolume, self._postopCropROI)
            self._postopCropROI = None
            self._checkCanApply()

    def onAlignMidlineButton(self) -> None:
        with slicer.util.tryWithErrorDisplay(_("Failed to align to midline."), waitCursor=True):
            result = self.logic.computeMidsagittalAlignment(self._parameterNode.preopVolume)
            if result is None:
                return
            centroid, theta = result
            self.logic.setMidlineTransform(self._parameterNode.preopVolume, centroid, theta)

    def onPrepareSceneButton(self) -> None:
        with slicer.util.tryWithErrorDisplay(_("Failed to compute results."), waitCursor=True):
            # Compute output
            self.logic.prepareForRegistration(
                self.ui.preopVolumeSelector.currentNode(),
                self.ui.postopVolumeSelector.currentNode()
            )
            self._nextStep()

    def onDrawRegistrationMaskButton(self) -> None:
        with slicer.util.tryWithErrorDisplay(_("Failed to open registration mask editor."), waitCursor=True):
            self.logic.openRegistrationMaskEditor(
                self.ui.preopVolumeSelector.currentNode()
            )

    def onPerformVolumeRegistrationButton(self) -> None:
        with slicer.util.tryWithErrorDisplay(_("Failed to start registration."), waitCursor=True):
            self.ui.performVolumeRegistrationButton.enabled = False
            self.ui.performVolumeRegistrationButton.text = "Registrierung läuft..."
            self.ui.brainsProgressBar.setVisible(True)
            cliNode = self.logic.performVolumeRegistration(
                self.ui.preopVolumeSelector.currentNode(),
                self.ui.postopVolumeSelector.currentNode()
            )
            self._brainsObserverTag = cliNode.AddObserver("ModifiedEvent", self._onBrainsRegistrationModified)
            self._brainsCliNode = cliNode

    def _onBrainsRegistrationModified(self, cliNode, event) -> None:
        if cliNode.IsBusy():
            return
        cliNode.RemoveObserver(self._brainsObserverTag)
        self._brainsCliNode = None
        self.ui.performVolumeRegistrationButton.enabled = True
        self.ui.performVolumeRegistrationButton.text = "2b. Register CT-Scans (BRAINS)"
        self.ui.brainsProgressBar.setVisible(False)
        if cliNode.GetStatus() == slicer.vtkMRMLCommandLineModuleNode.Completed:
            maskSeg = self._parameterNode.registrationMaskSegmentation
            if maskSeg is not None and maskSeg.GetDisplayNode():
                maskSeg.GetDisplayNode().SetVisibility(False)
            ncc = self.logic.computeRegistrationNCC(
                self._parameterNode.preopVolume,
                self._parameterNode.postopVolume
            )
            print(f"Registration NCC: {ncc:.4f}")
            self._nextStep()
        else:
            slicer.util.errorDisplay("Registrierung fehlgeschlagen. Bitte Konsolenausgabe prüfen.")

    def onRecenterPlanSTLsButton(self) -> None:
        with slicer.util.tryWithErrorDisplay(_("Failed to recenter Planning Data."), waitCursor=True):
            # Compute output
            self.logic.recenterPlannningSTLs()

    def onSegmentPreopCTButton(self) -> None:
        with slicer.util.tryWithErrorDisplay(_("Failed to segment preop CT."), waitCursor=True):
            self.logic.segmentPreopVolume()

    def onRegisterPlanToPreopButton(self) -> None:
        with slicer.util.tryWithErrorDisplay(_("Failed to algin Plannung Data to Preop CT."), waitCursor=True):
            self.logic.alignPlanToPreop()
            
            slicer.util.messageBox(f"Registration completed (RMS: {self.logic.getParameterNode().rmsPlanToPreop})")
            self._nextStep()
        return
    
    def onApplyTransformsToPlannedModelButton(self) -> None:
        with slicer.util.tryWithErrorDisplay(_("Failed to compute results."), waitCursor=True):
            self.logic.applyTransformsToPlannedModel()
            transformNode = slicer.mrmlScene.GetFirstNodeByName("registration plan to preop")
            if transformNode is None:
                transformNode = slicer.mrmlScene.GetFirstNodeByName("manual registration plan to preop")
            self._parameterNode.planToPreopTransform = transformNode

    def onApplyAlignmentTransformButton(self) -> None:
        with slicer.util.tryWithErrorDisplay(_("Failed to apply alignment transform."), waitCursor=True):
            model = self.ui.psiPlannedModelSelector.currentNode()
            if model is None:
                slicer.util.errorDisplay("No model selected.")
                return
            self.logic.applyAlignmentTransformToModel(model)

    def onPrepareSegmentationButton(self) -> None:
        with slicer.util.tryWithErrorDisplay(_("Failed to compute results."), waitCursor=True):
            self.logic.prepareSegmentation()
            self._nextStep()

    def _onPsiPostopSegmentationChanged(self, node) -> None:
        self.ui.psiPostopSegmentComboBox.blockSignals(True)
        self.ui.psiPostopSegmentComboBox.clear()
        if node is not None:
            seg = node.GetSegmentation()
            for i in range(seg.GetNumberOfSegments()):
                segId = seg.GetNthSegmentID(i)
                self.ui.psiPostopSegmentComboBox.addItem(seg.GetSegment(segId).GetName(), segId)
        self.ui.psiPostopSegmentComboBox.blockSignals(False)
        # Store ID of the first segment as default, or clear if no segmentation
        if node is not None and node.GetSegmentation().GetNumberOfSegments() > 0:
            self._parameterNode.psiPostopSegmentId = node.GetSegmentation().GetNthSegmentID(0)
        else:
            self._parameterNode.psiPostopSegmentId = ""
        self._checkCanApply()

    def _onPsiPostopSegmentComboBoxChanged(self, index) -> None:
        segId = self.ui.psiPostopSegmentComboBox.itemData(index)
        if segId and self._parameterNode:
            self._parameterNode.psiPostopSegmentId = segId
        self._checkCanApply()

    def onUseExistingSegmentationButton(self) -> None:
        with slicer.util.tryWithErrorDisplay(_("Failed to set segmentation."), waitCursor=True):
            if self._parameterNode.psiPostopSegmentation is None:
                slicer.util.errorDisplay("No segmentation selected.")
                return
            self._nextStep()
    
    def onAlignPSIsButton(self) -> None:
        with slicer.util.tryWithErrorDisplay(_("Failed to compute results."), waitCursor=True):
            # Compute output
            self.logic.alignPSIModels()
            slicer.util.messageBox(f"Registration completed (RMS: {self.logic.getParameterNode().rmsPlanToPostop})")
            self._nextStep()

    def onManualAlignPSIsButton(self) -> None:
        with slicer.util.tryWithErrorDisplay(_("Failed to set up manual alignment."), waitCursor=True):
            self.logic.manualAlignPSIModel()
            self._nextStep()

    def onRegisterToSelectedModelButton(self) -> None:
        with slicer.util.tryWithErrorDisplay(_("Failed to register to selected model."), waitCursor=True):
            targetModel = self.ui.preopModelSelector.currentNode()
            if targetModel is None:
                slicer.util.errorDisplay("No model selected.")
                return
            self.logic.alignPlanToPreopToModel(targetModel)
            transformNode = slicer.mrmlScene.GetFirstNodeByName("registration plan to preop")
            if transformNode is None:
                transformNode = slicer.mrmlScene.GetFirstNodeByName("manual registration plan to preop")
            self._parameterNode.planToPreopTransform = transformNode

    def onCalculateM2MDistanceButton(self) -> None:
        with slicer.util.tryWithErrorDisplay(_("Failed to compute results."), waitCursor=True):
            # Compute output
            self.logic.calculatePSIModelToModelDistance()
            self._nextStep()

    def onPrintPSIResultsButton(self) -> None:
        with slicer.util.tryWithErrorDisplay(_("Failed to compute results."), waitCursor=True):
            # Compute output
            self.logic.printPSIResults()
            slicer.util.messageBox("<b>Output complete!</b><p>You have finished this case. Don't forget so save the scene in the appropriate directory!</p>")

    def onPlannedModelSelectorChanged(self, node) -> None:
        if (node == None):
            return
        
        print(node.GetName())
        self.ui.psiPlannedModelSelector.setCurrentNodeID(node.GetID())
        self.ui.psiPlannedModelSelectorCalculation.setCurrentNodeID(node.GetID())
        self.ui.psiPlannedModelSelectorOutput.setCurrentNodeID(node.GetID())

        return
       
    def onPostopModelSelectorChanged(self, node) -> None:
        if (node == None):
            return
        
        print(node.GetName())
        self.ui.psiPostopModelSelectorCalculation.setCurrentNodeID(node.GetID())
        self.ui.psiPostopModelSelectorOutput.setCurrentNodeID(node.GetID())
       
        return

    def _nextStep(self) -> None:
        if (self.ui.stepsToolbox.currentIndex < (self.ui.stepsToolbox.count-1)):
            self.ui.stepsToolbox.setCurrentIndex(self.ui.stepsToolbox.currentIndex+1)
        
        #self.logic.getParameterNode().step = self.ui.stepsToolbox.currentIndex

    def onOpenDocumentationButton(self) -> None:
        docPath = self.resourcePath("Docs/GeneralPSOIWorkflowModule/psi_analysis_workflow_EN.html")
        qt.QDesktopServices.openUrl(qt.QUrl.fromLocalFile(docPath))

    def onReload(self):
        logging.info("Reloading GeneralPSOIWorkflowModule")
        importlib.reload(mod)
        for submoduleName in __submoduleNames__:
            mod1 = importlib.import_module('.'.join(['PSOILib',submoduleName]), __name__)
            importlib.reload(mod1)

        if isinstance(self, ScriptedLoadableModuleWidget):
             ScriptedLoadableModuleWidget.onReload(self)

    
class GeneralPSOIWorkflowModuleLogic(ScriptedLoadableModuleLogic):
    """This class should implement all the actual
    computation done by your module.  The interface
    should be such that other python code can import
    this class and make use of the functionality without
    requiring an instance of the Widget.
    Uses ScriptedLoadableModuleLogic base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def __init__(self) -> None:
        """Called when the logic class is instantiated. Can be used for initializing member variables."""
        ScriptedLoadableModuleLogic.__init__(self)

    def getParameterNode(self):
        return GeneralPSOIWorkflowModuleParameterNode(super().getParameterNode())

    def createCropROI(self, volume):
        bounds = [0.0] * 6
        volume.GetRASBounds(bounds)
        roiNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsROINode")
        roiNode.SetName(f"Crop ROI - {volume.GetName()}")
        center = [(bounds[0]+bounds[1])/2, (bounds[2]+bounds[3])/2, (bounds[4]+bounds[5])/2]
        size = [bounds[1]-bounds[0], bounds[3]-bounds[2], bounds[5]-bounds[4]]
        roiNode.SetCenter(center)
        roiNode.SetSize(size)
        roiNode.CreateDefaultDisplayNodes()
        roiNode.GetDisplayNode().SetHandlesInteractive(True)
        return roiNode

    def applyCrop(self, volume, roiNode):
        cropParams = slicer.vtkMRMLCropVolumeParametersNode()
        slicer.mrmlScene.AddNode(cropParams)
        tempNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLScalarVolumeNode", "__croptemp__")
        cropParams.SetInputVolumeNodeID(volume.GetID())
        cropParams.SetOutputVolumeNodeID(tempNode.GetID())
        cropParams.SetROINodeID(roiNode.GetID())
        cropParams.SetVoxelBased(True)
        slicer.modules.cropvolume.logic().Apply(cropParams)
        slicer.mrmlScene.RemoveNode(cropParams)
        # Overwrite original volume with cropped data
        volume.SetAndObserveImageData(tempNode.GetImageData())
        ijkToRAS = vtk.vtkMatrix4x4()
        tempNode.GetIJKToRASMatrix(ijkToRAS)
        volume.SetIJKToRASMatrix(ijkToRAS)
        volume.StorableModified()
        volume.Modified()
        slicer.mrmlScene.RemoveNode(tempNode)
        slicer.mrmlScene.RemoveNode(roiNode)

    def computeMidsagittalAlignment(self, preopVolume):
        """Computes the yaw rotation to align the skull midsagittal plane with x=0.
        Returns (centroid_ras, theta_rad) or None if too few bone voxels found.
        Does NOT modify the scene — call setMidlineTransform to apply."""

        # Downsample 4× and threshold to cortical bone only.
        # Upper bound excludes metal artifact blooms (dental implants etc. > 1500 HU).
        arr = slicer.util.arrayFromVolume(preopVolume)
        step = 4
        sub = arr[::step, ::step, ::step]
        kji = np.argwhere((sub > 400) & (sub < 1500))

        if len(kji) < 100:
            logging.warning("computeMidsagittalAlignment: too few cortical bone voxels, skipping.")
            return None

        # Convert downsampled IJK → RAS
        ijkToRAS = vtk.vtkMatrix4x4()
        preopVolume.GetIJKToRASMatrix(ijkToRAS)
        M = np.array([[ijkToRAS.GetElement(r, c) for c in range(4)] for r in range(4)])
        ijk_h = np.vstack([kji[:, 2] * step, kji[:, 1] * step, kji[:, 0] * step, np.ones(len(kji))])
        ras = (M @ ijk_h)[:3, :].T  # N × 3

        # 3D PCA; assign eigenvectors to axes by dot product (not eigenvalue rank)
        centroid = ras.mean(axis=0)
        _, eigenvectors = np.linalg.eigh(np.cov((ras - centroid).T))
        abs_dots = np.abs(eigenvectors.T @ np.eye(3))   # [evec, ras_axis]
        lr_idx = int(np.argmax(abs_dots[:, 0]))          # eigenvector closest to RAS x
        v_lr = eigenvectors[:, lr_idx]
        if v_lr[0] < 0:
            v_lr = -v_lr  # Right = +x

        # Project onto axial plane → yaw-only angle
        v_lr_xy = np.array([v_lr[0], v_lr[1]])
        v_lr_xy /= np.linalg.norm(v_lr_xy)
        theta = -np.arctan2(v_lr_xy[1], v_lr_xy[0])

        return centroid, theta

    def setMidlineTransform(self, preopVolume, centroid, theta_rad):
        """Creates (or replaces) the 'preop midsagittal alignment' transform node,
        rotating the preop CT by theta_rad around the skull centroid (yaw only).
        The transform is NOT hardened, so the spinbox can update it live."""

        cos_t, sin_t = np.cos(theta_rad), np.sin(theta_rad)
        R = np.array([[cos_t, -sin_t, 0],
                      [sin_t,  cos_t, 0],
                      [0,      0,     1]])
        # Rotate about centroid; also shift centroid x to 0 (midsagittal)
        t = np.array([0.0, centroid[1], centroid[2]]) - R @ centroid

        mat4 = np.eye(4)
        mat4[:3, :3] = R
        mat4[:3, 3] = t
        vtkMat = vtk.vtkMatrix4x4()
        for r in range(4):
            for c in range(4):
                vtkMat.SetElement(r, c, mat4[r, c])

        # Reuse existing node if present, otherwise create it
        transformNode = slicer.mrmlScene.GetFirstNodeByName("preop midsagittal alignment")
        if transformNode is None:
            transformNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLTransformNode")
            transformNode.SetName("preop midsagittal alignment")

        transformNode.SetMatrixTransformToParent(vtkMat)
        preopVolume.SetAndObserveTransformNodeID(transformNode.GetID())
        transformNode.CreateDefaultDisplayNodes()
        transformNode.GetDisplayNode().SetEditorVisibility(True)
        helperfunctions.centerTransformToNode(transformNode, preopVolume)

    def prepareForRegistration(self, preopVolume, postopVolume):
        pn = self.getParameterNode()

        pn.preopVolume.SetName("preop volume")
        pn.postopVolume.SetName("postop volume")

        if (pn.skullPlannedModel != None):
            pn.skullPlannedModel.SetName("Skull planned model")
            pn.skullPlannedModel.GetDisplayNode().SetColor(helperfunctions.COLOR_PLANNED)
            pn.skullPlannedModel.GetDisplayNode().SetVisibility(False)
            helperfunctions.applyMaterialToModelNode(
                pn.skullPlannedModel,
                helperfunctions.MATERIAL_BONE
            )

        # Harden the midsagittal alignment transform into the volume but keep the node
        # so that planning STLs can later be transformed from the original coordinate system
        alignTransform = slicer.mrmlScene.GetFirstNodeByName("preop midsagittal alignment")
        if alignTransform is not None:
            if alignTransform.GetDisplayNode():
                alignTransform.GetDisplayNode().SetEditorVisibility(False)
            preopVolume.HardenTransform()

        # display volumes in slice views
        slicer.util.setSliceViewerLayers(foreground=postopVolume, foregroundOpacity=0.5, background=preopVolume)

        # enable Volume rendering
        # helperfunctions.showVolumeRendering(preopVolume, preset="CT-Bone", hideSoftTissue=True, thresholds=[250,800])
        # helperfunctions.showVolumeRendering(postopVolume, preset="CT-Bone", hideSoftTissue=True, thresholds=[650, 850])

        alignmentTransform = helperfunctions.alignNodesByCenterOfGravity(postopVolume, preopVolume)
        alignmentTransform.CreateDefaultDisplayNodes()
        alignmentTransform.GetDisplayNode().SetEditorVisibility(True)
        alignmentTransform.SetName("manual registration postop to preop")
        
        helperfunctions.setDefault3dView()
        
        return
    
    def computeRegistrationNCC(self, preopVolume, postopVolume):
        transformNode = slicer.util.getNode("registration postop to preop")

        # Resample postop into preop space using the BRAINS result transform
        resampledNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLScalarVolumeNode")
        params = {
            "inputVolume": postopVolume.GetID(),
            "referenceVolume": preopVolume.GetID(),
            "outputVolume": resampledNode.GetID(),
            "transformationFile": transformNode.GetID(),
        }
        slicer.cli.run(slicer.modules.resamplescalarvectordwivolume, None, params, wait_for_completion=True)

        # NCC over non-background voxels (air in CT is typically < -900 HU)
        a = slicer.util.arrayFromVolume(preopVolume).flatten().astype(np.float64)
        b = slicer.util.arrayFromVolume(resampledNode).flatten().astype(np.float64)
        mask = (a > -900) & (b > -900)
        a, b = a[mask], b[mask]
        ncc = float(np.mean(((a - a.mean()) / a.std()) * ((b - b.mean()) / b.std())))

        slicer.mrmlScene.RemoveNode(resampledNode)
        self.getParameterNode().nccRegistration = ncc
        return ncc

    def openRegistrationMaskEditor(self, preopVolume):
        pn = self.getParameterNode()

        # Create a new segmentation node if none exists yet
        maskSegNode = pn.registrationMaskSegmentation
        if maskSegNode is None:
            maskSegNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode")
            maskSegNode.SetName("registration mask")
            maskSegNode.GetSegmentation().AddEmptySegment("mask")
            pn.registrationMaskSegmentation = maskSegNode

        # Volume rendering for spatial orientation while drawing the mask
        helperfunctions.showVolumeRendering(preopVolume, preset="CT-Bone", hideSoftTissue=True, thresholds=[600, 800])

        # Show mask segmentation in 3D at 50% opacity
        maskSegNode.CreateDefaultDisplayNodes()
        maskSegNode.CreateClosedSurfaceRepresentation()
        maskSegNode.GetDisplayNode().SetVisibility3D(True)
        maskSegNode.GetDisplayNode().SetOpacity3D(0.5)

        # Center slice views and set 3D view to AP (anterior)
        slicer.util.resetSliceViews()
        threeDView = slicer.app.layoutManager().threeDWidget(0).threeDView()
        threeDView.rotateToViewAxis(3)  # 3 = Anterior
        threeDView.resetFocalPoint()

        # Switch to Segment Editor
        slicer.util.selectModule("SegmentEditor")
        editor = slicer.modules.SegmentEditorWidget.editor
        editor.setSegmentationNode(maskSegNode)
        editor.setSourceVolumeNode(preopVolume)

        # Activate Scissors in FillInside mode
        editor.setActiveEffectByName("Scissors")
        effect = editor.activeEffect()
        if effect:
            effect.setParameter("Operation", "FillInside")
            effect.setParameter("Shape", "FreeForm")

    def performVolumeRegistration(self, preopVolume, postopVolume):
        pn = self.getParameterNode()

        try:
            slicer.util.getNode("manual registration postop to preop").GetDisplayNode().SetEditorVisibility(False)
        except Exception:
            pass

        transformNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLTransformNode")
        transformNode.SetName("registration postop to preop")

        postopVolume.HardenTransform()

        # Run registration
        parameters = {}
        parameters["fixedVolume"] = preopVolume.GetID()
        parameters["movingVolume"] = postopVolume.GetID()
        parameters["linearTransform"] = transformNode.GetID()
        parameters["useRigid"] = True  # options include: "useRigid", "useAffine", "useBSpline"
        parameters["initializeTransformMode"] = "Off"
        parameters["samplingPercentage"] = 0.002

        maskSegNode = pn.registrationMaskSegmentation
        if maskSegNode is not None and maskSegNode.GetSegmentation().GetNumberOfSegments() > 0:
            parameters["fixedBinaryVolume"] = maskSegNode.GetID()
            parameters["maskProcessingMode"] = "ROI"

        return slicer.cli.run(slicer.modules.brainsfit, None, parameters, wait_for_completion=False)
    
    def recenterPlannningSTLs(self):
        skullPlannedModel = self.getParameterNode().skullPlannedModel
        preopVolume = self.getParameterNode().preopVolume

        # remove existing nodes from the scene
        helperfunctions.removeNodesFromSceneByName([
            "manual registration plan to preop"
        ])

        if (skullPlannedModel == None):
            raise Exception("No model for the planned skull position set")
            return
        

        preopVolume.SetDisplayVisibility(True)
        slicer.util.setSliceViewerLayers(background=preopVolume)

        skullPlannedModel.SetDisplayVisibility(True)
        skullPlannedModel.GetDisplayNode().SetVisibility2D(True)

        # center the planned skull to the preop volume
        centeringTransform = helperfunctions.alignNodesByCenterOfGravity(skullPlannedModel, preopVolume)    

        # display the transform for manual correction
        centeringTransform.SetName("manual registration plan to preop")
        centeringTransform.CreateDefaultDisplayNodes()
        centeringTransform.GetDisplayNode().SetEditorVisibility(True)

        self.getParameterNode().postopVolume.SetDisplayVisibility(False)

        # harden the transforms and delete the transform node from the scene
        # skullPlannedModel.HardenTranform()
        # psiPlannedModel.HardenTransform()
        # slicer.mrmlScene.RemoveNode(centeringTransform)

        return
    
    def segmentPreopVolume(self):
        # gather data
        volumeNode = self.getParameterNode().preopVolume
        skullPlannedModel = self.getParameterNode().skullPlannedModel

        # remove existing nodes from the scene
        helperfunctions.removeNodesFromSceneByName([
            volumeNode.GetName() + " segmentation",
            volumeNode.GetName() + " cropped",
            skullPlannedModel.GetName() + " Bounding Box"
        ])

        # harden the current transform of the skull and psi models
        transformNodeId = skullPlannedModel.GetTransformNodeID()
        if (transformNodeId != None):
            skullPlannedModel.HardenTransform()
            slicer.mrmlScene.GetNodeByID(transformNodeId).GetDisplayNode().SetEditorVisibility(False)

        # build the roi
        roi = helperfunctions.builROIfromNodeBounds(self.getParameterNode().skullPlannedModel, None, 2)

        # crop the preop volume by the to the roi
        cropVolumeParameters = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLCropVolumeParametersNode")
        cropVolumeParameters.SetInputVolumeNodeID(volumeNode.GetID())
        cropVolumeParameters.SetROINodeID(roi.GetID())
        slicer.modules.cropvolume.logic().Apply(cropVolumeParameters)
        croppedVolume = cropVolumeParameters.GetOutputVolumeNode()
        croppedVolume.SetName(volumeNode.GetName() + " cropped")

        # hide the roi
        roi.GetDisplayNode().SetVisibility(False)

        # create segmentation for the cropped volume
        segmentationNode = slicer.vtkMRMLSegmentationNode()
        segmentationNode.SetName(volumeNode.GetName() + " segmentation")
        segmentId = segmentationNode.GetSegmentation().AddEmptySegment()
        segment = segmentationNode.GetSegmentation().GetSegment(segmentId)
        segment.SetName(volumeNode.GetName() + " segment")
        slicer.mrmlScene.AddNode(segmentationNode)

        # compute segment data
        # first copy the segment binary map to an array
        segmentArray = slicer.util.arrayFromSegmentBinaryLabelmap(segmentationNode, segmentId, croppedVolume)
        # then set all voxels in the labelmap, where  HU in the volume is above the threshold, to 1
        # 600 seems to be a good threshold for the midface
        # TODO: allow customization of the threshold
        segmentArray[slicer.util.arrayFromVolume(croppedVolume) > 200] = 1
        # finally update the segment with the data
        slicer.util.updateSegmentBinaryLabelmapFromArray(segmentArray, segmentationNode, segmentId,croppedVolume)

        # now display the segmentation in the 3d View
        segmentationNode.GetSegmentation().CreateRepresentation(slicer.vtkSegmentationConverter().GetSegmentationClosedSurfaceRepresentationName())
        segment.SetColor(helperfunctions.COLOR_PREOP)

        # and hide the volume renderings
        volumeNode.SetDisplayVisibility(False)
        self.getParameterNode().postopVolume.SetDisplayVisibility(False)

    def alignPlanToPreop(self):
        # remove existing nodes from the scene
        helperfunctions.removeNodesFromSceneByName([
            "registration plan to preop"
        ])

        # convert the preop segmentation to a model
        segmentationNode = slicer.util.getNode(self.getParameterNode().preopVolume.GetName() + " segmentation")
        segmentationNode.GetDisplayNode().SetVisibility(False)
        slicer.modules.segmentations.logic().ExportVisibleSegmentsToModels(segmentationNode, 0)

        # change settings for the preop model
        preopModel = slicer.util.getNode(self.getParameterNode().preopVolume.GetName() + " segment")
        preopModel.SetName(self.getParameterNode().preopVolume.GetName() + " model")
        preopModel.GetDisplayNode().SetColor(helperfunctions.COLOR_PREOP)
        helperfunctions.applyMaterialToModelNode(
                preopModel,
                helperfunctions.MATERIAL_BONE
            )

        # register the planned model to the preop model using the ModelRegistration-Module
        import ModelRegistration
        sourceToTargetTransform = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLinearTransformNode")
        sourceToTargetTransform.SetName("registration plan to preop")
        mrLogic = ModelRegistration.ModelRegistrationLogic()
        mrLogic.run(self.getParameterNode().skullPlannedModel, preopModel, sourceToTargetTransform)
        self.getParameterNode().rmsPlanToPreop = mrLogic.ComputeMeanDistance(self.getParameterNode().skullPlannedModel, preopModel, sourceToTargetTransform)

        # apply the new transform to the planned models
        self.getParameterNode().skullPlannedModel.SetAndObserveTransformNodeID(sourceToTargetTransform.GetID())
        
        helperfunctions.centerTransformToNode(sourceToTargetTransform, preopModel)

        return

    def alignPlanToPreopToModel(self, targetModel):
        """Register skull planned model directly to an already-existing preop model node."""
        helperfunctions.removeNodesFromSceneByName(["registration plan to preop"])

        sourceToTargetTransform = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLinearTransformNode")
        sourceToTargetTransform.SetName("registration plan to preop")

        import ModelRegistration
        mrLogic = ModelRegistration.ModelRegistrationLogic()
        mrLogic.run(self.getParameterNode().skullPlannedModel, targetModel, sourceToTargetTransform)
        self.getParameterNode().rmsPlanToPreop = mrLogic.ComputeMeanDistance(
            self.getParameterNode().skullPlannedModel, targetModel, sourceToTargetTransform
        )

        self.getParameterNode().skullPlannedModel.SetAndObserveTransformNodeID(sourceToTargetTransform.GetID())
        helperfunctions.centerTransformToNode(sourceToTargetTransform, targetModel)

    # applies the regisrtation transform (plan to preop) for the selected psi
    def applyTransformsToPlannedModel(self):
        pn = self.getParameterNode()
        psiPlannedModel = pn.psiPlannedModel
        psiPlannedModel.SetDisplayVisibility(True)

        try:
            registrationPlanToPreopManual = slicer.util.getNode("manual registration plan to preop")
            psiPlannedModel.SetAndObserveTransformNodeID(registrationPlanToPreopManual.GetID())
            psiPlannedModel.HardenTransform()
        except Exception as e:
            print("No manual registration from plan to preop found. Skipping.", file=sys.stderr)

        try:
            registrationPlanToPreop = slicer.util.getNode("registration plan to preop")
            psiPlannedModel.SetAndObserveTransformNodeID(registrationPlanToPreop.GetID())
            psiPlannedModel.HardenTransform()
        except Exception as e:
            print("No computed registration from plan to preop found. Skipping.", file=sys.stderr)

    def applyAlignmentTransformToModel(self, model):
        """Applies the plan-to-preop registration transform(s) to the given model and hardens."""
        try:
            manual = slicer.util.getNode("manual registration plan to preop")
            model.SetAndObserveTransformNodeID(manual.GetID())
            model.HardenTransform()
        except Exception:
            pass
        try:
            computed = slicer.util.getNode("registration plan to preop")
            model.SetAndObserveTransformNodeID(computed.GetID())
            model.HardenTransform()
        except Exception:
            pass

    # prepares the scene for the segmentation of the intraoperative situation of the selected psi
    def prepareSegmentation(self):

        pn = self.getParameterNode()

        preopVolume = pn.preopVolume
        postopVolume = pn.postopVolume
        psiPlannedModel = pn.psiPlannedModel
        skullPlannedModel = pn.skullPlannedModel

        #pn.psiPlannedModel.SetName(pn.psiPlannedName)
        pn.psiPlannedModel.GetDisplayNode().SetColor(helperfunctions.COLOR_PLANNED)
        pn.psiPlannedModel.GetDisplayNode().SetVisibility(False)
        helperfunctions.applyMaterialToModelNode(
            pn.psiPlannedModel,
            helperfunctions.MATERIAL_METAL
        )

        # remove existing nodes from the scene
        helperfunctions.removeNodesFromSceneByName([
            psiPlannedModel.GetName() + " postop Segmentation",
            postopVolume.GetName() + " cropped to " + psiPlannedModel.GetName(),
            psiPlannedModel.GetName() + " Bounding Box"
        ])

        preopVolume.SetDisplayVisibility(False)
        postopVolume.SetDisplayVisibility(False)
        psiPlannedModel.SetDisplayVisibility(True)

        if (skullPlannedModel != None):
            skullPlannedModel.SetDisplayVisibility(False)
        
        try:
            preopSegmentation = slicer.util.getNode(preopVolume.GetName() + " segmentation")
            preopSegmentation.SetDisplayVisibility(False)
        except Exception as e:
            print("No preop segmentation found. If you have not registered the planned stl to the preop ct this is no problem and can be ignored", file=sys.stderr)

        try:
            preopSegmentation = slicer.util.getNode(preopVolume.GetName() + " model")
            preopSegmentation.SetDisplayVisibility(False)
        except Exception as e:
            print("No preop model found. If you have not registered the planned stl to the preop ct this is no problem and can be ignored", file=sys.stderr)

        # Crop postop volume to the approximat bounds of the planned PSI and store in new Volume node
        roi = helperfunctions.builROIfromNodeBounds(psiPlannedModel, None, 2)
        cropVolumeParameters = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLCropVolumeParametersNode")
        cropVolumeParameters.SetInputVolumeNodeID(postopVolume.GetID())
        cropVolumeParameters.SetROINodeID(roi.GetID())
        slicer.modules.cropvolume.logic().Apply(cropVolumeParameters)

        croppedCT = cropVolumeParameters.GetOutputVolumeNode()
        croppedCT.SetName(postopVolume.GetName() + " cropped to " + psiPlannedModel.GetName())

        slicer.util.setSliceViewerLayers(background=croppedCT, foreground=None)
        slicer.util.resetSliceViews()
        roi.GetDisplayNode().SetVisibility(False)
        helperfunctions.setDefault3dView(5)

        # create Segmentations
        segmentationNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode")
        segmentationNode.SetName(psiPlannedModel.GetName() + " postop Segmentation")
        segmentation = segmentationNode.GetSegmentation()
        segmentId = segmentation.AddEmptySegment()
        segment = segmentation.GetSegment(segmentId)
        segment.SetColor(helperfunctions.COLOR_POSTOP)
        segment.SetName(psiPlannedModel.GetName() + " postop Segment")
        pn.psiPostopSegmentation = segmentationNode
        pn.psiPostopSegmentId = segmentId
     
        # switch to SegmentEditor
        slicer.util.selectModule("SegmentEditor")

        # prefill segment editor with correct settings and run a thresholding
        # effect with appropriate settings
        slicer.modules.SegmentEditorWidget.editor.setSegmentationNode(segmentationNode)
        slicer.modules.SegmentEditorWidget.editor.setSourceVolumeNode(croppedCT)
        slicer.modules.SegmentEditorWidget.editor.setActiveEffectByName("Threshold")
        effect = slicer.modules.SegmentEditorWidget.editor.activeEffect()
        effect.setParameter(
            "MinimumThreshold",
            1750
        )
        effect.setParameter(
            "MaximumThreshold",
            slicer.util.arrayFromVolume(croppedCT).max()
        )
        effect.self().onApply()

        # show 3D representation of the segmentation
        segmentationNode.CreateClosedSurfaceRepresentation()
        slicer.modules.SegmentEditorWidget.editor.setActiveEffectByName("Scissors")


    def alignPSIModels(self):
        psiPlannedModel = self.getParameterNode().psiPlannedModel
        psiPlannedModel.SetDisplayVisibility(False)

        # remove existing nodes from the scene
        helperfunctions.removeNodesFromSceneByName([
            f"{psiPlannedModel.GetName()} postop model",
            f"{psiPlannedModel.GetName()} registered to postop"
        ])

        # convert Segmentation to model
        segmentationNode = self.getParameterNode().psiPostopSegmentation
        if segmentationNode is None:
            segmentationNode = slicer.util.getNode(f"{psiPlannedModel.GetName()} postop Segmentation")
        segmentationNode.SetDisplayVisibility(False)
        seg = segmentationNode.GetSegmentation()
        segmentId = self.getParameterNode().psiPostopSegmentId or seg.GetNthSegmentID(0)
        segmentName = seg.GetSegment(segmentId).GetName()
        # Temporarily hide all other segments so only the selected one is exported
        dispNode = segmentationNode.GetDisplayNode()
        savedVisibility = {seg.GetNthSegmentID(i): dispNode.GetSegmentVisibility(seg.GetNthSegmentID(i))
                           for i in range(seg.GetNumberOfSegments())}
        for sId in savedVisibility:
            dispNode.SetSegmentVisibility(sId, sId == segmentId)
        slicer.modules.segmentations.logic().ExportVisibleSegmentsToModels(segmentationNode, 0)
        for sId, vis in savedVisibility.items():
            dispNode.SetSegmentVisibility(sId, vis)
        psiPostopModel = slicer.util.getNode(segmentName)
        psiPostopModel.SetName(f"{psiPlannedModel.GetName()} postop model")
        psiPostopModel.GetDisplayNode().SetColor(np.array(helperfunctions.COLOR_POSTOP)*0.7)

        # clone planned model and register to postop position
        registeredModel, rms = helperfunctions.registerSourceModelToTargetModel(
            psiPlannedModel,
            psiPostopModel,
            f"registration {psiPlannedModel.GetName()} to postop",
            f"{psiPlannedModel.GetName()} registered to postop",
            hardenTransform=False)
        
        registeredModel.GetDisplayNode().SetColor(helperfunctions.COLOR_POSTOP)
        registeredModel.GetDisplayNode().SetVisibility2D(True)
        helperfunctions.applyMaterialToModelNode(registeredModel, helperfunctions.MATERIAL_BONE)
        
        self.getParameterNode().rmsPlanToPostop = rms
        self.getParameterNode().psiPostopModel = registeredModel

        alignmentTransform = slicer.util.getNode(f"registration {psiPlannedModel.GetName()} to postop")
        alignmentTransform.CreateDefaultDisplayNodes()
        alignmentTransform.GetDisplayNode().SetEditorVisibility(True)
        alignmentTransform.GetDisplayNode().SetRotationHandleComponentVisibility3D([True, True, True, True])
        helperfunctions.centerTransformToNode(alignmentTransform, registeredModel)


    def manualAlignPSIModel(self):
        psiPlannedModel = self.getParameterNode().psiPlannedModel
        psiPlannedModel.SetDisplayVisibility(False)

        clonedName = f"{psiPlannedModel.GetName()} registered to postop"
        helperfunctions.removeNodesFromSceneByName([clonedName])

        clonedModel = helperfunctions.cloneModel(psiPlannedModel, clonedName)
        clonedModel.GetDisplayNode().SetColor(helperfunctions.COLOR_POSTOP)
        clonedModel.GetDisplayNode().SetVisibility(True)
        clonedModel.GetDisplayNode().SetVisibility2D(True)
        helperfunctions.applyMaterialToModelNode(clonedModel, helperfunctions.MATERIAL_BONE)

        transformName = f"registration {psiPlannedModel.GetName()} to postop"
        helperfunctions.removeNodesFromSceneByName([transformName])
        alignmentTransform = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLinearTransformNode")
        alignmentTransform.SetName(transformName)

        clonedModel.SetAndObserveTransformNodeID(alignmentTransform.GetID())

        alignmentTransform.CreateDefaultDisplayNodes()
        alignmentTransform.GetDisplayNode().SetEditorVisibility(True)
        alignmentTransform.GetDisplayNode().SetRotationHandleComponentVisibility3D([True, True, True, True])
        helperfunctions.centerTransformToNode(alignmentTransform, clonedModel)

        self.getParameterNode().psiPostopModel = clonedModel

    def calculatePSIModelToModelDistance(self):
        psiPlannedModel = self.getParameterNode().psiPlannedModel
        psiPlannedModelRegisteredToPostop = self.getParameterNode().psiPostopModel
                
        # before calculating the distances we have to harden the transform
        psiPlannedModelRegisteredToPostop.HardenTransform()

        # remove existing nodes from the scene
        helperfunctions.removeNodesFromSceneByName([
            f"{psiPlannedModel.GetName()} distance model planned postop"
        ])

		# try to hide models that are not needed
        try:
            slicer.util.getNode(f"{psiPlannedModel.GetName()} postop model").SetDisplayVisibility(False)
            slicer.util.getNode(f"registration {psiPlannedModel.GetName()} to postop").GetDisplayNode().SetEditorVisibility(False)
        except Exception as e:
            print("Error while hiding unneeded nodes.", file=sys.stderr)    

        distanceNode = helperfunctions.computeModelToModelDistancePointByPoint(
            psiPlannedModel,
            psiPlannedModelRegisteredToPostop,
            f"{psiPlannedModel.GetName()} distance model planned postop"
        )
        
        self.getParameterNode().psiDistanceModel = distanceNode

        helperfunctions.hideAllDisplayNodes()
        distanceNode.SetDisplayVisibility(True)
        helperfunctions.applyMaterialToModelNode(distanceNode, helperfunctions.MATERIAL_BONE)

    
    def printPSIResults(self):
        psiPlannedModel = self.getParameterNode().psiPlannedModel
        psiPostopModel = self.getParameterNode().psiPostopModel
        psiDistanceModel = self.getParameterNode().psiDistanceModel

        # remove existing nodes from the scene
        helperfunctions.removeNodesFromSceneByName([
            psiPlannedModel.GetName() + " segmentation",
            psiPostopModel.GetName() + " segmentation",
            psiPlannedModel.GetName() + " OBB",
            psiPostopModel.GetName() + " OBB",
        ])

        return self.printResults(
            psiPlannedModel.GetName(),
            psiPlannedModel,
            psiPostopModel,
            psiDistanceModel,
            psiPlannedModel
        )

    def printAllResults(self):
        resultsMaxilla = self.printMaxillaResults()
        resultsPSI = self.printPSIResults()

        combinedResults = resultsMaxilla | resultsPSI

        outputPath = os.path.dirname(slicer.util.getNode("max preop").GetStorageNode().GetFileName())
        outputFilename = os.path.join(outputPath, 'output_all.csv')
        with open(outputFilename, 'w') as output:
            writer = csv.writer(output)

            writer.writerow(combinedResults.keys())
            writer.writerow(combinedResults.values())

        print(f"Combined output written to {outputFilename}")

    # generic method for outputting the results (Model-To-Model-Distance, Hausdorf-Distance, Dice-Coefficient),
    # Rotation and Location
    def printResults(self, prefix, nodePlanned, nodePostop, nodeDistance, nodeForFilename):
        # remove whitespace from prefix
        prefix = prefix.replace(" ","_")

        # remove existing nodes from the scene
        helperfunctions.removeNodesFromSceneByName([
            nodePlanned.GetName() + " segmentation",
            nodePostop.GetName() +  " segmentation",
            nodePlanned.GetName() + " OBB",
            nodePostop.GetName() +  " OBB",
        ])

        resultsTableRow = {}

        # Calaculating dice and Hausdorff is done using segment statistics, so the models need to be
        # converted to segmentations
        segmentationPlanned	= helperfunctions.convertModelToSegmentation(nodePlanned)
        segmentationPostop	= helperfunctions.convertModelToSegmentation(nodePostop)
        diceAndHausdorff	= helperfunctions.getDiceAndHausdorff(segmentationPlanned, segmentationPostop)
        
        resultsTableRow[f'{prefix}_rms_plan_to_preop'] = self.getParameterNode().rmsPlanToPreop
        resultsTableRow[f'{prefix}_rms_plan_to_postop'] = self.getParameterNode().rmsPlanToPostop

        resultsTableRow[f'{prefix}_dice_plan_intraop'] = diceAndHausdorff['dice']
        resultsTableRow[f'{prefix}_hausdorff_avg_planned_postop'] = diceAndHausdorff['avgHausdorffDistance']
        resultsTableRow[f'{prefix}_hausdorff_max_planned_postop'] = diceAndHausdorff['maxHausdorffDistance']
            
        # calculate angle bewteen planned and postop position
        transformNode = slicer.util.getNode(f"registration {nodePlanned.GetName()} to postop")
        rotMat = slicer.util.arrayFromTransformMatrix(transformNode)
        rotation = scipy.spatial.transform.Rotation.from_matrix(rotMat[:3, :3])
        euler_angles_xyz = rotation.as_euler("xyz", degrees=True)

        resultsTableRow[f'{prefix}_rotation_x'] = euler_angles_xyz[0]
        resultsTableRow[f'{prefix}_rotation_y'] = euler_angles_xyz[1]
        resultsTableRow[f'{prefix}_rotation_z'] = euler_angles_xyz[2]

        # Calculate vector between bounding box centers
        roisPlanned = helperfunctions.buildSegmentOBB(segmentationPlanned, helperfunctions.COLOR_PLANNED, True)
        roisPostop = helperfunctions.buildSegmentOBB(segmentationPostop, helperfunctions.COLOR_POSTOP, True)
        roiPlannedCenter = [0,0,0]
        roisPlanned[0].GetCenter(roiPlannedCenter)
        roiPostopCenter = [0,0,0]
        roisPostop[0].GetCenter(roiPostopCenter)
        vector = np.array(roiPostopCenter) - np.array(roiPlannedCenter)
        distance = np.linalg.norm(vector)

        roisPlanned[0].SetDisplayVisibility(False)
        roisPostop[0].SetDisplayVisibility(False)

        helperfunctions.alignCameraToBoundingBox(roisPlanned[0], axis=0)

        resultsTableRow[f'{prefix}_distance'] = distance
        resultsTableRow[f'{prefix}_vector_x'] = vector[0]
        resultsTableRow[f'{prefix}_vector_y'] = vector[1]
        resultsTableRow[f'{prefix}_vector_z'] = vector[2]

        # Results of Model-To-Model-Distance
        distanceArrayTotal	=	slicer.util.arrayFromModelPointData(nodeDistance, "point to point distance signed")
        resultsTableRow[f'{prefix}_m2m_rms'] = np.sqrt(np.mean(np.square(distanceArrayTotal)))
        resultsTableRow['registration_ncc'] = self.getParameterNode().nccRegistration
        
        for key, value in resultsTableRow.items():
            print(f"{key[0:30]: <30}: {value:.3f}") 

        # print(*[f"{x[0:16]: ^16}" for x in resultsTableRow.keys()])
        # print(*[f"{x: ^16.3f}" for x in resultsTableRow.values()])

        outputPath = os.path.dirname(nodeForFilename.GetStorageNode().GetFileName())
        with open(os.path.join(outputPath, f'output_{prefix}.csv'), 'w') as output:
            writer = csv.writer(output)

            writer.writerow(resultsTableRow.keys())
            writer.writerow(resultsTableRow.values())

        # print(resultsTableRow)
        # print(";".join(map(str, resultsTableRow.values())))
        
        return resultsTableRow  
