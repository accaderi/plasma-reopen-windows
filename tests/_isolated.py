"""Imported first by every test: state, settings and profiles go to a
temporary folder, so running the tests never writes into the real log,
settings file or profiles."""
import os
import tempfile

_root = tempfile.mkdtemp(prefix="prw-tests-")
for _var in ("XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
    os.environ[_var] = os.path.join(_root, _var.lower())
    os.makedirs(os.environ[_var], exist_ok=True)
# no notifications on the desktop of whoever runs the tests
os.environ["PRW_NO_NOTIFICATIONS"] = "1"
