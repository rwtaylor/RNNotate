"""
Setup script for teamRNN
"""

try:
	from setuptools import setup
except:
	from distutils.core import setup

setup(name = "teamRNN",
	version = "0.0.1",
	author = "Greg Zynda",
	author_email="gzynda@tacc.utexas.edu",
	license="BSD-3",
	description="A tool that estimates sequence complexing by counting distinct k-mers in sliding windows",
	install_requires=["pysam"],
	tests_require=["pysam"],
	packages = ["teamRNN"],
	#scripts = ["bin/komplexity"],
	test_suite = "tests")
