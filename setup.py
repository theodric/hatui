#!/usr/bin/env python3
"""
Setup script for haTUI - Home Assistant Terminal User Interface
"""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="haTUI",
    version="1.0.0",
    author="Theodric",
    description="A TUI (terminal user interface) for Home Assistant",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/theodric/hatui",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: End Users/Desktop",
        "License :: OSI Approved :: Apache License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Topic :: Home Automation",
        "Topic :: System :: Monitoring",
    ],
    python_requires=">=3.8",
    install_requires=[
        "urwid>=2.1.0",
        "aiohttp>=3.8.0",
        "pyyaml>=6.0",
        "requests>=2.25.0",
    ],
    entry_points={
        "console_scripts": [
            "hatui=hatui.main:main",
        ],
    },
    include_package_data=True,
    zip_safe=False,
) 
