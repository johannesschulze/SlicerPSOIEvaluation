cmake_minimum_required(VERSION 3.16.3...3.19.7 FATAL_ERROR)

project(PSOIEvaluation)

#-----------------------------------------------------------------------------
# Extension meta-information
set(EXTENSION_HOMEPAGE "https://www.slicer.org/wiki/Documentation/Nightly/Extensions/PSOIEvaluation")
set(EXTENSION_CONTRIBUTORS "Johannes Schulze (Bundeswehrkrankenhaus Ulm)")
set(EXTENSION_DESCRIPTION "Extension for managing the Workflow for evaluating the patient specific osteosythesis implant (PSOI) research at German Military Forces Hospital Ulm and University Hospital Ulm

Proveides the Modules for BSSO-Evaluation, LeFort-Osteotomy-Evaluation and orbital wall fracture PSOI Evaluation")
set(EXTENSION_ICONURL "https://www.example.com/Slicer/Extensions/PSOIEvaluation.png")
set(EXTENSION_SCREENSHOTURLS "https://www.example.com/Slicer/Extensions/PSOIEvaluation/Screenshots/1.png")
set(EXTENSION_DEPENDS "NA") # Specified as a list or "NA" if no dependencies

#-----------------------------------------------------------------------------
# Extension dependencies
find_package(Slicer REQUIRED)
include(${Slicer_USE_FILE})

#-----------------------------------------------------------------------------
# Extension modules
add_subdirectory(PSOIEvaluation)
## NEXT_MODULE

#-----------------------------------------------------------------------------
include(${Slicer_EXTENSION_GENERATE_CONFIG})
include(${Slicer_EXTENSION_CPACK})
