import sys; sys.path.insert(0, ".")
from tests import fake_llm; fake_llm.install()
from pipeline import cli
cli.main(sys.argv[1:])
