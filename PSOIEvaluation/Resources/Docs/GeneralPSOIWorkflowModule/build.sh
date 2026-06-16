pandoc psi_analysis_workflow_DE.md \
	 -o psi_analysis_workflow_DE.html \
	 --template template.html \
	 --include-in-header header.html \
	 --include-before-body navbar.html \
	 --include-after-body ../footer.html \
	 --standalone \
	 --no-highlight \
	 --toc --toc-depth 2 \
	 --mathjax

pandoc psi_analysis_workflow_EN.md \
	 -o psi_analysis_workflow_EN.html \
	 --template template.html \
	 --include-in-header header.html \
	 --include-before-body navbar.html \
	 --include-after-body ../footer.html \
	 --standalone \
	 --no-highlight \
	 --toc --toc-depth 2 \
	 --mathjax
