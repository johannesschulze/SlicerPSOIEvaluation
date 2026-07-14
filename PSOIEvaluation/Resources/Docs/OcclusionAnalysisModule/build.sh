pandoc occlusion_analysis_EN.md \
	 -o occlusion_analysis_EN.html \
	 --template template.html \
	 --include-in-header header.html \
	 --include-before-body navbar.html \
	 --include-after-body ../footer.html \
	 --standalone \
	 --no-highlight \
	 --toc --toc-depth 2 \
	 --mathjax
