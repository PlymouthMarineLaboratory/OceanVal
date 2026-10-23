from setuptools import Command, find_packages, setup
import sys

DESCRIPTION = "A Python package for ocean model validation and analysis"
LONG_DESCRIPTION = """

**oceanVal** is a Python package designed to automate the process of validating ocean models against observational datasets. It provides a suite of tools to facilitate the comparison of model outputs with various observational data sources, enabling researchers to assess model performance effectively. 

Core abilities of oceanVal include:

  - Matching model output variables to observational datasets 
  - Assesing spatial and temporal performance of ocean models 
  - Assessing model skill using a variety of statistical metrics 
  - Asssessing extent of model biases 
  - Generating comprehensive validation reports in html format


"""


PROJECT_URLS = {
    "Bug Tracker": "https://github.com/pmlmodelling/oceanVal/issues",
    "Source Code": "https://github.com/pmlmodelling/oceanVal",
}

extras_require: dict() = dict()


with open("requirements.txt") as f:
    REQUIREMENTS = [i.strip() for i in f if i.strip()]

setup(name='oceanval',
      version='0.6.1',
      description=DESCRIPTION,
      long_description=LONG_DESCRIPTION,
      long_description_content_type="text/markdown",
      python_requires='>=3.10',
      classifiers=[
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        "Operating System :: POSIX :: Linux",
        "Operating System :: MacOS",
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Programming Language :: Python :: 3.13',
    ],

      project_urls=PROJECT_URLS,
      url = "https://github.com/pmlmodelling/oceanVal",
      author='Robert Wilson',
      maintainer='Robert Wilson',
      author_email='rwi@pml.ac.uk',
      include_package_data=True,
      package_data={
      'oceanval': ['data/*'] },

      packages = ["oceanval"],
      # both spellings open the OceanVal window (oceanval/app.py)
      entry_points = {
          "console_scripts": [
              "oceanval = oceanval.app:main",
              # macOS and Windows ignore the case of file names, so there the two
              # are one file, which pip would fail to replace on an upgrade
              *([] if sys.platform in ("darwin", "win32") else ["OceanVal = oceanval.app:main"]),
          ],
      },
      install_requires = REQUIREMENTS,
      extras_require = extras_require,
      zip_safe=False)




