#!/usr/bin/python
import setuptools
from distutils.core import setup
import os
import textwrap

current_dir = os.path.abspath(os.path.dirname(__file__))

about = {}
with open(os.path.join(current_dir, "sightingdb", "__version__.py"), "r") as fp:
          exec(fp.read(), about)

setup(name="sightingdb",
      version=about["__version__"],
      author=about["__author__"],
      author_email=about["__author_email__"],
      description=about["__description__"],
      long_description=textwrap.dedent(open("README.rst", "r").read()),
      long_description_content_type="text/x-rst",
      maintainer=about["__maintainer__"],
      maintainer_email=about["__maintainer_email__"],
      url=about["__url__"],
      license=about["__license__"],
      classifiers=[
          "Programming Language :: Python :: 3",
          "License :: OSI Approved :: MIT License",
          "Operating System :: OS Independent",
      ],
      install_requires=[
                "requests",
      ],
      packages=["sightingdb"])
