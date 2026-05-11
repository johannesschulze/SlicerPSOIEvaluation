"""
functions.py

Shared python module for common definitions (colors, materials) and
functions, that are used across multiple 3D slicer modules 
"""

from __main__ import vtk, slicer

import numpy as np
import scipy
import collections
import sys

import qt

import SegmentStatistics

COLOR_PREOP		= (0.5, 0.5, 0.5)	    # Grey
COLOR_PLANNED		= (0.4, 1.0, 0.4)	# bright green
COLOR_POSTOP		= (0.8, 0.5, 0.7)	# purple
COLOR_REGISTERED	= (0.3,0.4,1.0)		# light blue
COLOR_DISTANCE		= (1, 0.8, 0.6)     # pink

MATERIAL_METAL = {
    'ambient': 0.2,
    'diffuse': 0.6,
    'specular':1.0,
    'power': 19.5
    # 'metallic': 1.0,
    # 'roughness': 1
}

MATERIAL_BONE = {
    'ambient': 0.1,
    'diffuse': 1.0,
    'specular':0.0,
    'power': 1.0
}

def removeNodesFromScene(nodeList):
    """
    Removes a list of nodes from the scene.
    
    :param nodeList: List of nodes
    """
    for node in nodeList:
        slicer.mrmlScene.RemoveNode(node)

def removeNodesFromSceneByName(nodeNames):
    """
    Removes a list of nodes, identified by name, from the scene.
    
    :param nodeNames: List of node names. If single stirng is passed as an argument, only this node is removed
    """
    if (isinstance(nodeNames, str)):
        nodeNames = [nodeNames]
    
    for nodeName in nodeNames:
        try:
            slicer.mrmlScene.RemoveNode(slicer.util.getNode(nodeName))
        except Exception as e:
            None

def applyMaterialToModelNode(modelNode, material, color = None):
    """
    Applies a predevined material to a model node
    
    :param modelNode: Model node as provided by slicer.getNode()
    :param material: dictionary of the material properties (ambient, diffuse, specular, power)
    :param color: optional, color to thange the model to, provided as a RGB vector
    """
    displayNode = modelNode.GetDisplayNode()

    displayNode.SetAmbient(material['ambient'])
    displayNode.SetDiffuse(material['diffuse'])
    displayNode.SetSpecular(material['specular'])
    displayNode.SetPower(material['power'])

    if (color != None):
        displayNode.SetColor(color)

def arrayFromVTKVector3D(vec):
    """
    Converts a vtkVector3D to a simple array

    I'm shure this function is already implemented somewhere in numpy_support or slicer.util, just can't bother to search for it
    
    :param vec: vtkVectror3D
    """
    result = [0.0]*3
    result[0] = vec.GetX()
    result[1] = vec.GetY()
    result[2] = vec.GetZ()
    return result

def getNodeBoundsCOG(node):
    """
    determine the center of gravity of a node determining the center of the nodes bounding box
    
    :param node: node
    """
    bounds = [0.0] * 6
    node.GetRASBounds(bounds)
    return (((bounds[0]+bounds[1])/2,(bounds[2]+bounds[3])/2,(bounds[4]+bounds[5])/2))

def centerTransformToNode(transformNode, targetNode):
    """
    sets the Center of Transformation for a transform node to the
    center of the bounding box of the target node, making manual
    transformation much easier
    
    :param transformNode: Transform node
    :param targetNode: Target node, to wich the CEnter of Transformation should be aligned
    """
    transformNode.SetCenterOfTransformation(getNodeBoundsCOG(targetNode))

def getDiceAndHausdorff(referenceSegmentationNode, compareSegmentationNode):
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

def convertModelToSegmentation(modelNode):
    # Create segmentation
    segmentationNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode")
    segmentationNode.CreateDefaultDisplayNodes() # only needed for display
    segmentationNode.SetName(modelNode.GetName() + " segmentation")
    segmentationNode.GetDisplayNode().SetVisibility(False)

    # Import the model into the segmentation node
    slicer.modules.segmentations.logic().ImportModelToSegmentationNode(modelNode, segmentationNode)
    
    return segmentationNode

def setDefault3dView(viewAxis = 3):
    layoutManager = slicer.app.layoutManager()
    threeDWidget = layoutManager.threeDWidget(0)
    threeDView = threeDWidget.threeDView()
    viewNode = threeDWidget.mrmlViewNode()
    
    viewNode.SetBackgroundColor(1,1,1)
    viewNode.SetBackgroundColor2(1,1,1)
    viewNode.SetBoxVisible(False)
    viewNode.SetAxisLabelsVisible(False)
    viewNode.SetRenderMode(viewNode.Orthographic)
    
    threeDView.rotateToViewAxis(viewAxis)
    threeDView.resetFocalPoint()
    threeDView.resetCamera()

    camera = slicer.modules.cameras.logic().GetViewActiveCameraNode(threeDView.mrmlViewNode())
    camera.SetParallelProjection(True)
    camera.SetParallelScale(100)

def alignNodesByCenterOfGravity(sourceNode, targetNode):
    """
    rougly aligns two nodes by the centers of their bounding boxes
    
    :param sourceNode: node to be moved
    :param targetNode: target node
    """
    sourceToTargetTransform = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLinearTransformNode")
    sourceNode.SetAndObserveTransformNodeID(sourceToTargetTransform.GetID())
    centerTransformToNode(sourceToTargetTransform, sourceNode)

    sourceCOG = getNodeBoundsCOG(sourceNode)
    targetCOG = getNodeBoundsCOG(targetNode)

    vec = (targetCOG[0]-sourceCOG[0], targetCOG[1]-sourceCOG[1], targetCOG[2]-sourceCOG[2])

    transformMatrix = slicer.util.vtkMatrixFromArray(
        np.row_stack((
            (1.0,0.0,0.0,vec[0]),
            (0.0,1.0,0.0,vec[1]),
            (0.0,0.0,1.0,vec[2]),
            (0.0,0.0,0.0,1.0)
        ))
    )

    sourceToTargetTransform.SetMatrixTransformToParent(transformMatrix)

    return(sourceToTargetTransform)

def getRotationFromMatrixTotal(transformNode):
    rotMat = slicer.util.arrayFromTransformMatrix(transformNode)
    rotation = scipy.spatial.transform.Rotation.from_matrix(rotMat[:3, :3])
    return np.linalg.norm(rotation.as_rotvec(degrees=True))

# Uses a custom python implementation of the ModelToModel-Distance approach to
# the point-by-point distance on similar models
def computeModelToModelDistancePointByPoint(sourceModel, targetModel, distanceModelName):
    distanceModel = cloneModel(sourceModel, distanceModelName)

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
    #distanceModel.GetDisplayNode().SetAndObserveColorNodeID('vtkMRMLPETProceduralColorNodePET-Rainbow2')
    #distanceModel.GetDisplayNode().SetAndObserveColorNodeID('vtkMRMLColorTableNodeFileColdToHotRainbow.txt')
    distanceModel.GetDisplayNode().SetAndObserveColorNodeID('vtkMRMLColorTableNodeFileMagma.txt')
    #distanceModel.GetDisplayNode().SetAndObserveColorNodeID('vtkMRMLColorTableNodeFilePlasma.txt')

    distanceModel.GetDisplayNode().SetActiveScalar("point to point distance absolute", vtk.vtkAssignAttribute.POINT_DATA)

    # display color legend
    colorLegendDisplayNode = slicer.modules.colors.logic().AddDefaultColorLegendDisplayNode(distanceModel)
    colorLegendDisplayNode.SetVisibility(True)
    colorLegendDisplayNode.SetTitleText("Distance planned - postop")
    colorLegendDisplayNode.GetTitleTextProperty().BoldOn()
    colorLegendDisplayNode.GetTitleTextProperty().SetColor((0,0,0))
    colorLegendDisplayNode.GetTitleTextProperty().SetShadow(False)
    colorLegendDisplayNode.GetTitleTextProperty().SetFontSize(30)
    colorLegendDisplayNode.SetSize(0.1,1.0)
    colorLegendDisplayNode.SetPosition(1,1)
    colorLegendDisplayNode.GetLabelTextProperty().SetShadow(False)
    colorLegendDisplayNode.GetLabelTextProperty().SetColor((0,0,0))

    return distanceModel

def openNew3DWindow(width=500, height=500, layoutName = "New3DView", layoutLabel = "T3", layoutColor=[1,1,1]):
    # layout name is used to create and identify the underlying view node and  should be set to a value that is not used in any of the layouts owned by the layout manager
    # ownerNode manages this view instead of the layout manager (it can be any node in the scene)
    viewOwnerNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLScriptedModuleNode")

    # Create MRML node
    viewLogic = slicer.vtkMRMLViewLogic()
    viewLogic.SetMRMLScene(slicer.mrmlScene)
    viewNode = viewLogic.AddViewNode(layoutName)
    viewNode.SetLayoutLabel(layoutLabel)
    viewNode.SetLayoutColor(layoutColor)
    viewNode.SetAndObserveParentLayoutNodeID(viewOwnerNode.GetID())

    # Create widget
    viewWidget = slicer.qMRMLThreeDWidget()
    viewWidget.setMRMLScene(slicer.mrmlScene)
    viewWidget.setMRMLViewNode(viewNode)
    viewWidget.size = qt.QSize(1000,1000)
    viewWidget.show()

    return viewWidget

# Uses the closest-point approach of the vtkDistancePolyDataFilter (similar to the ModelToModel-Distane-Module)
def computeModelToModelDistance(sourceModel, registeredModel, outputNodeName):
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

def showNodes(nodes):
    for node in nodes:
        node.GetDisplayNode().SetVisibility(True)

def hideNodes(nodes):
    for node in nodes:
        node.GetDisplayNode().SetVisibility(False)

def hideNodesByName(nodeNames):
    for nodeName in nodeNames:
        try:
            slicer.util.getNode(nodeName).SetDisplayVisibility(False)
        except Exception as e:
            None

def hideAllDisplayNodes():
    for node in slicer.util.getNodesByClass("vtkMRMLDisplayNode"):
        node.SetVisibility(False)

def showAllDisplayNodes():
    for node in slicer.util.getNodesByClass("vtkMRMLDisplayNode"):
        node.SetVisibility(True)

def hideAllVolumeRenderingNodes() -> None:
    """
    Hide all Volume Renderings in the scene
    """
    for node in slicer.util.getNodesByClass("vtkMRMLVolumeRenderingDisplayNode"):
        node.SetVisibility3D(False)

def showVolumeRendering(
        volumeNode,
        preset: str = "CT-Bone",
        hideSoftTissue: bool = False,
        thresholds: tuple = (300,500)):
    """
    activate volume rendering for node

    :param volumeNode: node to be rendered as a volume.
    :param preset: 3D-Slicer Volume Rendering Preset. Defaults to "CT-Bone"
    :param hideSoftTissue: Adapt the gradient transfer function to only show bone
    :param thresholds: Threshold to be used for setting up te gradient transfer function

    :returns Display Node of the Volume Rendering
    """
    volRenLogic = slicer.modules.volumerendering.logic()
    displayNode = volRenLogic.CreateDefaultVolumeRenderingNodes(volumeNode)
    scalarRange = volumeNode.GetImageData().GetScalarRange()

    displayNode.GetVolumePropertyNode().Copy(volRenLogic.GetPresetByName(preset))

    if (hideSoftTissue):
        # Set up gradient vs opacity transfer function
        gradientOpacityTransferFunction = displayNode.GetVolumePropertyNode().GetVolumeProperty().GetScalarOpacity()
        gradientOpacityTransferFunction.RemoveAllPoints()
        gradientOpacityTransferFunction.AddPoint(thresholds[0],0.0)
        gradientOpacityTransferFunction.AddPoint(thresholds[1],1.0)

    # Show volume rendering
    displayNode.SetVisibility(True)

    return displayNode

def alignCameraToBoundingBox(boxNode, axis = 0, viewWidget = None):
    
    """
    Aligns the camera along one of the axes of the provided bounding box

    
    :param boxNode: vtkMRMLMarkupsROINode
    :param axis: axis that is used in vtkMRMLMarkupsROINode.GetAxis
    :param camera: camera node, defaults to slicer.util.getNode("Camera")
    """

    if (viewWidget == None):
        viewWidget = slicer.app.layoutManager().threeDWidget(0)

    viewNode = viewWidget.mrmlViewNode()
    camera = slicer.modules.cameras.logic().GetViewActiveCameraNode(viewNode)

    # get the center of the bounding box
    obbCenter = arrayFromVTKVector3D(boxNode.GetCenter())
    obbAxis = [0.0]*3

    # get the desired axis of the bounding box
    boxNode.GetAxis(axis,obbAxis)

    # determine the camera position by projecting 100 mm along the axis from the bounding box center
    if (axis==2):
        obbAxisPoint = np.array(obbCenter) - (np.array(obbAxis)*100)
    else:
        obbAxisPoint = np.array(obbCenter) + (np.array(obbAxis)*100)

    # set focal point and camera position
    slicer.app.layoutManager().threeDWidget(0).threeDView().resetFocalPoint()
    camera.GetCamera().SetRoll(0)
    camera.SetFocalPoint(obbCenter)
    camera.SetPosition(obbAxisPoint)
    camera.ResetClippingRange()
    camera.GetCamera().SetRoll(0)

    """
    determine the difference in rotation (roll) between camera and bounding box.
    This is done by calculating the angle between the upwards vector of the camera
    view and one of the other bounding box axes
    """ 
    obb2ndAxis = [0.0]*3
    boxNode.GetAxis((axis+2)%3, obb2ndAxis)
    viewUpAxis = camera.GetCamera().GetViewUp()

    if (axis==2):
        rollAngle = vtk.vtkMath.DegreesFromRadians(vtk.vtkMath.AngleBetweenVectors(obb2ndAxis, viewUpAxis))-90
    else:
        rollAngle = vtk.vtkMath.DegreesFromRadians(vtk.vtkMath.AngleBetweenVectors(np.array(obb2ndAxis), viewUpAxis))

    print(viewUpAxis)

    camera.GetCamera().SetRoll(rollAngle)

def createLineFromPointCoordinates(pointList, lineName):
    if len(pointList) > 2:
        print("point list exceeds maximum number of points (2)", file=sys.stderr)
        return None
    
    lineNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsLineNode", lineName)
    lineNode.AddControlPoint(pointList[0])
    lineNode.AddControlPoint(pointList[1])
    return lineNode

def createLinesFromROIAxes(roiNode, length = 30):
    center = roiNode.GetCenter()

    for i in range(3):
        axis = [0.0]*3
        roiNode.GetAxis(i, axis)
        axisPoint = np.array(center) + (np.array(axis)*length)

        lineNode = createLineFromPointCoordinates([center, axisPoint], f"{roiNode.GetName()} axis {i}")
        lineNode.GetMeasurement("length").SetEnabled(False)


  

def createPlaneFromMarkups(pointListNode, indices, planeName):
    """
    Creates a Plane from a set of points from a point list node.
    
    :param pointListNode: Point List Node
    :param indices: Indices of the points from pointListNode that are to be used for generating the plane
    :param planeName: Name of the generated Plane(node)
    """
    planeNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsPlaneNode", planeName)
    planeNode.SetPlaneType(0)

    if indices == None:
        """"""
        indices = list(range(0, pointListNode.GetNumberOfControlPoints()))

    for i in indices:
        planeNode.AddControlPoint(pointListNode.GetNthControlPointPosition(i))

    return planeNode

def cloneModel(nodeToClone, clonedName):
    shNode = slicer.vtkMRMLSubjectHierarchyNode.GetSubjectHierarchyNode(slicer.mrmlScene)
    itemIDToClone = shNode.GetItemByDataNode(nodeToClone)
    clonedItemID = slicer.modules.subjecthierarchy.logic().CloneSubjectHierarchyItem(shNode, itemIDToClone)
    clonedNode = shNode.GetItemDataNode(clonedItemID)
    clonedNode.SetName(clonedName)
    return clonedNode

def registerSourceModelToTargetModel(sourceModel, targetModel, transformName, registeredModelName = None, clone = True, color = COLOR_REGISTERED, hardenTransform = True):
        """
        Docstring for registerSourceModelToTargetModel
        
        :param sourceModel: Description
        :param targetModel: Description
        :param transformName: Description
        :param registeredModelName: Description
        """
        if (registeredModelName is None):
            registeredModelName = f"{sourceModel.GetName()} registered"

        if (clone):
            registeredModel = cloneModel(sourceModel, registeredModelName)
        else:
            registeredModel = sourceModel

        # Überlageurngstransform definieren
        sourceToTargetTransform = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLinearTransformNode")
        sourceToTargetTransform.SetName(transformName)

        # Modullogik für Model Registration laden
        import ModelRegistration
        
        mrLogic = ModelRegistration.ModelRegistrationLogic()
        mrLogic.run(sourceModel, targetModel, sourceToTargetTransform)

        # Registrierungstransform auf das kopierte Objekt anweneden und TRansform härten (das kann man bestimmt abkürzen)
        registeredModel.SetAndObserveTransformNodeID(sourceToTargetTransform.GetID())

        if (hardenTransform):
            registeredModel.HardenTransform()
        
        registeredModel.GetDisplayNode().SetColor(color)
        registeredModel.GetDisplayNode().SetVisibility(1)

        # return a tuble of the registered model an the mean distance between source and target
        return registeredModel, mrLogic.ComputeMeanDistance(sourceModel, targetModel, sourceToTargetTransform)

def builROIfromNodeBounds(node, color = None, expansion = 0, visible = False):
    bounds = [0.0]*6
    node.GetBounds(bounds)

    roi = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsROINode")
    roi.SetName(node.GetName() + " Bounding Box")

    roi.AddControlPoint([bounds[0]-expansion,bounds[2]-expansion,bounds[4]-expansion])
    roi.AddControlPoint([bounds[1]+expansion,bounds[3]+expansion,bounds[5]+expansion])

    return roi

def buildSegmentOBB(segmentationNode, color = None, visible = False):	
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