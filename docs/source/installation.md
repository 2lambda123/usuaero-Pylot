# Installation

## Getting Python

If you do nothave Python installed on your machine, it can be downloaded from [https://www.anaconda.com/distribution/](https://www.anaconda.com/distribution/). This is the Anaconda distribution, which we highly recommend (no we are not affiliated with nor do we receive financial support from Anaconda).

## Getting the Source Code

You can either download the source as a ZIP file and extract the contents, or clone the Pylot repository using Git. If your system does not already have a version of Git installed, you will not be able to use this second option unless you first download and install Git. If you are unsure, you can check by typing `git --version` into a command prompt.

### Downloading source as a ZIP file

1. Open a web browser and navigate to [https://github.com/usuaero/Pylot](https://github.com/usuaero/Pylot)
2. Make sure the Branch is set to `Master`
3. Click the `Clone or download` button
4. Select `Download ZIP`
5. Extract the downloaded ZIP file to a local directory on your machine

### Cloning the Github repository

1. From the command prompt navigate to the directory where MachUp will be installed. Note: git will automatically create a folder within this directory called Pylot. Keep this in mind if you do not want multiple nested folders called Pylot.
2. Execute

    $ git clone https://github.com/usuaero/Pylot

We recommend cloning from the repository, as this will allow you to most easily download and install periodic updates. Updates can be installed using the following command

    $ git pull

## Installing

Once you have the source code downloaded, navigate to the root (Pylot/) directory and execute

    $ pip install .

Please note that any time you update the source code (e.g. after executing a git pull), Pylot will need to be reinstalled by executing the above command.

## Testing the Installation

Once the installation is complete, run

    $ py.test test/

to verify Pylot is working properly on your machine. Some warnings and depreciation notices are normal.