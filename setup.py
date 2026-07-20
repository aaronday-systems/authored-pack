from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.sdist import sdist


class LeanSdist(sdist):
    def make_release_tree(self, base_dir, files):
        super().make_release_tree(base_dir, files)
        generated_metadata = Path(base_dir) / "authored_pack.egg-info"
        if generated_metadata.is_dir():
            shutil.rmtree(generated_metadata)


if __name__ == "__main__":
    setup(cmdclass={"sdist": LeanSdist})
