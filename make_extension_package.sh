version=5.10
mkdir slicer_psoi_evaluation_extension
mkdir slicer_psoi_evaluation_extension/lib
mkdir slicer_psoi_evaluation_extension/lib/Slicer-$version
mkdir slicer_psoi_evaluation_extension/share
mkdir slicer_psoi_evaluation_extension/share/Slicer-$version
ln -s ../../../PSOIEvaluation slicer_psoi_evaluation_extension/lib/Slicer-$version/qt-scripted-modules
ln -s ../../../PSOIEvaluation.s4ext slicer_psoi_evaluation_extension/share/Slicer-$version/PSOIEvaluation.s4ext
tar -cvf slicer_psoi_evaluation_extension_$version.tar.gz -h slicer_psoi_evaluation_extension --dereference --gzip
rm -r slicer_psoi_evaluation_extension
