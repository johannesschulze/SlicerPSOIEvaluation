import os
from typing import Annotated, Optional

import numpy as np
import slicer
import scipy
import vtk
# from slicer import scipy
# from slicer import vtk
import SegmentStatistics
import qt
import csv

from slicer.i18n import tr as _
from slicer.i18n import translate
from slicer.ScriptedLoadableModule import ScriptedLoadableModuleWidget, ScriptedLoadableModule, ScriptedLoadableModuleLogic, ScriptedLoadableModuleTest
from slicer.util import VTKObservationMixin
from slicer.parameterNodeWrapper import (
	parameterNodeWrapper,
	WithinRange,
)

from slicer import vtkMRMLScalarVolumeNode

COLOR_PREOP		= (0.5, 0.5, 0.5)	# Grau
COLOR_PLANNED		= (0.4, 1.0, 0.4)	# Hell-Grün
COLOR_POSTOP		= (0.8, 0.5, 0.7)	# lila
COLOR_REGISTERED	= (0.3,0.4,1.0)		# blau
COLOR_DISTANCE		= (1, 0.8, 0.6)

COLOR_MAP_MANDIBLE = {
	'mandible preop'	: COLOR_PREOP,
	'mandible planned'	: COLOR_PLANNED,
	'mandible postop'	: COLOR_POSTOP
}

COLOR_MAP_MAXILLA = {
	'max preop' :		COLOR_PREOP,
	'max planned' :		COLOR_PLANNED,
	'max postop' :		COLOR_POSTOP,
	'lefortpsi planned'	: COLOR_PLANNED,
	'lefortpsi postop'	: COLOR_POSTOP
}

#
# LeFortPSIWorkflowModule
#


class LeFortPSIWorkflowModule(ScriptedLoadableModule):
	"""Uses ScriptedLoadableModule base class, available at:
	https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
	"""

	def __init__(self, parent):
		ScriptedLoadableModule.__init__(self, parent)
		self.parent.title = _("LeFortPSIWorkflowModule")  # TODO: make this more human readable by adding spaces
		# TODO: set categories (folders where the module shows up in the module selector)
		self.parent.categories = [translate("qSlicerAbstractCoreModule", "Ulm")]
		self.parent.dependencies = ['SegmentStatistics','ModelRegistration'] 
		self.parent.contributors = ["Johannes Schulze (Bundeswehrkrankenhaus Ulm)"]
		self.parent.helpText = _("""
This is a module for the automation of the proceses related to the LeFort-PSI research at the German Armed Military Forces Hospital in Ulm, Germany.
""")
		# TODO: replace with organization, grant and thanks
		self.parent.acknowledgementText = _("""
This file was originally developed by Johannes Schulze (Bundeswehrkrankenhaus Ulm) without any funding or grants.
""")

		# Additional initialization step after application startup is complete
		# slicer.app.connect("startupCompleted()", registerSampleData)


		slicer.app.connect("startupCompleted()", self.onStartupCompleted)

	# gets called when 3D slicer has completed it's startup, even before the module is opend
	def onStartupCompleted(self):
		newToolBar = qt.QToolBar()
		newToolBar.setWindowTitle("Test-Toolbar")
		newToolBar.setObjectName("TestToolBar")
		slicer.util.mainWindow().addToolBar(newToolBar)

		# Add Toogle Dark Mode Action
		moduleIcon = qt.QIcon(self.resourcePath('Icons/moon-icon.png'))
		self.StyleAction = newToolBar.addAction(moduleIcon, "Toggle Dark Mode")
		self.StyleAction.triggered.connect(self.onSlicerStyleToggle)

		# Main window takes care of saving and restoring toolbar geometry and state.
  		# However, when state is restored the sequence browser toolbar was not created yet.
  		# We need to restore the main window state again, now, that the Sequences toolbar is available.
		settings = qt.QSettings()
		settings.beginGroup("MainWindow")

		if (settings.value("RestoreGeometry", False)):
			slicer.util.mainWindow().restoreState(settings.value("windowState"))

		settings.endGroup()

		# mainToolBar = slicer.util.findChild(slicer.util.mainWindow(), 'ModuleToolBar')
		# add_widget = True
	
		# for element in mainToolBar.actions():
		# 	if element.text == "Toggle Dark Mode":
		# 		self.StyleAction = element
		# 		add_widget = False
		
		# if add_widget:        
		# 	moduleIcon = qt.QIcon(self.resourcePath('Icons/moon-icon.png'))
		# 	self.StyleAction = mainToolBar.addAction(moduleIcon, "Toggle Dark Mode")

	def onSlicerStyleToggle(self) -> None:
		
		# based on qSlicerSettingsStylesPanel::onStyleChanged

		currentStyleName = slicer.app.style().objectName

		if(currentStyleName == 'slicer'):
			slicer.app.setStyle('Dark Slicer')
		else:
			slicer.app.setStyle('Slicer')

		slicer.app.installEventFilter(slicer.app.style())
		slicer.app.setPalette(slicer.app.style().standardPalette())
#
# LeFortPSIWorkflowModuleParameterNode
#


@parameterNodeWrapper
class LeFortPSIWorkflowModuleParameterNode:
	"""
	The parameters needed by module.

	inputVolume - The volume to threshold.
	imageThreshold - The value at which to threshold the input volume.
	invertThreshold - If true, will invert the threshold.
	thresholdedVolume - The output volume that will contain the thresholded volume.
	invertedVolume - The output volume that will contain the inverted thresholded volume.
	"""

	inputVolume: vtkMRMLScalarVolumeNode
	imageThreshold: Annotated[float, WithinRange(-100, 500)] = 100
	invertThreshold: bool = False
	thresholdedVolume: vtkMRMLScalarVolumeNode
	invertedVolume: vtkMRMLScalarVolumeNode


#
# LeFortPSIWorkflowModuleWidget
#


class LeFortPSIWorkflowModuleWidget(ScriptedLoadableModuleWidget, VTKObservationMixin):
	"""Uses ScriptedLoadableModuleWidget base class, available at:
	https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
	"""

	def __init__(self, parent=None) -> None:
		"""Called when the user opens the module the first time and the widget is initialized."""
		ScriptedLoadableModuleWidget.__init__(self, parent)
		VTKObservationMixin.__init__(self)  # needed for parameter node observation
		self.logic = None
		self._parameterNode = None
		self._parameterNodeGuiTag = None

	def setup(self) -> None:
		"""Called when the user opens the module the first time and the widget is initialized."""
		ScriptedLoadableModuleWidget.setup(self)

		# Load widget from .ui file (created by Qt Designer).
		# Additional widgets can be instantiated manually and added to self.layout.
		uiWidget = slicer.util.loadUI(self.resourcePath("UI/LeFortPSIWorkflowModule.ui"))
		self.layout.addWidget(uiWidget)
		self.ui = slicer.util.childWidgetVariables(uiWidget)

		# Set scene in MRML widgets. Make sure that in Qt designer the top-level qMRMLWidget's
		# "mrmlSceneChanged(vtkMRMLScene*)" signal in is connected to each MRML widget's.
		# "setMRMLScene(vtkMRMLScene*)" slot.
		uiWidget.setMRMLScene(slicer.mrmlScene)

		# Create logic class. Logic implements all computations that should be possible to run
		# in batch mode, without a graphical user interface.
		self.logic = LeFortPSIWorkflowModuleLogic()

		# Connections

		# These connections ensure that we update parameter node when scene is closed
		self.addObserver(slicer.mrmlScene, slicer.mrmlScene.StartCloseEvent, self.onSceneStartClose)
		self.addObserver(slicer.mrmlScene, slicer.mrmlScene.EndCloseEvent, self.onSceneEndClose)

		# Buttons
		self.ui.prepareModelsButton.connect("clicked(bool)", self.logic.prepareModels)
		
		# Buttons for Maxilla Model handling
		self.ui.markupsButton.connect("clicked(bool)", self.logic.createMarkupsForPlanes)
		self.ui.planesButton.connect("clicked(bool)", self.logic.createResectionPlanes)
		self.ui.splitModelsButton.connect("clicked(bool)", self.logic.splitMaxillaFromMidface)
		self.ui.alignMaxillaModelsButton.connect("clicked(bool)", self.logic.alignMaxillaModels)
		self.ui.calculateM2MDistanceButton.connect("clicked(bool)", self.logic.calculateMaxillaModelToModelDistance)
		self.ui.printMaxillaResultsButton.connect("clicked(bool)", self.logic.printMaxillaResults)

		# Buttons for PSI Model handling
		self.ui.alignPSIsButton.connect("clicked(bool)", self.logic.alignPSIModels)
		self.ui.calculateM2MDistancePSIButton.connect("clicked(bool)", self.logic.calculatePSIModelToModelDistance)
		self.ui.printPSIResultsButton.connect("clicked(bool)", self.logic.printPSIResults)

		# Button for final Output of all Results
		self.ui.printAllResultsButton.connect("clicked(bool)", self.logic.printAllResults)

		# Make sure parameter node is initialized (needed for module reload)
		self.initializeParameterNode()

	def cleanup(self) -> None:
		"""Called when the application closes and the module widget is destroyed."""
		self.removeObservers()

	def enter(self) -> None:
		"""Called each time the user opens this module."""
		# Make sure parameter node exists and observed
		self.initializeParameterNode()

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

	def onClearConsoleButtonClick(self) -> None:
		slicer.app.pythonConsole().clear()

	def initializeParameterNode(self) -> None:
		"""Ensure parameter node exists and observed."""
		# Parameter node stores all user choices in parameter values, node selections, etc.
		# so that when the scene is saved and reloaded, these settings are restored.

		self.setParameterNode(self.logic.getParameterNode())

		# Select default input nodes if nothing is selected yet to save a few clicks for the user
		if not self._parameterNode.inputVolume:
			firstVolumeNode = slicer.mrmlScene.GetFirstNodeByClass("vtkMRMLScalarVolumeNode")
			if firstVolumeNode:
				self._parameterNode.inputVolume = firstVolumeNode

	def setParameterNode(self, inputParameterNode: Optional[LeFortPSIWorkflowModuleParameterNode]) -> None:
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
		return
		
		if self._parameterNode and self._parameterNode.inputVolume and self._parameterNode.thresholdedVolume:
			self.ui.setupPSIButton.toolTip = _("Compute output volume")
			self.ui.setupPSIButton.enabled = True
		else:
			self.ui.setupPSIButton.toolTip = _("Select input and output volume nodes")
			self.ui.setupPSIButton.enabled = False

	def onSwitchToM2MDistanceButton (self) -> None:
		slicer.util.selectModule("ModelToModelDistance")
	
	# Buttons für die PSI-Auswerung
	def onSetupPSIButton(self) -> None:
		self.logic.process()
		
	def onPrintPSIButton(self) -> None:
		self.logic.printPSIResults()
		
	# Buttons für die Ramus-Auswertung
		
	def onPlanesButton (self) -> None:
		self.logic.createResectionPlanes()
		#slicer.util.selectModule("Markups")
		
	def onSetupRamusButton(self) -> None:
		self.logic.prepareRamus()
		
	def onPrintRamusButton(self) -> None:
		self.logic.printRamusResults()
		
	def onRenameMandibleButton(self) -> None:
		self.logic.renameMandibleModels()
		
	def onRamusM2MDistanceButton (self) -> None:
		self.logic.calculateRamusM2MDistance()
		
#
# LeFortPSIWorkflowModuleLogic
#


class LeFortPSIWorkflowModuleLogic(ScriptedLoadableModuleLogic):
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
		return LeFortPSIWorkflowModuleParameterNode(super().getParameterNode())

	def setDefault3dView(self, viewAxis = 3):
		layoutManager = slicer.app.layoutManager()
		threeDWidget = layoutManager.threeDWidget(0)
		threeDView = threeDWidget.threeDView()
		viewNode = threeDWidget.mrmlViewNode()
		
		viewNode.SetBackgroundColor(1,1,1)
		viewNode.SetBackgroundColor2(1,1,1)
		viewNode.SetBoxVisible(False)
		viewNode.SetAxisLabelsVisible(False)
		#viewNode.SetRenderMode(viewNode.Orthographic)
		
		threeDView.rotateToViewAxis(viewAxis)
		threeDView.resetFocalPoint()

	def cloneModel(self, nodeToClone, clonedName):
		shNode = slicer.vtkMRMLSubjectHierarchyNode.GetSubjectHierarchyNode(slicer.mrmlScene)
		itemIDToClone = shNode.GetItemByDataNode(nodeToClone)
		clonedItemID = slicer.modules.subjecthierarchy.logic().CloneSubjectHierarchyItem(shNode, itemIDToClone)
		clonedNode = shNode.GetItemDataNode(clonedItemID)
		clonedNode.SetName(clonedName)
		return clonedNode

	def convertModelToSegmentation(self, modelNode):
		# Create segmentation
		segmentationNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode")
		segmentationNode.CreateDefaultDisplayNodes() # only needed for display
		segmentationNode.SetName(modelNode.GetName() + " segmentation")
		segmentationNode.GetDisplayNode().SetVisibility(False)

		# Import the model into the segmentation node
		slicer.modules.segmentations.logic().ImportModelToSegmentationNode(modelNode, segmentationNode)
		
		return segmentationNode

	def getDiceAndHausdorff(self, referenceSegmentationNode, compareSegmentationNode):
		referenceSegmentID = referenceSegmentationNode.GetSegmentation().GetNthSegmentID(0)
		compareSegmentID = compareSegmentationNode.GetSegmentation().GetNthSegmentID(0)

		
		# Parameter für den Vergleich festlegen	
		paramNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentComparisonNode")
		paramNode.SetAndObserveReferenceSegmentationNode(referenceSegmentationNode)
		paramNode.SetReferenceSegmentID(referenceSegmentID)
		paramNode.SetAndObserveCompareSegmentationNode(compareSegmentationNode)
		paramNode.SetCompareSegmentID(compareSegmentID)

		# Das SegmentComparison-Modul mit den Parametern füttern
		segmentComparisonLogic = slicer.modules.segmentcomparison.logic()
		
		# Dice und Hausdorff ausrechnen
		segmentComparisonLogic.ComputeDiceStatistics(paramNode)
		segmentComparisonLogic.ComputeHausdorffDistances(paramNode)

		maxHD = paramNode.GetMaximumHausdorffDistanceForBoundaryMm()
		averageHD = paramNode.GetAverageHausdorffDistanceForBoundaryMm()
		dice = paramNode.GetDiceCoefficient()
		
		# print(f"Dice Coefficient: 		{dice}")
		# print(f"Maximum Hausdorff distance:	{maxHD}")
		# print(f"Average Hausdorff distance:	{averageHD}")
		
		return {
			'dice'			: dice,
			'maxHausdorffDistance'	: maxHD,
			'avgHausdorffDistance'	: averageHD
		}

	def buildSegmentOBB(self, segmentationNode, color = None, visible = False):	
		segStatLogic = SegmentStatistics.SegmentStatisticsLogic()
		segStatLogic.getParameterNode().SetParameter("Segmentation", segmentationNode.GetID())
		segStatLogic.getParameterNode().SetParameter("LabelmapSegmentStatisticsPlugin.obb_origin_ras.enabled",str(True))
		segStatLogic.getParameterNode().SetParameter("LabelmapSegmentStatisticsPlugin.obb_diameter_mm.enabled",str(True))
		segStatLogic.getParameterNode().SetParameter("LabelmapSegmentStatisticsPlugin.obb_direction_ras_x.enabled",str(True))
		segStatLogic.getParameterNode().SetParameter("LabelmapSegmentStatisticsPlugin.obb_direction_ras_y.enabled",str(True))
		segStatLogic.getParameterNode().SetParameter("LabelmapSegmentStatisticsPlugin.obb_direction_ras_z.enabled",str(True))
		segStatLogic.computeStatistics()
		stats = segStatLogic.getStatistics()
		
		rois = list()

		for segmentId in stats["SegmentIDs"]:
			# Get bounding box
			obb_origin_ras = np.array(stats[segmentId,"LabelmapSegmentStatisticsPlugin.obb_origin_ras"])
			obb_diameter_mm = np.array(stats[segmentId,"LabelmapSegmentStatisticsPlugin.obb_diameter_mm"])
			obb_direction_ras_x = np.array(stats[segmentId,"LabelmapSegmentStatisticsPlugin.obb_direction_ras_x"])
			obb_direction_ras_y = np.array(stats[segmentId,"LabelmapSegmentStatisticsPlugin.obb_direction_ras_y"])
			obb_direction_ras_z = np.array(stats[segmentId,"LabelmapSegmentStatisticsPlugin.obb_direction_ras_z"])
			
			# Create ROI
			segment = segmentationNode.GetSegmentation().GetSegment(segmentId)
			roi=slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsROINode")
			roi.SetName(segment.GetName() + " OBB")
			roi.SetSize(obb_diameter_mm)

			# Modify the display of the ROI
			roi.GetDisplayNode().SetHandlesInteractive(False)  # do not let the user resize the box
			roi.GetDisplayNode().SetOpacity(0.3)
			roi.GetDisplayNode().SetVisibility(visible)

			if (color != None):
				roi.GetDisplayNode().SetSelectedColor(color)

			# Position and orient ROI using a transform
			obb_center_ras = obb_origin_ras+0.5*(obb_diameter_mm[0] * obb_direction_ras_x + obb_diameter_mm[1] * obb_direction_ras_y + obb_diameter_mm[2] * obb_direction_ras_z)
			boundingBoxToRasTransform = np.row_stack((np.column_stack((obb_direction_ras_x, obb_direction_ras_y, obb_direction_ras_z, obb_center_ras)), (0, 0, 0, 1)))
			boundingBoxToRasTransformMatrix = slicer.util.vtkMatrixFromArray(boundingBoxToRasTransform)
			roi.SetAndObserveObjectToNodeMatrix(boundingBoxToRasTransformMatrix)
			rois.append(roi)
		
		return rois



	def splitMaxillaFromMidface(self, basename):
		# nameLeft = basename + " left"
		# nameRight = basename + " right"

		# Dynamic Modeler konfigurieren
		try:
			modelerNode = slicer.util.getNode('resection cut')
		except Exception as e: # noqa: F841
			modelerNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLDynamicModelerNode")
			modelerNode.SetName('resection cut')
			modelerNode.SetToolName("Plane cut")

		# Ebenen, entlang derer getrennt werden soll festlegen
		try:
			resectionPlaneNode = slicer.util.getNode('resection plane')
			resectionPlaneObliqueNode = slicer.util.getNode('resection plane oblique')
		except Exception as e:
			print("Resektionsebenen nicht ausreichend definiert")
			return

		# zuerst die Ebenenauswahl zurücksetzen (falls das bspw. von Hand gesetzt wurde)
		modelerNode.RemoveNodeReferenceIDs('PlaneCut.InputPlane')
		# Dann die beiden Resektionsebenen festlegen
		modelerNode.AddNodeReferenceID("PlaneCut.InputPlane", resectionPlaneNode.GetID())
		modelerNode.AddNodeReferenceID("PlaneCut.InputPlane", resectionPlaneObliqueNode.GetID())
	
		# Paraneter für die Art, wie das Ausgabemodell erstellt wird
		modelerNode.SetAttribute("CapSurface", "no")
		modelerNode.SetAttribute("OperationType", "Union")

		for name in ['max preop', 'max postop', 'max planned']:
			print(f"Spalte {name} vom Mittelgesicht ab")
			# Knoten des jeweiligen Modells ermitteln
			modelNode = slicer.util.getNode(name)
		
			# Hierarchieknoten des jeweiligen Ausgangsmodells und seines übergeordeneten Ordners ermitteln
			shNode = slicer.vtkMRMLSubjectHierarchyNode.GetSubjectHierarchyNode(slicer.mrmlScene)
			shModelNode = shNode.GetItemByName(modelNode.GetName())
			shParentNode = shNode.GetItemParent(shModelNode)
		
			# Farbe des Ausgangsmodells
			color = modelNode.GetDisplayNode().GetColor()

			childNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLModelNode")
			childNode.SetName(f"{name} cropped")
			childNode.CreateDefaultDisplayNodes()
			childNode.SetDisplayVisibility(True)
			childNode.GetDisplayNode().SetOpacity(1.0)
			childNode.GetDisplayNode().SetColor(color)
			
			# neues Modell in der Hierarchie auf der gleichen Ebene wie das Ausgangsmodell eintragen
			shChildNode = shNode.GetItemByName(f"{name} cropped")
			shNode.SetItemParent(shChildNode, shParentNode)
		
			# zu trennendes Modell als Parameter für Dynamic Modeler setzen
			modelerNode.SetNodeReferenceID('PlaneCut.InputModel', modelNode.GetID())
		
			# bei"Operation type" Union festlegt brauchen wir das "positive model"
			modelerNode.SetNodeReferenceID('PlaneCut.OutputPositiveModel', childNode.GetID())
		
			# nachdem alle Parameter gesetzt wurde kann es ausgeführt werden
			slicer.modules.dynamicmodeler.logic().RunDynamicModelerTool(modelerNode)
		
		# Wenn alles abgetrennt ist können die Ebenen und die Markups ausgeblendet werden
		slicer.util.getNode("landmarks").GetDisplayNode().SetVisibility(0)
		resectionPlaneNode.GetDisplayNode().SetVisibility(0)
		resectionPlaneObliqueNode.GetDisplayNode().SetVisibility(0)

	def alignMaxillaModels(self):
		registeredModel = self.registerSourceModelToTargetModel("max preop cropped", "max planned cropped", "registration preop-planned", "max preop cropped registered to planned")
		clonedModel = self.cloneModel(registeredModel, "max preop cropped registered to postop")
		registeredModel.GetDisplayNode().SetColor(COLOR_PLANNED)
		clonedModel.GetDisplayNode().SetColor(COLOR_POSTOP)

		# Das Postop-Model in die geplante Position bringen
		postopModelInPlannedPosition = self.registerSourceModelToTargetModel("max postop cropped", "max planned cropped", "registration postop-planned", "max postop cropped registered to planned")
		postopModelInPlannedPosition.GetDisplayNode().SetVisibility(False)
	
		# jetzt das oben bereit nochmal geklonte Preop-Modell in die Postop-position zu bringen
		# dafür die Transfprmation postop-planned duplizieren und invertieren und diese danna uf das 
		# duplizierte Präop-Modell anwenden. Im Ergebnis sollte es das preop-Modell drei mal geben, in der 
		# Ausgangsposition, der geplanten Position und der postoperativen Position
		clonedTransform = self.cloneModel(slicer.util.getNode("registration postop-planned"), "registration planned-postop")
		clonedTransform.Inverse()
		clonedModel.SetAndObserveTransformNodeID(clonedTransform.GetID())
		clonedModel.HardenTransform()

	def alignPSIModels(self):
		registeredModel = self.registerSourceModelToTargetModel("lefortpsi planned", "lefortpsi postop", "registration psi planned-postop", "lefortpsi planned registered to postop")
		registeredModel.GetDisplayNode().SetColor(COLOR_POSTOP)

	def registerSourceModelToTargetModel(self, sourceModelName, targetModelName, transformName, registeredModelName = None):
			# Modelle definieren
		sourceModel = slicer.util.getNode(sourceModelName)
		targetModel = slicer.util.getNode(targetModelName)

		if (registeredModelName is None):
			registeredModelName = f"{sourceModelName} registered"

		registeredModel = self.cloneModel(sourceModel, registeredModelName)

		# Überlageurngstransform definieren
		sourceToTargetTransform = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLinearTransformNode")
		sourceToTargetTransform.SetName(transformName)

		# put the transform into a designated folder
		shNode = slicer.mrmlScene.GetSubjectHierarchyNode()
		folderNodeID = shNode.GetItemChildWithName(shNode.GetSceneItemID() ,"Transforms")
		if (folderNodeID == 0):
			folderNodeID = shNode.CreateFolderItem(shNode.GetSceneItemID(), "Transforms")
		shNode.CreateItem(folderNodeID, sourceToTargetTransform)

		# Modullogik für Model Registration laden
		import ModelRegistration
		
		mrLogic = ModelRegistration.ModelRegistrationLogic()
		mrLogic.run(sourceModel, targetModel, sourceToTargetTransform)

		# Registrierungstransform auf das kopierte Objekt anweneden und TRansform härten (das kann man bestimmt abkürzen)
		registeredModel.SetAndObserveTransformNodeID(sourceToTargetTransform.GetID())
		registeredModel.HardenTransform()
		
		registeredModel.GetDisplayNode().SetColor(COLOR_REGISTERED)
		registeredModel.GetDisplayNode().SetVisibility(1)

		return registeredModel

		
	def getRotationFromMatrixTotal(self, transformNode):
		rotMat = slicer.util.arrayFromTransformMatrix(transformNode)
		rotation = scipy.spatial.transform.Rotation.from_matrix(rotMat[:3, :3])
		return np.linalg.norm(rotation.as_rotvec(degrees=True))
	
	def printPSIResults(self):
		return self.printResults(
			'lefortpsi',
			slicer.util.getNode('lefortpsi planned'),
			slicer.util.getNode('lefortpsi planned registered to postop'),
			slicer.util.getNode('lefortpsi planned')
		)

	def printMaxillaResults(self):
		return self.printResults(
			'max',
			slicer.util.getNode('max preop cropped registered to planned'),
			slicer.util.getNode('max preop cropped registered to postop'),
			slicer.util.getNode('max preop')
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
	def printResults(self, prefix, nodePlanned, nodePostop, nodeForFilename):
		resultsTableRow = {}

		# Calaculating dice and Hausdorff is done using segment statistics, so the models need to be
		# converted to segmentations
		segmentationPlanned	= self.convertModelToSegmentation(nodePlanned)
		segmentationPostop	= self.convertModelToSegmentation(nodePostop)
		diceAndHausdorff	= self.getDiceAndHausdorff(segmentationPlanned, segmentationPostop)
		
		resultsTableRow[f'{prefix}_dice_plan_intraop'] = diceAndHausdorff['dice']
		resultsTableRow[f'{prefix}_hausdorff_avg_planned_postop'] = diceAndHausdorff['avgHausdorffDistance']
		resultsTableRow[f'{prefix}_hausdorff_max_planned_postop'] = diceAndHausdorff['maxHausdorffDistance']
			
		# calculate angle bewteen planned and postop position
		transformNode = slicer.util.getNode("registration planned-postop")
		rotMat = slicer.util.arrayFromTransformMatrix(transformNode)
		rotation = scipy.spatial.transform.Rotation.from_matrix(rotMat[:3, :3])
		euler_angles_xyz = rotation.as_euler("xyz", degrees=True)

		resultsTableRow[f'{prefix}_rotation_x'] = euler_angles_xyz[0]
		resultsTableRow[f'{prefix}_rotation_y'] = euler_angles_xyz[1]
		resultsTableRow[f'{prefix}_rotation_z'] = euler_angles_xyz[2]

		# Calculate vector between bounding box centers
		roisPlanned = self.buildSegmentOBB(segmentationPlanned, COLOR_PLANNED, True)
		roisPostop = self.buildSegmentOBB(segmentationPostop, COLOR_POSTOP, True)
		roiPlannedCenter = [0,0,0]
		roisPlanned[0].GetCenter(roiPlannedCenter)
		roiPostopCenter = [0,0,0]
		roisPostop[0].GetCenter(roiPostopCenter)
		vector = np.array(roiPostopCenter) - np.array(roiPlannedCenter)
		distance = np.linalg.norm(vector)

		resultsTableRow[f'{prefix}_distance'] = distance
		resultsTableRow[f'{prefix}_vector_x'] = vector[0]
		resultsTableRow[f'{prefix}_vector_y'] = vector[1]
		resultsTableRow[f'{prefix}_vector_z'] = vector[2]

		# Results of Model-To-Model-Distance
		distanceNode = slicer.util.getNode(f'{prefix} distance planned postop')
		distanceArrayTotal	=	slicer.util.arrayFromModelPointData(distanceNode, "point to point distance signed")
		resultsTableRow[f'{prefix}_m2m_rms'] = np.sqrt(np.mean(np.square(distanceArrayTotal)))
		
		print(*[f"{x[0:16]: ^16}" for x in resultsTableRow.keys()])
		print(*[f"{x: ^16.3f}" for x in resultsTableRow.values()])

		outputPath = os.path.dirname(nodeForFilename.GetStorageNode().GetFileName())
		with open(os.path.join(outputPath, f'output_{prefix}.csv'), 'w') as output:
			writer = csv.writer(output)

			writer.writerow(resultsTableRow.keys())
			writer.writerow(resultsTableRow.values())

		# print(resultsTableRow)
		# print(";".join(map(str, resultsTableRow.values())))
		
		return resultsTableRow
	
	def processPSIs(self):
		print()
		print("--------------------------------")
		print("- LeFort PSI Workflow Automation -")
		print("--------------------------------")
		print(flush=True)
		
		self.setDefault3dView()
		self.splitModelInHalf(slicer.util.getNode('psis postop'), 'psi postop')

		for side in ['left','right']:
			nodePlanned = slicer.util.getNode(f'psi planned {side}')
			nodePostop = slicer.util.getNode(f'psi postop {side}')
			nodePlanned.GetDisplayNode().SetColor(COLOR_PLANNED)
			nodePostop.GetDisplayNode().SetColor(COLOR_POSTOP)
			nodePostop.GetDisplayNode().SetOpacity(0.5)

			print(f"{side}: Registering planned to postop models", flush=True)

			self.registerSourceModelToTargetModel(
				f"psi planned {side}",
				f"psi postop {side}",
				f"registration psi planned-postop {side}"
			)

			print(f"{side}: Converting models (planned and postop) to segmentations", flush=True)
			segmentationPlanned =	self.convertModelToSegmentation(slicer.util.getNode(f'psi planned {side}'))
			segmentationPostop =	self.convertModelToSegmentation(slicer.util.getNode(f'psi postop {side}'))
			
			print(f"{side}: Calculating Dice-Coefficient and Hausdorff-Distances for planned-vs-postop", flush=True)
			diceAndHausdorff = self.getDiceAndHausdorff(segmentationPlanned, segmentationPostop) # noqa: F841
			
			print(flush=True)
		
		#print("Generating models for Model-To-Model-Distance", flush=True)
		#self.createModelsForM2MDistance("distance planned-postop")
		
		print("\nDone!")


	def prepareModels(self):
		self.setDefault3dView(1)

		patterns = {
			'*_Max_pre-op'		:	'max preop',
			'*_Max_post-op'		:	'max postop',
			'*_Max_planned'		:	'max planned',
			'*_Lefort_planned'  :	'lefortpsi planned',
			'*_Lefort_post-op'  :	'lefortpsi postop'
		}

		shNode = slicer.mrmlScene.GetSubjectHierarchyNode()
		folderNodeID = shNode.CreateFolderItem(shNode.GetSceneItemID(), "Models")

		for pattern, newName in patterns.items():
			try:
				
				node = slicer.util.getNode(pattern)		# Node des Modells anhand des Patterns suchen
				oldName = node.GetName()				# dann den alten Namen auslesen...
				node.SetName(newName)					# ... und den neuen Namen setzen

				# dann noch die Farbe des Modells anhand der Color-Map setzen
				node.GetDisplayNode().SetColor(COLOR_MAP_MAXILLA[newName])

				# und das Modell in den Models-Order packen
				shNode.CreateItem(folderNodeID, node)

				print(f"Modell {oldName} umbenannt in {newName}")
			except Exception as e:
				try:
					node = slicer.util.getNode(newName)	# Node des Modells mit dem korreten Namen suchen 

					# wenn es exisitert wurde wahrschienlich die Farbe auch schon gesetzt, aber sicherheitshalber
					# trotzdem nochmal setzen, falls die Umbenennung manuell erfolgt ist
					node.GetDisplayNode().SetColor(COLOR_MAP_MAXILLA[newName])

					print(f"Modell {newName} existiert bereits. Nichts mehr zu tun")
				except Exception as e2:
					print(f"Modell für {newName} (bzw. {pattern} nicht gefunden! Bitte überprüfe, ob die Modelle vorliegen und korrekt benannt sind!")


	def createMarkupsForPlanes(self):
		interactionNode = slicer.app.applicationLogic().GetInteractionNode()
		selectionNode = slicer.app.applicationLogic().GetSelectionNode()
		selectionNode.SetReferenceActivePlaceNodeClassName("vtkMRMLMarkupsFiducialNode")
		landmarksListNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode", "landmarks")

		selectionNode.SetActivePlaceNodeID(landmarksListNode.GetID())
		interactionNode.SetCurrentInteractionMode(interactionNode.Place)

	def createResectionPlanes(self):
		try:
			landmarksListNode = slicer.util.getNode("landmarks")
		except Exception as e:
			print("Landmarkenliste unter dem Namen 'landmarks' nicht gefunden. Bitte überprüfe, ob die Liste vorhanden ist")
			return
		
		if (landmarksListNode.GetNumberOfControlPoints() < 4):
			print("Landmarkenliste enthält nicht genug Landmarkenpunkte (4)")
			return

		landmarksListNode.SetNthControlPointLabel(0, "tmr")
		landmarksListNode.SetNthControlPointLabel(1, "tml")
		landmarksListNode.SetNthControlPointLabel(2, "spa")
		landmarksListNode.SetNthControlPointLabel(3, "nsn")

		resectionPlaneNode = self.createPlaneFromMarkups(landmarksListNode, [0,1,2], "resection plane")
		markupsPositions = slicer.util.arrayFromMarkupsControlPoints(resectionPlaneNode)
		resectionPlaneNode.SetCenter(np.mean(markupsPositions,0))
		resectionPlaneNode.GetDisplayNode().SetSelectedColor(0.0,0.0,1.0)

		resectionPlaneObliqueNode = self.createPlaneFromMarkups(landmarksListNode, [0,1,3], "resection plane oblique")
		markupsPositions = slicer.util.arrayFromMarkupsControlPoints(resectionPlaneObliqueNode)
		resectionPlaneObliqueNode.SetCenter(np.mean(markupsPositions,0))	
		resectionPlaneObliqueNode.GetDisplayNode().SetSelectedColor(0.0,0.0,1.0)

		# put the planes into a designated folder
		shNode = slicer.mrmlScene.GetSubjectHierarchyNode()
		folderNodeID = shNode.GetItemChildWithName(shNode.GetSceneItemID() ,"Planes")
		if (folderNodeID == 0):
			folderNodeID = shNode.CreateFolderItem(shNode.GetSceneItemID(), "Planes")
		shNode.CreateItem(folderNodeID, resectionPlaneNode)
		shNode.CreateItem(folderNodeID, resectionPlaneObliqueNode)

	def createPlaneFromMarkups(self, pointListNode, indices, planeName):
		planeNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsPlaneNode", planeName)
		planeNode.SetPlaneType(0)

		for i in indices:
			planeNode.AddControlPoint(pointListNode.GetNthControlPointPosition(i))

		return planeNode
	
	def calculateMaxillaModelToModelDistance(self):
		self.computeModelToModelDistancePointByPoint(
			"max preop cropped registered to planned",
			"max preop cropped registered to postop",
			"max distance planned postop"
		)

	def calculatePSIModelToModelDistance(self):
		self.computeModelToModelDistancePointByPoint(
			"lefortpsi planned",
			"lefortpsi planned registered to postop",
			"lefortpsi distance planned postop"
		)


	# Uses the closest-point approach of the vtkDistancePolyDataFilter (similar to the ModelToModel-Distane-Module)
	def computeModelToModelDistance(self, sourceModel, registeredModel, outputNodeName):
		print(f"Calculating Model-To-Model-Distance ('Signed closest point') for Models {sourceModel.GetName()} and {registeredModel.GetName()}. Output ist stored in model {outputNodeName}")
		
		# VTK-Filter zur Abstandsmessung erstellen
		distanceFilter = vtk.vtkDistancePolyDataFilter()	
		
		# das Modell in der Originalposition als Ausgangswert setzen
		distanceFilter.SetInputData(0,sourceModel.GetPolyData())
		
		# das Modell in der registrierten Position als Zielwert setzen
		distanceFilter.SetInputData(1,registeredModel.GetPolyData())
		
		# Daten ohne Vorzeichen ermitteln (Vorzeichen haben nur Sinn bei PointToPointDistance)
		distanceFilter.SetSignedDistance(False)
		distanceFilter.ComputeSecondDistanceOff()
		
		# Ermittlung der Distanz zum Zentrum der Zelle deaktivieren
		#distanceFilter.SetComputeCellCenterDistance(True)
		
		# nun die Daten durch aktualisieren des Filters generieren und dem
		# outputNode (Kopie des sourceModel) zuweisen
		distanceFilter.Update()
		distanceMap = distanceFilter.GetOutput()
		
		outputNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLModelNode",outputNodeName)

		outputNode.SetAndObservePolyData(distanceMap)
		
		return [outputNode, distanceMap]
	
	# Uses a custom python implementation of the ModelToModel-Distance approach to
	# the point-by-point distance on similar models
	def computeModelToModelDistancePointByPoint(self, sourceModelName, targetModelName, distanceModelName):
		sourceModel = slicer.util.getNode(sourceModelName)
		targetModel = slicer.util.getNode(targetModelName)
		distanceModel = self.cloneModel(sourceModel, distanceModelName)

		sourceCoords = vtk.util.numpy_support.vtk_to_numpy(sourceModel.GetMesh().GetPoints().GetData())
		targetCoords = vtk.util.numpy_support.vtk_to_numpy(targetModel.GetMesh().GetPoints().GetData())

		# für das Source-Model die Normalen bestimmen
		normalGenerator =  vtk.vtkPolyDataNormals()
		normalGenerator.SetInputDataObject(sourceModel.GetMesh())
		normalGenerator.SetSplitting(False)
		normalGenerator.SetComputeCellNormals(False)
		normalGenerator.SetComputePointNormals(True)
		normalGenerator.Update()
		meshWithNormals = normalGenerator.GetOutput()
		#print(meshWithNormals)
		sourceNormals = vtk.util.numpy_support.vtk_to_numpy(normalGenerator.GetOutput().GetPointData().GetNormals())

		distance_absolute	= [None] * len(sourceCoords)
		distance_signed		= [None] * len(sourceCoords)
		distance_along_x	= [None] * len(sourceCoords)
		distance_along_y	= [None] * len(sourceCoords)
		distance_along_z	= [None] * len(sourceCoords)
		vectors				= [[None,None,None]] * len(sourceCoords)

		for i in range(len(sourceCoords)):
			vec = targetCoords[i] - sourceCoords[i] 
			dotProduct = np.dot(sourceNormals[i], vec)
			
			distance_absolute[i] = np.linalg.norm(vec)
			distance_signed[i] = -distance_absolute[i] if dotProduct >= 0 else distance_absolute[i]
			distance_along_x[i] = vec[0]
			distance_along_y[i] = vec[1]
			distance_along_z[i] = vec[2]
			vectors[i] = vec

		# VTK-Array for the unsigned distance
		vtkArrayDistancesAbsolute = vtk.util.numpy_support.numpy_to_vtk(distance_absolute)
		vtkArrayDistancesAbsolute.SetName("point to point distance absolute")

		# VTK-Array for the signed distance
		vtkArrayDistancesSigned = vtk.util.numpy_support.numpy_to_vtk(distance_signed)
		vtkArrayDistancesSigned.SetName("point to point distance signed")

		# VKT-Arrays for the distances along the cartesian axes
		vtkArrayDistancesAlongX = vtk.util.numpy_support.numpy_to_vtk(distance_along_x)
		vtkArrayDistancesAlongX.SetName("point to point distance along x")
		vtkArrayDistancesAlongY = vtk.util.numpy_support.numpy_to_vtk(distance_along_y)
		vtkArrayDistancesAlongY.SetName("point to point distance along y")
		vtkArrayDistancesAlongZ = vtk.util.numpy_support.numpy_to_vtk(distance_along_z)
		vtkArrayDistancesAlongZ.SetName("point to point distance along z")

		# VTK-Arrays for the point-by-point vectors from source to target
		vtkArrayVectors = vtk.util.numpy_support.numpy_to_vtk(vectors)
		vtkArrayVectors.SetName("point to point vector")

		# add the VTK-arrays to the Model
		distanceModel.GetMesh().GetPointData().AddArray(vtkArrayDistancesAbsolute)
		distanceModel.GetMesh().GetPointData().AddArray(vtkArrayDistancesSigned)
		distanceModel.GetMesh().GetPointData().AddArray(vtkArrayDistancesAlongX)
		distanceModel.GetMesh().GetPointData().AddArray(vtkArrayDistancesAlongY)
		distanceModel.GetMesh().GetPointData().AddArray(vtkArrayDistancesAlongZ)
		distanceModel.GetMesh().GetPointData().AddArray(vtkArrayVectors)

		# display model scalar
		distanceModel.GetDisplayNode().SetVisibility(1)
		distanceModel.GetDisplayNode().SetScalarVisibility(1)
		distanceModel.GetDisplayNode().SetAndObserveColorNodeID("vtkMRMLPETProceduralColorNodePET-Rainbow2")
		distanceModel.GetDisplayNode().SetActiveScalar("point to point distance signed", vtk.vtkAssignAttribute.POINT_DATA)

		# display color legend
		colorLegendDisplayNode = slicer.modules.colors.logic().AddDefaultColorLegendDisplayNode(distanceModel)
		colorLegendDisplayNode.SetVisibility(True)
		colorLegendDisplayNode.SetTitleText("Distance planned - postop")
		colorLegendDisplayNode.GetTitleTextProperty().BoldOn()
		colorLegendDisplayNode.GetTitleTextProperty().SetColor((0,0,0))
		colorLegendDisplayNode.GetTitleTextProperty().SetShadow(False)
		colorLegendDisplayNode.GetTitleTextProperty().SetFontSize(30)
		colorLegendDisplayNode.SetSize(0.05,0.4)
		colorLegendDisplayNode.GetLabelTextProperty().SetShadow(False)
		colorLegendDisplayNode.GetLabelTextProperty().SetColor((0,0,0))

		# p1 = inPolyData1->GetPoint( i ) ;
        # p2 = inPolyData2->GetPoint( i ) ;
        # PointsToVec( p1 , p2 , vec ) ;
        # normal = normalsArray->GetTuple( i ) ;
        # dotProduct = vtkMath::Dot( normal , vec ) ;
        # //Signed and absolute corresponding point distance
        # absoluteDistance = vtkMath::Norm( vec ) ;
        # signedDistanceArray->InsertTuple1( i , ( dotProduct >= 0 ? absoluteDistance : -absoluteDistance ) ) ;
        # distanceArray->InsertTuple1( i , absoluteDistance ) ;
        # distanceVecArray->InsertTuple3( i , vec[ 0 ] , vec[ 1 ] , vec[ 2 ] ) ;
        # //MagDir: projection of the distance vector on the normal
        # signedMagNormDirArray->InsertTuple1( i , dotProduct ) ;
        # magNormDirArray->InsertTuple1( i , ( dotProduct >= 0 ? dotProduct : -dotProduct ) ) ;
        # for( int j = 0 ; j < 3 ; j++ )
        # {
        #     normal[ j ] *= dotProduct ;
        # }
        # magNormDirVecArray->InsertTuple3( i , normal[ 0 ] , normal[ 1 ] , normal[ 2 ] ) ;
        # PointToPointAlongXArray->InsertTuple1(i , vec[ 0 ] );
        # PointToPointAlongYArray->InsertTuple1(i , vec[ 1 ] );
        # PointToPointAlongZArray->InsertTuple1(i , vec[ 2 ] );



		# import vtk
		
		# Punktdaten (Scalar) hinzufügen
		# node.GetMesh().GetPointData().AddArray(VTKARRAY)
		
		# Array zu VTK-Array konvertieren
		# vtk.util.numpy_support.numpy_to_vtk([0,1,2,3])