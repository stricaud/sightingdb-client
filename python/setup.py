#!/usr/bin/python
import setuptools
from distutils.core import setup

setup(name="sightingdb",
            version="0.0.4",
            author="Sebastien Tricaud",
            author_email="sebastien@honeynet.org",
            description="Client library for SightingDB",
            url="http://www.github.com/stricaud/sightingdb-client/",
            classifiers=[
                "Programming Language :: Python :: 3",
                "License :: OSI Approved :: MIT License",
                "Operating System :: OS Independent",
            ],
            packages=["sightingdb"])
