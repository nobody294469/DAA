"""`python -m clindoc.web [--port 8000 ...]` == `clindoc serve ...`."""
import sys

from clindoc.cli import main

sys.exit(main(["serve", *sys.argv[1:]]))
