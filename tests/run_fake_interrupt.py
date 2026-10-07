import sys; sys.path.insert(0, ".")
from tests import fake_llm; fake_llm.install_interrupting(int(sys.argv[1]))
from pipeline import cli
cli.main(sys.argv[2:])
