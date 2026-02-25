# Slicer PSOI Evaluation extension

## dependencies

ModelToModelDistance
SlicerIGT
SlicerRT

## packing the slicer extension

Execute make_extension_package.sh. Adjust version-Veriable if necessary

## Manually packing into slicer extension

### create folder structure for extension

slicer_psoi_evaluation_extension/
|- lib/
  |- Slicer-5.8/
    |- qt-scripted-modules/ -> references the PSOIEvaluation-directory
|- share/
  |- Slicer-5.8/
    |- PSOIEvaluation.s4ext -> references the PSOI-Evaluation.s4ext in the main project directory
    
### create the archive

	tar -cvf slicer_psoi_evaluation.tar.gz -h slicer_psoi_evaluation_extension --dereference --gzip

