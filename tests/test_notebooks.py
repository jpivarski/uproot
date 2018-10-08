#!/usr/bin/env python

import unittest
import sys
import os
import subprocess
import papermill as pm


class Test(unittest.TestCase):
    def runTest(self):
        pass

    def test_notebooks(self):
        output_nb = os.path.join(os.getcwd(), 'output.ipynb')
        common_kwargs = {
            'output': str(output_nb),
            'kernel_name': 'python{}'.format(sys.version_info.major)
        }

        subprocess.call('binder/postBuild', shell=True)

        cwd = os.getcwd()
        os.chdir(os.path.join(cwd, 'binder'))

        pm.execute_notebook('tutorial.ipynb', **common_kwargs)

        pm.execute_notebook('version-3-features.ipynb', **common_kwargs)

        os.chdir(cwd)


if __name__ == '__main__':
    unittest.main()