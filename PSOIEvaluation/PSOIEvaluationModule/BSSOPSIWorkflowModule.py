import os
from typing import Annotated, Optional

import numpy as np
import slicer
import scipy
import vtk
# from slicer import scipy
# from slicer import vtk
import SegmentStatistics

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
	'mandible preop' :		COLOR_PREOP,
	'mandible planned' :	COLOR_PLANNED,
	'mandible postop' :		COLOR_POSTOP
}

#
# BSSOPSIWorkflowModule
#


class BSSOPSIWorkflowModule(ScriptedLoadableModule):
	"""Uses ScriptedLoadableModule base class, available at:
	https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
	"""

	def __init__(self, parent):
		ScriptedLoadableModule.__init__(self, parent)
		self.parent.title = _("BSSOPSIWorkflowModule")  # TODO: make this more human readable by adding spaces
		# TODO: set categories (folders where the module shows up in the module selector)
		self.parent.categories = [translate("qSlicerAbstractCoreModule", "Ulm")]
		self.parent.dependencies = ['SegmentStatistics','ModelRegistration']  # TODO: add here list of module names that this module requires
		self.parent.contributors = ["Johannes Schulze (Bundeswehrkrankenhaus Ulm)"]  # TODO: replace with "Firstname Lastname (Organization)"
		# TODO: update with short description of the module and a link to online module documentation
		# _() function marks text as translatable to other languages
		self.parent.helpText = _("""
This is a module for the automation of the proceses related to the BSSO-PSI research at the German Armed Military Forces Hospital in Ulm, Germany.
""")
		# TODO: replace with organization, grant and thanks
		self.parent.acknowledgementText = _("""
This file was originally developed by Johannes Schulze (Bundeswehrkrankenhaus Ulm) without any funding or grants.
""")

		# Additional initialization step after application startup is complete
		# slicer.app.connect("startupCompleted()", registerSampleData)


#
# BSSOPSIWorkflowModuleParameterNode
#


@parameterNodeWrapper
class BSSOPSIWorkflowModuleParameterNode:
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
# BSSOPSIWorkflowModuleWidget
#


class BSSOPSIWorkflowModuleWidget(ScriptedLoadableModuleWidget, VTKObservationMixin):
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
		uiWidget = slicer.util.loadUI(self.resourcePath("UI/BSSOPSIWorkflowModule.ui"))
		self.layout.addWidget(uiWidget)
		self.ui = slicer.util.childWidgetVariables(uiWidget)

		# Set scene in MRML widgets. Make sure that in Qt designer the top-level qMRMLWidget's
		# "mrmlSceneChanged(vtkMRMLScene*)" signal in is connected to each MRML widget's.
		# "setMRMLScene(vtkMRMLScene*)" slot.
		uiWidget.setMRMLScene(slicer.mrmlScene)

		# Create logic class. Logic implements all computations that should be possible to run
		# in batch mode, without a graphical user interface.
		self.logic = BSSOPSIWorkflowModuleLogic()

		# Connections

		# These connections ensure that we update parameter node when scene is closed
		self.addObserver(slicer.mrmlScene, slicer.mrmlScene.StartCloseEvent, self.onSceneStartClose)
		self.addObserver(slicer.mrmlScene, slicer.mrmlScene.EndCloseEvent, self.onSceneEndClose)

		# Buttons
		self.ui.setupPSIButton.connect("clicked(bool)", self.onSetupPSIButton)
		self.ui.setupRamusButton.connect("clicked(bool)", self.onSetupRamusButton)
		self.ui.switchM2MDistanceModuleButton.connect("clicked(bool)", self.onSwitchToM2MDistanceButton)
		self.ui.switchM2MDistanceModuleButton_2.connect("clicked(bool)", self.onRamusM2MDistanceButton)
		self.ui.printPSIButton.connect("clicked(bool)", self.onPrintPSIButton)
		self.ui.printRamusButton.connect("clicked(bool)", self.onPrintRamusButton)
		self.ui.planesButton.connect("clicked(bool)", self.onPlanesButton)
		self.ui.renameMandibleButton.connect("clicked(bool)", self.onRenameMandibleButton)

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

	def setParameterNode(self, inputParameterNode: Optional[BSSOPSIWorkflowModuleParameterNode]) -> None:
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
		self.ui.setupPSIButton.enabled = True
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
		self.logic.preparePlanes()
		slicer.util.selectModule("Markups")
		
	def onSetupRamusButton(self) -> None:
		self.logic.prepareRamus()
		
	def onPrintRamusButton(self) -> None:
		self.logic.printRamusResults()
		
	def onRenameMandibleButton(self) -> None:
		self.logic.renameMandibleModels()
		
	def onRamusM2MDistanceButton (self) -> None:
		self.logic.calculateRamusM2MDistance()
		
#
# BSSOPSIWorkflowModuleLogic
#


class BSSOPSIWorkflowModuleLogic(ScriptedLoadableModuleLogic):
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
		return BSSOPSIWorkflowModuleParameterNode(super().getParameterNode())

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
		
		print(f"Dice Coefficient: 		{dice}")
		print(f"Maximum Hausdorff distance:	{maxHD}")
		print(f"Average Hausdorff distance:	{averageHD}")
		
		return {
			'dice'			: dice,
			'maxHausdorffDistance'	: maxHD,
			'avgHausdorffDistance'	: averageHD
		}

	def buildSegmentOBB(self, segmentationNode):	
		segStatLogic = SegmentStatistics.SegmentStatisticsLogic()
		segStatLogic.getParameterNode().SetParameter("Segmentation", segmentationNode.GetID())
		segStatLogic.getParameterNode().SetParameter("LabelmapSegmentStatisticsPlugin.obb_origin_ras.enabled",str(True))
		segStatLogic.getParameterNode().SetParameter("LabelmapSegmentStatisticsPlugin.obb_diameter_mm.enabled",str(True))
		segStatLogic.getParameterNode().SetParameter("LabelmapSegmentStatisticsPlugin.obb_direction_ras_x.enabled",str(True))
		segStatLogic.getParameterNode().SetParameter("LabelmapSegmentStatisticsPlugin.obb_direction_ras_y.enabled",str(True))
		segStatLogic.getParameterNode().SetParameter("LabelmapSegmentStatisticsPlugin.obb_direction_ras_z.enabled",str(True))
		segStatLogic.computeStatistics()
		stats = segStatLogic.getStatistics()
		
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
			roi.GetDisplayNode().SetHandlesInteractive(False)  # do not let the user resize the box
			roi.SetSize(obb_diameter_mm)
			# Position and orient ROI using a transform
			obb_center_ras = obb_origin_ras+0.5*(obb_diameter_mm[0] * obb_direction_ras_x + obb_diameter_mm[1] * obb_direction_ras_y + obb_diameter_mm[2] * obb_direction_ras_z)
			boundingBoxToRasTransform = np.row_stack((np.column_stack((obb_direction_ras_x, obb_direction_ras_y, obb_direction_ras_z, obb_center_ras)), (0, 0, 0, 1)))
			boundingBoxToRasTransformMatrix = slicer.util.vtkMatrixFromArray(boundingBoxToRasTransform)
			roi.SetAndObserveObjectToNodeMatrix(boundingBoxToRasTransformMatrix)

		
	def modelDistance(self, sourceModel, registeredModel, outputNodeName):
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

	def createModelsForM2MDistance(self, basename):
		for side in ['left','right']:
			childNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLModelNode")
			childNode.SetName(f"{basename} {side}")
			childNode.CreateDefaultDisplayNodes()
			childNode.SetDisplayVisibility(True)
			childNode.GetDisplayNode().SetOpacity(1.0)
			childNode.GetDisplayNode().SetColor(COLOR_DISTANCE)

		# modelLeft = slicer.util.getNode(f"{basename} left")
		# modelRight = slicer.util.getNode(f"{basename} right")

	def cropRamusByTwoPlanes(self, modelNode, basename, plane1Node, plane2Node):
		newName = basename + " cropped"
		
		# Hierarchieknoten des Ausgangsmodells und seines übergeordeneten Ordners ermitteln
		shNode = slicer.vtkMRMLSubjectHierarchyNode.GetSubjectHierarchyNode(slicer.mrmlScene)
		shModelNode = shNode.GetItemByName(modelNode.GetName())
		shParentNode = shNode.GetItemParent(shModelNode)
		
		# Farbe des Ausgangsmodells
		color = modelNode.GetDisplayNode().GetColor()
		
		childNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLModelNode")
		childNode.SetName(newName)
		childNode.CreateDefaultDisplayNodes()
		childNode.SetDisplayVisibility(True)
		childNode.GetDisplayNode().SetOpacity(1.0)
		childNode.GetDisplayNode().SetColor(color)
			
		# neues Modell in der Hierarchie auf der gleichen Ebene wie das Ausgangsmodell eintragen
		shChildNode = shNode.GetItemByName(basename)
		shNode.SetItemParent(shChildNode, shParentNode)
		
		try:
			modelerNode = slicer.util.getNode('Ramus cut')
		except Exception as e:  # noqa: F841
			modelerNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLDynamicModelerNode")
			modelerNode.SetName('Ramus cut')
			modelerNode.SetToolName("Plane cut")
		
		# zu trennendes Modell als Parameter setzen
		modelerNode.SetNodeReferenceID('PlaneCut.InputModel', modelNode.GetID())
		
		# Ebenen entlang derer getrennt werden soll als Parameter setzen
		modelerNode.RemoveNodeReferenceIDs('PlaneCut.InputPlane')
		modelerNode.SetNodeReferenceID('PlaneCut.InputPlane', plane1Node.GetID())
		modelerNode.AddNodeReferenceID('PlaneCut.InputPlane', plane2Node.GetID())

		# Ausgabemodell setzen
		modelerNode.SetNodeReferenceID('PlaneCut.OutputNegativeModel', childNode.GetID())
		
		# Modelle ohne abgedeckte Schnittflächen erstellen
		modelerNode.SetAttribute("CapSurface","False")

		# nachdem alle Parameter gesetzt wurde kann es ausgeführt werden
		slicer.modules.dynamicmodeler.logic().RunDynamicModelerTool(modelerNode)
		
		return childNode

	def prepareRamus(self):
		nodeNames = ["mandible preop","mandible planned", "mandible postop"]
		
		# sicherstellen, dass alle Modelle vorliegen
		for nodeName in nodeNames:
			try:
				node = slicer.util.getNode(nodeName)
			except Exception as e:  # noqa: F841
				print(f"Modell {nodeName} nicht gefunden! Abbruch!")
				return
		
		if not self.preparePlanes():
			print("Ebenen sind noch nicht angelegt worden. Bitte überprüfen und dann neu starten")
			return
			
		# nun sind wir uns sicher dass alles da ist, also loslegen
		# durch alle Modelle iterierend und jeweils 1. in der Mitte spalten und 2.
		# entlang der oben definierten Ebenen spalten
		for nodeName in nodeNames:
			node = slicer.util.getNode(nodeName)
			
			node.GetDisplayNode().SetColor(COLOR_MAP_MANDIBLE[nodeName])
			
			print(f"Splitting model '{nodeName}' in half")
			splitNodes = self.splitModelInHalf(node, nodeName)
			
			for splitNode in splitNodes:
				splitNodeName = splitNode.GetName()
				print(f"Cropping model '{splitNodeName}'")
				croppedNode = self.cropRamusByTwoPlanes(
					splitNode, 
					splitNodeName,
					slicer.util.getNode("parallel ramus plane"),
					slicer.util.getNode("mandibular notch plane")
				)
				
				print(f"Converting model '{croppedNode.GetName()}' to segmentation", flush=True)
				# segmentation =	self.convertModelToSegmentation(croppedNode)
	
				print(f"Removing Model {splitNodeName}")
				slicer.mrmlScene.RemoveNode(splitNode)
		
		return

	def preparePlanes(self):
		planes = ["parallel ramus plane", "mandibular notch plane"]
		
		for plane in planes:
			try:
				planeNode = slicer.util.getNode(plane) # noqa: F841
				print(f"Ebene {plane} gefunden, wird nicht neu angelegt")
			except Exception as e: # noqa: F841
				modelerNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsPlaneNode")
				modelerNode.SetName(plane)
				return False
		
		return True


	def splitModelInHalf(self, modelNode, basename):
		# nameLeft = basename + " left"
		# nameRight = basename + " right"
		
		# Hierarchieknoten des Ausgangsmodells und seines übergeordeneten Ordners ermitteln
		shNode = slicer.vtkMRMLSubjectHierarchyNode.GetSubjectHierarchyNode(slicer.mrmlScene)
		shModelNode = shNode.GetItemByName(modelNode.GetName())
		shParentNode = shNode.GetItemParent(shModelNode)
		
		# Farbe des Ausgangsmodells
		color = modelNode.GetDisplayNode().GetColor()
		
		for side in ['left','right']:
			childNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLModelNode")
			childNode.SetName(f"{basename} {side}")
			childNode.CreateDefaultDisplayNodes()
			childNode.SetDisplayVisibility(True)
			childNode.GetDisplayNode().SetOpacity(1.0)
			childNode.GetDisplayNode().SetColor(color)
			
			# neues Modell in der Hierarchie auf der gleichen Ebene wie das Ausgangsmodell eintragen
			shChildNode = shNode.GetItemByName(f"{basename} {side}")
			shNode.SetItemParent(shChildNode, shParentNode)

		modelLeft = slicer.util.getNode(f"{basename} left")
		modelRight = slicer.util.getNode(f"{basename} right")

		
		try:
			modelerNode = slicer.util.getNode('Median cut')
		except Exception as e: # noqa: F841
			modelerNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLDynamicModelerNode")
			modelerNode.SetName('Median cut')
			modelerNode.SetToolName("Plane cut")
		
		# zu trennendes Modell als Parameter setzen
		modelerNode.SetNodeReferenceID('PlaneCut.InputModel', modelNode.GetID())
		
		# Ebene entlang derer getrennt werden soll als Parameter setzen (sollte bei Verwendung der Vorlage nicht notwendig sein)
		# modelerNode.SetNodeReferenceID('PlaneCut.InputPlane', slicer.util.getFirstNodeByClassByName("vtkMRMLSliceNode", "Yellow").GetID())
		modelerNode.SetNodeReferenceID('PlaneCut.InputPlane', "vtkMRMLSliceNodeYellow")
		
		
		# Auf das setzen der Ouput-Modelle wird verzichtet, da diese in der Vorlage schon gesetzt sein sollten
		# Falls sie noch nicht gesetzt sind müsste es so lauten:
		modelerNode.SetNodeReferenceID('PlaneCut.OutputNegativeModel', modelRight.GetID())
		modelerNode.SetNodeReferenceID('PlaneCut.OutputPositiveModel', modelLeft.GetID())

		# nachdem alle Parameter gesetzt wurde kann es ausgeführt werden
		slicer.modules.dynamicmodeler.logic().RunDynamicModelerTool(modelerNode)
		
		return [modelRight, modelLeft]


	def registerSourceModelToTargetModel(self, sourceModelName, targetModelName, transformName):
		# Modelle definieren
		sourceModel = slicer.util.getNode(sourceModelName)
		targetModel = slicer.util.getNode(targetModelName)
		registeredModel = self.cloneModel(sourceModel, f"{sourceModelName} registered")

		# Überlageurngstransform definieren
		sourceToTargetTransform = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLinearTransformNode")
		sourceToTargetTransform.SetName(transformName)

		# Modullogik für Model Registration laden
		import ModelRegistration
		
		mrLogic = ModelRegistration.ModelRegistrationLogic()
		mrLogic.run(sourceModel, targetModel, sourceToTargetTransform)

		# Registrierungstransform auf das kopierte Objekt anweneden und TRansform härten (das kann man bestimmt abkürzen)
		registeredModel.SetAndObserveTransformNodeID(sourceToTargetTransform.GetID())
		registeredModel.HardenTransform()
		
		registeredModel.GetDisplayNode().SetColor(COLOR_REGISTERED)
		
	def setDefault3dView(self):
		layoutManager = slicer.app.layoutManager()
		threeDWidget = layoutManager.threeDWidget(0)
		threeDView = threeDWidget.threeDView()
		viewNode = threeDWidget.mrmlViewNode()
		
		viewNode.SetBackgroundColor(1,1,1)
		viewNode.SetBackgroundColor2(1,1,1)
		viewNode.SetBoxVisible(False)
		viewNode.SetAxisLabelsVisible(False)
		#viewNode.SetRenderMode(viewNode.Orthographic)
		
		threeDView.rotateToViewAxis(3)
		threeDView.resetFocalPoint()
		
	def calculateRamusM2MDistance(self):
		for side in ['left','right']:
			self.modelDistance(
				slicer.util.getNode(f'mandible planned {side} cropped'),
				slicer.util.getNode(f'mandible postop {side} cropped'),
				f'distance mandible planned-postop {side}'
			)

	def process(self):
		print()
		print("--------------------------------")
		print("- BSSO PSI Workflow Automation -")
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
		
	def getRotationFromMatrixTotal(self, transformNode):
		rotMat = slicer.util.arrayFromTransformMatrix(transformNode)
		rotation = scipy.spatial.transform.Rotation.from_matrix(rotMat[:3, :3])
		return np.linalg.norm(rotation.as_rotvec(degrees=True))
	
	def printRamusResults(self) -> None:
		resultsTableRow = {}
		
		for side in ['left','right']:
			segmentationPlanned =	slicer.util.getNode(f'mandible planned {side} cropped segmentation')
			segmentationPostop =	slicer.util.getNode(f'mandible postop {side} cropped segmentation')
			diceAndHausdorff = 		self.getDiceAndHausdorff(segmentationPlanned, segmentationPostop)
		
			resultsTableRow[f'ramus_dice_plan_intraop_{side}'] = diceAndHausdorff['dice']
			resultsTableRow[f'ramus_hausdorff_avg_plan_intraop_{side}'] = diceAndHausdorff['avgHausdorffDistance']
			resultsTableRow[f'ramus_hausdorff_max_plan_intraop_{side}'] = diceAndHausdorff['maxHausdorffDistance']
			
			# TODO calculate angle bewteen planned and postop position
			
			# Auswertung der Daten von Model-To-Model-Distance
			distanceNode = slicer.util.getNode(f'distance mandible planned-postop {side}')
			distanceArrayTotal	=	slicer.util.arrayFromModelCellData(distanceNode, 'Distance')
			
			resultsTableRow[f'ramus_m2m_total_{side}'] = np.mean(distanceArrayTotal)
			
		print(resultsTableRow)
		print(";".join(map(str, resultsTableRow.values())))
		
		return
	
	def printPSIResults(self):
		resultsTableRow = {}
		sides = ['left','right']
		
		for side in sides:
			segmentationPlanned =	slicer.util.getNode(f'psi planned {side} segmentation')
			segmentationPostop =	slicer.util.getNode(f'psi postop {side} segmentation')
			diceAndHausdorff = 		self.getDiceAndHausdorff(segmentationPlanned, segmentationPostop)
			
			resultsTableRow[f'dice_plan_intraop_{side}'] = diceAndHausdorff['dice']
			resultsTableRow[f'hausdorff_avg_plan_intraop_{side}'] = diceAndHausdorff['avgHausdorffDistance']
			resultsTableRow[f'hausdorff_max_plan_intraop_{side}'] = diceAndHausdorff['maxHausdorffDistance']
			
			resultsTableRow[f'angle_plan_intraop_{side}'] = self.getRotationFromMatrixTotal(slicer.util.getNode(f"registration psi planned-postop {side}"))
			
			# Auswertung der Daten von Model-To-Model-Distance
			distanceNode = slicer.util.getNode(f'distance planned-postop {side}')
			distanceArrayTotal	=	slicer.util.arrayFromModelPointData(distanceNode, 'AbsolutePointToPointDistance')
			distanceArrayX 		=	slicer.util.arrayFromModelPointData(distanceNode, 'PointToPointAlongX')
			distanceArrayY 		=	slicer.util.arrayFromModelPointData(distanceNode, 'PointToPointAlongY')
			distanceArrayZ 		=	slicer.util.arrayFromModelPointData(distanceNode, 'PointToPointAlongZ')
			
			# für linksseite Modelle müssen die X-Werte gespiegelt werden, damit lateral/medial vergleichbar ist
			if (side == 'left'):
				distanceArrayX *= -1
				
			resultsTableRow[f'm2m_total_{side}'] = np.mean(distanceArrayTotal)
			resultsTableRow[f'm2m_medial_{side}'] = np.mean(distanceArrayX[distanceArrayX>0]) if len(distanceArrayX[distanceArrayX>0]) > 0 else np.nan
			resultsTableRow[f'm2m_lateral_{side}'] = np.mean(distanceArrayX[distanceArrayX<0]) if len(distanceArrayX[distanceArrayX<0]) > 0 else np.nan
			resultsTableRow[f'm2m_superior_{side}'] = np.mean(distanceArrayZ[distanceArrayZ>0]) if len(distanceArrayZ[distanceArrayZ>0]) > 0 else np.nan
			resultsTableRow[f'm2m_inferior_{side}'] = np.mean(distanceArrayZ[distanceArrayZ<0]) if len(distanceArrayZ[distanceArrayZ<0]) > 0 else np.nan
			resultsTableRow[f'm2m_anterior_{side}'] = np.mean(distanceArrayY[distanceArrayY>0]) if len(distanceArrayY[distanceArrayY>0]) > 0 else np.nan
			resultsTableRow[f'm2m_posterior_{side}'] = np.mean(distanceArrayY[distanceArrayY<0]) if len(distanceArrayY[distanceArrayY<0]) > 0 else np.nan
		
		# resultTableNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLTableNode", "Results Table")	
		
		print(resultsTableRow)
		print(";".join(map(str, resultsTableRow.values())))
		
	def renameMandibleModels(self):
		# Liste der umzubenennenden Modelle
		patterns = {
			'*_mandible_pre-op':'mandible preop',
			'*_mandible_post-op':'mandible postop',
			'*_mandible_planned*':'mandible planned'
		}
		
		# durch die Liste der Modellnamen iterieren und prüfen, ob entweder ein Modell vorliegt,
		# dass umbenannt werden muss, oder ob 
		for pattern, newName in patterns.items():
			try:
				node = slicer.util.getNode(pattern)
				oldName = node.GetName()
				node.SetName(newName)
				print(f"Modell {oldName} umbenannt in {newName}")
			except Exception as e: # noqa: F841
				try:
					node = slicer.util.getNode(newName)
					print(f"Modell {newName} existiert bereits. Nichts mehr zu tun")
				except Exception as e2: # noqa: F841
					print(f"Modell für {newName} (bzw. {pattern} nicht gefunden! Bitte überprüfe, ob die Modelle vorliegen und korrekt benannt sind!")
		
		
	def customModelToModelDistancePointByPoint(self):
		return
		# import vtk
		
		# Punktdaten (Scalar) hinzufügen
		# node.GetMesh().GetPointData().AddArray(VTKARRAY)
		
		# Array zu VTK-Array konvertieren
		# vtk.util.numpy_support.numpy_to_vtk([0,1,2,3])